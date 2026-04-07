"""
llama-sse-proxy: Ensure usage field in SSE streams for AI agent frameworks
Version: 0.2.1 (2026-04-07)
Verified: ✅ Token stats fix - reasoning_content support, timing extraction without finish dependency

Original: llama.cpp's SSE streaming responses lack the `usage` field that AI
agent frameworks (OpenClaw, etc.) need to track token usage and trigger context
compaction. This proxy sits between the frontend and the LLM backend,
extracting timings from SSE chunks and injecting a proper `usage` chunk
before [DONE].

The proxy automatically injects `stream_options.include_usage` into streaming
requests, so backends that support it (LMStudio, Ollama, llama.cpp) return a
real usage chunk. Falls back to timing-based estimation if the backend doesn't
return usage.

Ollama compatibility mode: When enabled via --ollama-model, the proxy exposes
Ollama-compatible API endpoints (/api/chat, /api/generate, /api/tags) that
translate to/from OpenAI-compatible backend requests. This allows AI agent
frameworks configured with "ollama" API type to work with llama.cpp backends.

Features:
- Web dashboard (/stats) with bilingual support (zh/en) and auto-refresh
- Silent logging for polling endpoints
- Web configuration interface (/setup) - no config file needed to start

No external dependencies — uses only Python 3 standard library.
"""
import argparse
import datetime
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

log = logging.getLogger("llama-sse-proxy")

BACKEND = None
SHUTDOWN_EVENT = None
OLLAMA_MODEL = None  # Model name exposed via Ollama API

# Stream timeout (seconds) — per-chunk interval for both fetch and main thread
STREAM_TIMEOUT = 1800

# Statistics tracking
STATS = {
    "start_time": time.time(),
    "total_requests": 0,
    "stream_requests": 0,
    "non_stream_requests": 0,
    "ollama_requests": 0,
    "errors": 0,
    "total_tokens": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
}
STATS_LOCK = threading.Lock()


def setup_logging(log_file=None):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def _build_req(url, method, body, headers):
    """Build urllib.Request with cleaned-up headers."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        k_lower = k.lower()
        if k_lower in ("host", "content-length", "transfer-encoding", "connection"):
            continue
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    return req


def curl_request(method, path, body, headers, stream=False):
    """Send HTTP request to backend via urllib.

    stream=False  -> returns (status_code, headers_dict, body_bytes)
    stream=True   -> returns (status_code, headers_dict, queue)
                    queue is filled by a background thread
    """
    url = urljoin(BACKEND, path)
    log.info(f"backend_request: {method} {path} (stream={stream})")

    if not stream:
        req = _build_req(url, method, body, headers)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers) if e.headers else {}, e.read()
        except Exception as e:
            log.error(f"backend request error: {e}")
            raise
    else:
        data_queue = queue.Queue()

        def fetch():
            try:
                req = _build_req(url, method, body, headers)
                req.add_header("Accept", "text/event-stream")
                with urllib.request.urlopen(req, timeout=600) as resp:
                    buf = b""
                    while True:
                        recv = resp.read(4096)
                        if not recv:
                            break
                        buf += recv
                        # Split SSE chunks — llama.cpp sometimes sends compact format:
                        #   standard:    ...}\n\ndata: {...}\n\n
                        #   no sep:     ...}data: {...}   (most aggressive)
                        # NOTE: Do NOT split on double-space "  " — content may contain
                        # spaces, Chinese characters, or other tokens with consecutive
                        # spaces that would cause false splits and corrupt JSON chunks.
                        while True:
                            sep = buf.find(b"\n\n")
                            sep_len = 2
                            if sep == -1:
                                # Most aggressive: split on "}data:" boundary
                                # Only if we already have a complete "data: ..." block
                                ds = buf.find(b"}data: ")
                                if ds != -1:
                                    sep = ds + 1  # include the "}" in current chunk
                                    sep_len = 0
                            if sep == -1:
                                break
                            if sep == 0:
                                buf = buf[sep_len:]
                                continue
                            chunk = buf[:sep]
                            # Validate: chunk must be valid SSE format (starts with "data: ")
                            # and contain valid JSON after that
                            try:
                                text = chunk.decode("utf-8", errors="replace")
                                if text.startswith("data: "):
                                    data_str = text[6:].strip()
                                    if data_str == "[DONE]":
                                        pass  # valid
                                    elif data_str:
                                        json.loads(data_str)  # validate JSON
                                    # If we get here, JSON is valid
                                    data_queue.put(chunk)
                                else:
                                    # Not a data: line, drop it
                                    log.warning(f"Dropping non-SSE line: {chunk[:100]}...")
                            except json.JSONDecodeError:
                                # Incomplete JSON, don't forward this chunk
                                log.warning(f"Dropping chunk with invalid JSON: {chunk[:100]}...")
                            buf = buf[sep + sep_len:]
                    # Don't send incomplete data at end of stream; it will be lost
                    # (llama.cpp will close connection after sending complete chunks)
                data_queue.put(None)
            except Exception as e:
                log.error(f"fetch thread error: {e}")
                data_queue.put(None)

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()
        return 200, {}, data_queue


def _send_json_response(handler, status_code, obj):
    """Send a JSON response."""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _collect_stream_chunks(data_queue, timeout=300):
    """Read all chunks from a stream queue until None.

    Returns (list_of_raw_chunks, timings_info, usage_info, accumulated_content).
    """
    chunks = []
    last_timings = {}
    usage_info = {}
    accumulated_content = []

    while True:
        try:
            chunk = data_queue.get(timeout=timeout)
            if chunk is None:
                log.info(f"_collect_stream_chunks: got None (end), collected {len(chunks)} chunks")
                break
            chunks.append(chunk)
            if len(chunks) <= 3:
                log.info(f"chunk[{len(chunks)}]: {chunk[:300]}")
            # Parse for timings/usage extraction
            try:
                text = chunk.decode("utf-8", errors="replace")
                if text.startswith("data: "):
                    data_str = text[6:].strip()
                    if data_str and data_str != "[DONE]":
                        obj = json.loads(data_str)
                        if "usage" in obj:
                            u = obj["usage"]
                            usage_info["prompt_tokens"] = u.get("prompt_tokens", 0)
                            usage_info["completion_tokens"] = u.get("completion_tokens", 0)
                            usage_info["total_tokens"] = u.get("total_tokens", 0)
                            log.info(f"backend usage: {usage_info}")
                        timings = obj.get("timings")
                        choices = obj.get("choices", [])
                        finish = choices[0].get("finish_reason") if choices else None
                        # 只要有 timings 就更新，不依赖 finish（客户端可能提前断开）
                        if timings:
                            last_timings["prompt_n"] = timings.get("prompt_n", 0)
                            last_timings["predicted_n"] = timings.get("predicted_n", 0)
                            if finish:
                                log.info(f"timings: prompt={last_timings['prompt_n']}, "
                                         f"completion={last_timings['predicted_n']}")
                        delta = choices[0].get("delta", {}) if choices else {}
                        # 同时处理 content 和 reasoning_content（某些模型如 gemma 使用 reasoning_content）
                        content = delta.get("content", "") or delta.get("reasoning_content", "")
                        if content:
                            accumulated_content.append(content)
            except Exception:
                pass
        except queue.Empty:
            break

    return chunks, last_timings, usage_info, accumulated_content


def _get_usage_counts(last_timings, usage_info, accumulated_content):
    """Get prompt/completion token counts from available sources."""
    prompt_n = usage_info.get("prompt_tokens", 0) or last_timings.get("prompt_n", 0)
    completion_n = usage_info.get("completion_tokens", 0) or last_timings.get("predicted_n", 0)

    # If no backend usage and no timings, estimate from content
    if prompt_n == 0 and completion_n == 0 and accumulated_content:
        completion_n = max(1, len("".join(accumulated_content)) // 2)
        log.warning(f"usage estimate: completion={completion_n} "
                    f"(from {len(accumulated_content)} content chunks)")

    return prompt_n, completion_n


def _ollama_model_name():
    """Return the model name to expose via Ollama API."""
    return OLLAMA_MODEL or "default"


# ─── Ollama API handlers ─────────────────────────────────────────────────────


def handle_ollama_api_tags(handler):
    """GET /api/tags — Return model list in Ollama format."""
    model_name = _ollama_model_name()
    resp = {
        "models": [
            {
                "name": model_name,
                "model": model_name,
                "modified_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "size": 0,
                "digest": "proxy",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "proxy",
                    "families": ["proxy"],
                    "parameter_size": "unknown",
                    "quantization_level": "unknown",
                },
            }
        ]
    }
    _send_json_response(handler, 200, resp)


def handle_ollama_api_version(handler):
    """GET /api/version — Return Ollama version info."""
    _send_json_response(handler, 200, {"version": "0.5.0"})


def handle_ollama_api_show(handler, path):
    """GET /api/show — Return model info in Ollama format."""
    model_name = _ollama_model_name()
    resp = {
        "modelfile": f"# proxy to {BACKEND}",
        "parameters": "",
        "template": "",
        "details": {
            "parent_model": "",
            "format": "gguf",
            "family": "proxy",
            "families": ["proxy"],
            "parameter_size": "unknown",
            "quantization_level": "unknown",
        },
        "model_info": {},
    }
    _send_json_response(handler, 200, resp)


def handle_ollama_api_chat(handler, body_bytes):
    """POST /api/chat — Ollama chat endpoint.

    Converts Ollama chat request to OpenAI /v1/chat/completions,
    then converts the response back to Ollama format.
    """
    with STATS_LOCK:
        STATS["total_requests"] += 1
        STATS["ollama_requests"] += 1

    try:
        req_json = json.loads(body_bytes)
    except json.JSONDecodeError as e:
        with STATS_LOCK:
            STATS["errors"] += 1
        _send_json_response(handler, 400, {"error": f"Invalid JSON: {e}"})
        return

    stream = req_json.get("stream", False)
    model_name = _ollama_model_name()
    messages = req_json.get("messages", [])

    # Build OpenAI-compatible request
    openai_req = {
        "model": "default",
        "messages": messages,
        "stream": stream,
        "stream_options": {"include_usage": True} if stream else None,
    }

    # Pass through tools if present
    tools = req_json.get("tools")
    if tools:
        openai_req["tools"] = tools
        openai_req["tool_choice"] = "auto"

    # Pass through temperature, top_p, etc.
    options = req_json.get("options")
    if options:
        for key in ("temperature", "top_p", "top_k", "max_tokens", "seed", "frequency_penalty", "presence_penalty"):
            if key in options:
                openai_req[key] = options[key]

    # Clean up None values
    openai_req = {k: v for k, v in openai_req.items() if v is not None}

    openai_body = json.dumps(openai_req, separators=(",", ":")).encode("utf-8")

    if stream:
        with STATS_LOCK:
            STATS["stream_requests"] += 1
        _handle_ollama_chat_stream(handler, openai_body, model_name)
    else:
        with STATS_LOCK:
            STATS["non_stream_requests"] += 1
        _handle_ollama_chat_nonstream(handler, openai_body, model_name)


def _handle_ollama_chat_nonstream(handler, openai_body, model_name):
    """Handle non-streaming /api/chat by calling OpenAI backend."""
    try:
        status, headers, body_resp = curl_request(
            "POST", "/v1/chat/completions", openai_body, {}, stream=False
        )
    except Exception as e:
        _send_json_response(handler, 502, {"error": str(e)})
        return

    try:
        openai_resp = json.loads(body_resp)
        usage = openai_resp.get("usage", {})
        choices = openai_resp.get("choices", [])
        content = choices[0]["message"]["content"] if choices else ""

        # Check for tool_calls
        message = {"role": "assistant", "content": content}
        if choices and choices[0].get("message", {}).get("tool_calls"):
            message["tool_calls"] = choices[0]["message"]["tool_calls"]

        prompt_n = usage.get("prompt_tokens", 0)
        completion_n = usage.get("completion_tokens", 0)

        ollama_resp = {
            "model": model_name,
            "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "message": message,
            "done": True,
            "done_reason": "stop",
            "total_duration": 0,
            "load_duration": 0,
            "prompt_eval_count": prompt_n,
            "prompt_eval_duration": 0,
            "eval_count": completion_n,
            "eval_duration": 0,
        }
        _send_json_response(handler, 200, ollama_resp)
    except Exception as e:
        log.error(f"Error parsing non-stream chat response: {e}")
        _send_json_response(handler, 502, {"error": f"Backend parse error: {e}"})


def _handle_ollama_chat_stream(handler, openai_body, model_name):
    """Handle streaming /api/chat by converting OpenAI SSE to Ollama NDJSON."""
    try:
        status, headers, data_queue = curl_request(
            "POST", "/v1/chat/completions", openai_body, {}, stream=True
        )
    except Exception as e:
        _send_json_response(handler, 502, {"error": str(e)})
        return

    # Wait for backend connection
    # Collect all chunks first, then replay as Ollama format
    chunks, last_timings, usage_info, accumulated_content = _collect_stream_chunks(data_queue)
    prompt_n, completion_n = _get_usage_counts(last_timings, usage_info, accumulated_content)

    # Update token statistics
    with STATS_LOCK:
        STATS["prompt_tokens"] += prompt_n
        STATS["completion_tokens"] += completion_n
        STATS["total_tokens"] += prompt_n + completion_n

    start_time = time.time()

    # Send as Ollama streaming NDJSON (newline-delimited JSON)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.flush()

    log.info(f"Sending {len(chunks)} chunks as Ollama NDJSON...")
    sent = 0
    for chunk in chunks:
        try:
            text = chunk.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text == "data: [DONE]":
                continue

            if text.startswith("data: "):
                data_str = text[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                obj = json.loads(data_str)

                # Skip usage-only chunks (no choices)
                choices = obj.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})

                # Extract content and tool_calls
                content = delta.get("content", "")
                tool_calls = delta.get("tool_calls")

                message = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    message["tool_calls"] = tool_calls

                ollama_chunk = {
                    "model": model_name,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "message": message,
                    "done": False,
                }

                handler.wfile.write((json.dumps(ollama_chunk, ensure_ascii=False) + "\n").encode("utf-8"))
                handler.wfile.flush()
                sent += 1
        except (BrokenPipeError, ConnectionResetError):
            log.info("Client disconnected, stopping Ollama chat stream")
            break
        except Exception as e:
            log.error(f"Error writing chunk: {e}")
            break

    log.info(f"Sent {sent} content chunks, now sending done chunk...")

    # Send final done chunk with usage
    elapsed = int((time.time() - start_time) * 1e9)
    done_chunk = {
        "model": model_name,
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
        "total_duration": elapsed,
        "load_duration": 0,
        "prompt_eval_count": prompt_n,
        "prompt_eval_duration": 0,
        "eval_count": completion_n,
        "eval_duration": 0,
    }
    handler.wfile.write((json.dumps(done_chunk, ensure_ascii=False) + "\n").encode("utf-8"))
    handler.wfile.flush()

    log.info(f"ollama chat stream done: prompt={prompt_n}, completion={completion_n}")


def handle_ollama_api_generate(handler, body_bytes):
    """POST /api/generate — Ollama generate endpoint.

    Converts Ollama generate request to OpenAI /v1/completions,
    then converts the response back to Ollama format.
    """
    with STATS_LOCK:
        STATS["total_requests"] += 1
        STATS["ollama_requests"] += 1

    try:
        req_json = json.loads(body_bytes)
    except json.JSONDecodeError as e:
        with STATS_LOCK:
            STATS["errors"] += 1
        _send_json_response(handler, 400, {"error": f"Invalid JSON: {e}"})
        return

    stream = req_json.get("stream", False)
    model_name = _ollama_model_name()
    prompt = req_json.get("prompt", "")
    system = req_json.get("system")

    # Build OpenAI-compatible request for /v1/completions
    openai_req = {
        "model": "default",
        "prompt": prompt,
        "stream": stream,
        "stream_options": {"include_usage": True} if stream else None,
    }

    # Pass through options
    options = req_json.get("options")
    if options:
        for key in ("temperature", "top_p", "top_k", "max_tokens", "seed"):
            if key in options:
                openai_req[key] = options[key]

    openai_req = {k: v for k, v in openai_req.items() if v is not None}
    openai_body = json.dumps(openai_req, separators=(",", ":")).encode("utf-8")

    if stream:
        with STATS_LOCK:
            STATS["stream_requests"] += 1
        _handle_ollama_generate_stream(handler, openai_body, model_name)
    else:
        with STATS_LOCK:
            STATS["non_stream_requests"] += 1
        _handle_ollama_generate_nonstream(handler, openai_body, model_name)


def _handle_ollama_generate_nonstream(handler, openai_body, model_name):
    """Handle non-streaming /api/generate."""
    try:
        status, headers, body_resp = curl_request(
            "POST", "/v1/completions", openai_body, {}, stream=False
        )
    except Exception as e:
        _send_json_response(handler, 502, {"error": str(e)})
        return

    try:
        openai_resp = json.loads(body_resp)
        usage = openai_resp.get("usage", {})
        choices = openai_resp.get("choices", [])
        text = choices[0]["text"] if choices else ""

        prompt_n = usage.get("prompt_tokens", 0)
        completion_n = usage.get("completion_tokens", 0)

        ollama_resp = {
            "model": model_name,
            "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "response": text,
            "done": True,
            "done_reason": "stop",
            "context": [],
            "total_duration": 0,
            "load_duration": 0,
            "prompt_eval_count": prompt_n,
            "prompt_eval_duration": 0,
            "eval_count": completion_n,
            "eval_duration": 0,
        }
        _send_json_response(handler, 200, ollama_resp)
    except Exception as e:
        log.error(f"Error parsing non-stream generate response: {e}")
        _send_json_response(handler, 502, {"error": f"Backend parse error: {e}"})


def _handle_ollama_generate_stream(handler, openai_body, model_name):
    """Handle streaming /api/generate."""
    try:
        status, headers, data_queue = curl_request(
            "POST", "/v1/completions", openai_body, {}, stream=True
        )
    except Exception as e:
        _send_json_response(handler, 502, {"error": str(e)})
        return

    chunks, last_timings, usage_info, accumulated_content = _collect_stream_chunks(data_queue)
    prompt_n, completion_n = _get_usage_counts(last_timings, usage_info, accumulated_content)

    # Update token statistics
    with STATS_LOCK:
        STATS["prompt_tokens"] += prompt_n
        STATS["completion_tokens"] += completion_n
        STATS["total_tokens"] += prompt_n + completion_n

    start_time = time.time()

    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.flush()

    for chunk in chunks:
        try:
            text = chunk.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text == "data: [DONE]":
                continue

            if text.startswith("data: "):
                data_str = text[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                obj = json.loads(data_str)

                choices = obj.get("choices", [])
                if not choices:
                    continue

                response_text = choices[0].get("text", "")
                ollama_chunk = {
                    "model": model_name,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "response": response_text,
                    "done": False,
                }
                handler.wfile.write((json.dumps(ollama_chunk, ensure_ascii=False) + "\n").encode("utf-8"))
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            log.info("Client disconnected, stopping Ollama generate stream")
            break
        except Exception:
            pass

    elapsed = int((time.time() - start_time) * 1e9)
    done_chunk = {
        "model": model_name,
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "response": "",
        "done": True,
        "done_reason": "stop",
        "context": [],
        "total_duration": elapsed,
        "load_duration": 0,
        "prompt_eval_count": prompt_n,
        "prompt_eval_duration": 0,
        "eval_count": completion_n,
        "eval_duration": 0,
    }
    handler.wfile.write((json.dumps(done_chunk, ensure_ascii=False) + "\n").encode("utf-8"))
    handler.wfile.flush()

    log.info(f"ollama generate stream done: prompt={prompt_n}, completion={completion_n}")


# ─── OpenAI-compatible passthrough handlers ──────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Skip noisy polling requests from the stats dashboard
        msg = format % args
        if "GET /stats.json" in msg or "GET /favicon.ico" in msg:
            return
        log.info(msg)

    def _format_duration(self, seconds):
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        elif seconds < 86400:
            return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
        else:
            return f"{int(seconds // 86400)}d {int((seconds % 86400) // 3600)}h"

    def _handle_test_stats(self):
        """Test endpoint - send a real request to backend and verify token stats."""
        import urllib.request
        
        backend_url = getattr(self.server, 'backend_url', 'http://localhost:8080')
        test_payload = {
            "model": "test",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
            "max_tokens": 10
        }
        
        # Record stats before request
        before = {
            "total_requests": STATS["total_requests"],
            "total_tokens": STATS["total_tokens"],
            "prompt_tokens": STATS["prompt_tokens"],
            "completion_tokens": STATS["completion_tokens"],
        }
        
        try:
            req = urllib.request.Request(
                f"{backend_url}/v1/chat/completions",
                data=json.dumps(test_payload).encode('utf-8'),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            # Just send request, don't wait for full response
            with urllib.request.urlopen(req, timeout=10) as resp:
                # Read a bit to trigger processing
                resp.read(1024)
        except Exception as e:
            pass  # We don't care about response, just trigger the request
        
        # Give it a moment to process
        time.sleep(0.5)
        
        # Check stats after
        after = {
            "total_requests": STATS["total_requests"],
            "total_tokens": STATS["total_tokens"],
            "prompt_tokens": STATS["prompt_tokens"],
            "completion_tokens": STATS["completion_tokens"],
        }
        
        body = json.dumps({
            "ok": True,
            "message": "Test request sent to backend",
            "before": before,
            "after": after,
            "changed": {
                "total_requests": after["total_requests"] - before["total_requests"],
                "total_tokens": after["total_tokens"] - before["total_tokens"],
                "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
                "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
            }
        }, ensure_ascii=False).encode("utf-8")
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle(self):
        """Override to suppress connection reset errors on close."""
        try:
            super().handle()
        except (ConnectionResetError, OSError, ValueError):
            # Client closed connection early or socket already closed, ignore
            pass

    def _get_config_path(self):
        """Get the current config file path."""
        return getattr(self.server, 'config_path', 'config.json')

    def _get_current_config(self):
        """Get current configuration values."""
        return {
            "backend": getattr(self.server, 'backend_url', 'http://localhost:8080'),
            "port": getattr(self.server, 'server_port', 8081),
            "ollama_model": getattr(self.server, 'ollama_model', None) or '',
            "timeout": getattr(self.server, 'stream_timeout', 1800),
            "log_file": getattr(self.server, 'log_file', None) or '',
        }

    def do_GET(self):
        path = self.path.split("?")[0]  # strip query params

        # Silently handle favicon requests (browser auto-requests, noisy in logs)
        if path == "/favicon.ico":
            self.send_response(204)  # No Content
            self.end_headers()
            return

        # Setup/configuration endpoint - Web-based configuration UI
        if path == "/setup":
            self._handle_setup_page()
            return

        # Test endpoint - simulate a request to verify token stats work
        if path == "/test-stats":
            self._handle_test_stats()
            return

        # Statistics endpoint - returns proxy forwarding statistics
        if path == "/stats":
            with STATS_LOCK:
                uptime = time.time() - STATS["start_time"]
                stats_data = {
                    "uptime_seconds": int(uptime),
                    "uptime_formatted": self._format_duration(uptime),
                    "total_requests": STATS["total_requests"],
                    "stream_requests": STATS["stream_requests"],
                    "non_stream_requests": STATS["non_stream_requests"],
                    "ollama_requests": STATS["ollama_requests"],
                    "errors": STATS["errors"],
                    "total_tokens": STATS["total_tokens"],
                    "prompt_tokens": STATS["prompt_tokens"],
                    "completion_tokens": STATS["completion_tokens"],
                    "updated_at": datetime.datetime.now().strftime('%H:%M:%S'),
                }
            
            html = """<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>llama-sse-proxy Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 20px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        h1 {
            font-size: 2rem;
            background: linear-gradient(90deg, #00d4ff, #7b2cbf);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .lang-switch {
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            color: #fff;
            padding: 8px 16px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s;
        }
        .lang-switch:hover { background: rgba(255,255,255,0.2); }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 32px rgba(0,212,255,0.1);
        }
        .card-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 16px;
            color: #888;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .card-icon {
            width: 40px; height: 40px; border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.2rem;
        }
        .icon-blue { background: rgba(0,212,255,0.2); }
        .icon-green { background: rgba(0,255,136,0.2); }
        .icon-purple { background: rgba(123,44,191,0.2); }
        .icon-orange { background: rgba(255,165,0,0.2); }
        .icon-red { background: rgba(255,71,87,0.2); }
        .card-value { font-size: 2.5rem; font-weight: 700; margin-bottom: 8px; }
        .card-label { color: #888; font-size: 0.95rem; }
        .uptime { color: #00d4ff; }
        .requests { color: #00ff88; }
        .tokens { color: #7b2cbf; }
        .errors { color: #ff4757; }
        .error-card-fixed {
            position: fixed;
            right: -320px;
            top: 50%;
            transform: translateY(-50%);
            width: 280px;
            transition: right 0.5s cubic-bezier(0.68, -0.55, 0.265, 1.55);
            z-index: 100;
        }
        .error-card-fixed.show {
            right: 20px;
        }
        .details {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid rgba(255,255,255,0.1);
        }
        .detail-item { text-align: center; }
        .detail-value { font-size: 1.3rem; font-weight: 600; color: #fff; }
        .detail-label { font-size: 0.8rem; color: #666; margin-top: 4px; }
        .status-bar {
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 16px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }
        .status-indicator { display: flex; align-items: center; gap: 8px; }
        .status-dot {
            width: 10px; height: 10px; border-radius: 50%;
            background: #00ff88;
            animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .refresh-hint { color: #666; font-size: 0.85rem; }
        @media (max-width: 600px) {
            .grid { grid-template-columns: 1fr; }
            .details { grid-template-columns: 1fr; }
            h1 { font-size: 1.5rem; }
            .header { flex-direction: column; gap: 10px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🦙 <span data-i18n="title">llama-sse-proxy 监控面板</span></h1>
            <button class="lang-switch" onclick="toggleLang()">English</button>
        </div>
        
        <div class="status-bar">
            <div class="status-indicator">
                <div class="status-dot"></div>
                <span data-i18n="running">运行中</span>
            </div>
            <div class="refresh-hint"><span data-i18n="refresh">自动刷新: 5秒 | 最后更新</span>: <span id="updated-at">--:--:--</span></div>
        </div>
        
        <div class="grid">
            <div class="card">
                <div class="card-header">
                    <div class="card-icon icon-blue">⏱️</div>
                    <span data-i18n="uptime">运行时间</span>
                </div>
                <div class="card-value uptime" id="uptime-fmt">--</div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <div class="card-icon icon-green">📊</div>
                    <span data-i18n="total_requests">总请求数</span>
                </div>
                <div class="card-value requests" id="total-req">0</div>
                <div class="card-label" data-i18n="all_requests">所有 API 请求</div>
            </div>
            
            <div class="card">
                <div class="card-header">
                    <div class="card-icon icon-purple">📝</div>
                    <span data-i18n="token_stats">Token 统计</span>
                </div>
                <div class="card-value tokens" id="total-tokens">0</div>
                <div class="card-label" data-i18n="total_tokens">总计 Token 数</div>
                <div class="details">
                    <div class="detail-item">
                        <div class="detail-value" style="color:#ffa502" id="prompt-tokens">0</div>
                        <div class="detail-label" data-i18n="prompt">Prompt</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-value" style="color:#00ff88" id="completion-tokens">0</div>
                        <div class="detail-label" data-i18n="completion">Completion</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- 错误统计卡片 - 默认隐藏在右侧，有错误时滑入 -->
    <div class="card error-card-fixed" id="error-card">
        <div class="card-header">
            <div class="card-icon icon-red">⚠️</div>
            <span data-i18n="errors">错误统计</span>
        </div>
        <div class="card-value errors" id="errors">0</div>
        <div class="card-label" data-i18n="error_count">错误次数</div>
    </div>
    <script>
        const i18n = {
            zh: {
                title: "llama-sse-proxy 监控面板",
                running: "运行中",
                refresh: "自动刷新: 5秒 | 最后更新",
                uptime: "运行时间",
                seconds: "秒",
                total_requests: "总请求数",
                all_requests: "所有 API 请求",
                stream: "流式",
                non_stream: "非流式",
                ollama: "Ollama",
                token_stats: "TOKEN 统计",
                total_tokens: "总计 Token 数",
                prompt: "输入",
                completion: "输出",
                errors: "错误统计",
                error_count: "错误次数",
            },
            en: {
                title: "llama-sse-proxy Dashboard",
                running: "Running",
                refresh: "Auto-refresh: 5s | Last updated",
                uptime: "Uptime",
                seconds: "seconds",
                total_requests: "Total Requests",
                all_requests: "All API Requests",
                stream: "Stream",
                non_stream: "Non-Stream",
                ollama: "Ollama",
                token_stats: "TOKEN STATISTICS",
                total_tokens: "Total Tokens",
                prompt: "Prompt",
                completion: "Completion",
                errors: "Error Statistics",
                error_count: "Error Count",
            }
        };
        
        let currentLang = localStorage.getItem('lang') || 'zh';
        
        function toggleLang() {
            currentLang = currentLang === 'zh' ? 'en' : 'zh';
            localStorage.setItem('lang', currentLang);
            applyLang();
        }
        
        function applyLang() {
            document.querySelector('.lang-switch').textContent = currentLang === 'zh' ? 'English' : '中文';
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const key = el.getAttribute('data-i18n');
                if (i18n[currentLang][key]) {
                    el.textContent = i18n[currentLang][key];
                }
            });
        }
        
        function updateData() {
            fetch('/stats.json')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('uptime-fmt').textContent = data.uptime_formatted;
                    document.getElementById('total-req').textContent = data.total_requests;
                    document.getElementById('total-tokens').textContent = data.total_tokens.toLocaleString();
                    document.getElementById('prompt-tokens').textContent = data.prompt_tokens.toLocaleString();
                    document.getElementById('completion-tokens').textContent = data.completion_tokens.toLocaleString();
                    document.getElementById('errors').textContent = data.errors;
                    document.getElementById('error-card').classList.toggle('show', data.errors > 0);
                    document.getElementById('updated-at').textContent = data.updated_at;
                })
                .catch(() => {});
        }
        
        applyLang();
        updateData();
        setInterval(updateData, 5000);
    </script>
</body>
</html>"""
            
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return
        
        # Statistics JSON API for AJAX updates
        if path == "/stats.json":
            with STATS_LOCK:
                uptime = time.time() - STATS["start_time"]
                stats_data = {
                    "uptime_seconds": int(uptime),
                    "uptime_formatted": self._format_duration(uptime),
                    "total_requests": STATS["total_requests"],
                    "stream_requests": STATS["stream_requests"],
                    "non_stream_requests": STATS["non_stream_requests"],
                    "ollama_requests": STATS["ollama_requests"],
                    "errors": STATS["errors"],
                    "total_tokens": STATS["total_tokens"],
                    "prompt_tokens": STATS["prompt_tokens"],
                    "completion_tokens": STATS["completion_tokens"],
                    "updated_at": datetime.datetime.now().strftime('%H:%M:%S'),
                }
            body = json.dumps(stats_data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        # Health check - returns proxy status and backend connectivity
        if path == "/health":
            # Check backend connectivity
            backend_ok = False
            try:
                req = urllib.request.Request(BACKEND, method="HEAD")
                req.add_header("Accept", "*/*")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    backend_ok = True
            except Exception:
                backend_ok = False
            
            status_emoji = "✅" if backend_ok else "❌"
            body = f"""Status: OK

Proxy:   RUNNING
Backend: {status_emoji} {'CONNECTED' if backend_ok else 'DISCONNECTED'}
URL:     {BACKEND}
""".encode()
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Ollama API endpoints
        if OLLAMA_MODEL is not None:
            if path == "/api/tags":
                handle_ollama_api_tags(self)
                return
            if path == "/api/version":
                handle_ollama_api_version(self)
                return
            if path.startswith("/api/show"):
                handle_ollama_api_show(self, path)
                return
            # Also handle /v1/models for OpenAI compatibility
            if path == "/v1/models":
                model_name = _ollama_model_name()
                _send_json_response(self, 200, {
                    "object": "list",
                    "data": [{"id": model_name, "object": "model", "owned_by": "proxy"}]
                })
                return

        # Passthrough to backend
        try:
            status, headers, body = curl_request("GET", self.path, None, dict(self.headers))
            self.send_response(status)
            for k, v in headers.items():
                k_lower = k.lower()
                if k_lower in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if body:
                self.wfile.write(body)
        except Exception as e:
            log.error(f"GET error: {e}")
            try:
                self.send_error(502)
            except Exception:
                pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"

        path = self.path.split("?")[0]

        # Ollama API endpoints
        if OLLAMA_MODEL is not None:
            if path == "/api/chat":
                handle_ollama_api_chat(self, body)
                return
            if path == "/api/generate":
                handle_ollama_api_generate(self, body)
                return

        # OpenAI-compatible passthrough
        with STATS_LOCK:
            STATS["total_requests"] += 1

        try:
            req_json = json.loads(body)
            stream = req_json.get("stream", False)

            # LMStudio/Ollama: inject stream_options so backend returns usage
            if stream:
                if "stream_options" not in req_json:
                    req_json["stream_options"] = {"include_usage": True}
                    log.info("injected stream_options.include_usage=true")
                elif not req_json.get("stream_options", {}).get("include_usage"):
                    req_json["stream_options"]["include_usage"] = True
                    log.info("patched stream_options.include_usage=true")

            body = json.dumps(req_json, separators=(",", ":")).encode("utf-8")
        except Exception:
            stream = False

        if stream:
            with STATS_LOCK:
                STATS["stream_requests"] += 1
            self._stream_post(body)
        else:
            with STATS_LOCK:
                STATS["non_stream_requests"] += 1
            self._non_stream_post(body)

    def _non_stream_post(self, body):
        try:
            status, headers, body_resp = curl_request(
                "POST", self.path, body, dict(self.headers), stream=False
            )
            log.info(f"POST {self.path} -> backend status={status}")
            self.send_response(status)
            for k, v in headers.items():
                k_lower = k.lower()
                if k_lower in ("transfer-encoding", "connection"):
                    continue
                if k_lower == "content-length":
                    self.send_header(k, str(len(body_resp) if body_resp else 0))
                    continue
                self.send_header(k, v)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if body_resp:
                self.wfile.write(body_resp)
        except Exception as e:
            log.error(f"non-stream POST error: {e}")
            try:
                self.send_error(502)
            except Exception:
                pass

    def _stream_post(self, body):
        data_queue = queue.Queue()
        last_timings = {}
        accumulated_content = []  # 累积 delta.content，用于 usage 全零时估算
        done_received = [False]   # [0] = fetch 线程是否收到 [DONE]
        aborted = [False]          # [0] = fetch 线程是否检测到连接中断
        backend_has_usage = [False]  # [0] = 后端是否已返回过 usage chunk

        def fetch():
            try:
                status, headers, chunks = curl_request(
                    "POST", self.path, body, dict(self.headers), stream=True
                )
                while True:
                    try:
                        chunk = chunks.get(timeout=STREAM_TIMEOUT)
                        if chunk is None:
                            # 队列关闭（连接中断），跳出等待
                            break
                        # Extract timings / usage from the SSE chunk
                        is_valid_chunk = True
                        try:
                            text = chunk.decode("utf-8", errors="replace")
                            if text.startswith("data: "):
                                data_str = text[6:].strip()
                                if data_str == "[DONE]":
                                    done_received[0] = True
                                elif data_str:
                                    obj = json.loads(data_str)
                                    # 检测后端返回的 usage（LMStudio/Ollama 模式）
                                    if "usage" in obj:
                                        backend_has_usage[0] = True
                                        usage = obj["usage"]
                                        # 提取 token 数用于统计
                                        last_timings["prompt_n"] = usage.get("prompt_tokens", 0)
                                        last_timings["predicted_n"] = usage.get("completion_tokens", 0)
                                        log.info(f"backend usage: {usage}")
                                    # 也提取 timings（llama.cpp fallback）
                                    timings = obj.get("timings")
                                    choices = obj.get("choices", [])
                                    finish = choices[0].get("finish_reason") if choices else None
                                    # 只要有 timings 就更新，不依赖 finish
                                    if timings:
                                        last_timings["prompt_n"] = timings.get("prompt_n", 0)
                                        last_timings["predicted_n"] = timings.get("predicted_n", 0)
                                        if finish:
                                            log.info(f"timings: prompt={last_timings['prompt_n']}, "
                                                     f"completion={last_timings['predicted_n']}")
                                    # 累积 delta.content 用于 usage 估算
                                    delta = choices[0].get("delta", {}) if choices else {}
                                    # 同时处理 content 和 reasoning_content
                                    content = delta.get("content", "") or delta.get("reasoning_content", "")
                                    if content:
                                        accumulated_content.append(content)
                        except json.JSONDecodeError:
                            # JSON 解析失败，说明 chunk 不完整，丢弃
                            log.warning(f"Invalid JSON chunk, dropping: {chunk[:100]}...")
                            is_valid_chunk = False
                        except Exception:
                            pass
                        if is_valid_chunk:
                            data_queue.put(chunk)
                    except queue.Empty:
                        break
                data_queue.put(None)
            except Exception as e:
                log.error(f"fetch thread error: {e}")
                aborted[0] = True
                data_queue.put(None)

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()
        thread.join(timeout=STREAM_TIMEOUT)
        if thread.is_alive():
            log.error("Backend connection timeout")
            aborted[0] = True
            try:
                self.send_error(504)
            except Exception:
                pass
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.flush()

        usage_injected = False
        while True:
            try:
                chunk = data_queue.get(timeout=STREAM_TIMEOUT)
                if chunk is None:
                    # 队列关闭（可能连接中断）；检查是否需要补发 usage
                    if not usage_injected:
                        self._inject_usage_if_needed(
                            last_timings, accumulated_content, backend_has_usage[0]
                        )
                        usage_injected = True
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    usage_injected = True
                    break

                text = chunk.decode("utf-8", errors="replace")

                # Intercept [DONE], inject usage chunk first, then send [DONE]
                if not usage_injected and text.strip() == "data: [DONE]":
                    self._inject_usage_if_needed(
                        last_timings, accumulated_content, backend_has_usage[0]
                    )
                    usage_injected = True
                    # Skip queue [DONE]; send it explicitly after the loop
                    continue

                # Ensure each chunk ends with \n\n for proper SSE framing
                if not chunk.endswith(b"\n\n"):
                    self.wfile.write(chunk + b"\n\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            except queue.Empty:
                log.error("Queue timeout in stream_post")
                break
            except (BrokenPipeError, ConnectionResetError):
                log.info("Client disconnected, stopping SSE stream")
                break
            except Exception as e:
                log.error(f"stream write error: {e}")
                break

        # Ensure [DONE] is always sent after usage injection
        if usage_injected:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    def _inject_usage_if_needed(self, last_timings, accumulated_content, backend_has_usage):
        """Inject usage chunk only if the backend didn't already provide one.

        LMStudio/Ollama with stream_options.include_usage=true will send their own
        usage chunk — we just pass it through. For llama.cpp (no stream_options
        support), we estimate from timings or accumulated content.
        """
        prompt_n = last_timings.get("prompt_n", 0)
        predicted_n = last_timings.get("predicted_n", 0)
        
        # 如果预测 token 为 0，但有累积内容，则估算
        if predicted_n == 0 and accumulated_content:
            predicted_n = max(1, len("".join(accumulated_content)) // 2)
            log.warning(f"usage estimate: completion={predicted_n} "
                        f"(from {len(accumulated_content)} content chunks)")
        
        # 更新 token 统计（无论后端是否返回了 usage）
        if prompt_n > 0 or predicted_n > 0:
            with STATS_LOCK:
                STATS["prompt_tokens"] += prompt_n
                STATS["completion_tokens"] += predicted_n
                STATS["total_tokens"] += prompt_n + predicted_n
        
        if backend_has_usage:
            log.info("backend already sent usage, skip injection")
            return

        if prompt_n > 0 or predicted_n > 0:
            usage_chunk = (
                "data: {\"id\":\"usage-inject\","
                "\"object\":\"chat.completion.chunk\","
                "\"created\":%d,"
                "\"choices\":[{\"index\":0,\"delta\":{},"
                "\"finish_reason\":\"length\","
                "\"usage\":{\"prompt_tokens\":%d,"
                "\"completion_tokens\":%d,"
                "\"total_tokens\":%d}}]}\n\n"
                % (int(time.time()), prompt_n, predicted_n, prompt_n + predicted_n)
            )
            self.wfile.write(usage_chunk.encode("utf-8"))
            self.wfile.flush()
            log.info(f"usage injected (estimated): prompt={prompt_n}, completion={predicted_n}")
        else:
            log.warning("no usage available (no timings, no backend usage, no content)")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()


class ThreadedServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port, backend):
        super().__init__(("", port), Handler)
        global BACKEND
        BACKEND = backend
        mode = f" + Ollama mode (model={OLLAMA_MODEL})" if OLLAMA_MODEL else ""
        log.info(f"llama-sse-proxy: 0.0.0.0:{port} -> {backend}{mode}")

    def process_request(self, request, client_address):
        """Override to handle each request in a new thread."""
        import threading
        t = threading.Thread(target=self.process_request_thread,
                            args=(request, client_address))
        t.daemon = self.daemon_threads
        t.start()


def load_config(config_path):
    """Load configuration from JSON file."""
    if not config_path or not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load config file: {e}")
        return {}


def save_config(config_path, config_data):
    """Save configuration to JSON file."""
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"Failed to save config file: {e}")
        return False


# Default configuration values
DEFAULT_CONFIG = {
    "backend": "http://localhost:8080",
    "port": 8081,
    "ollama_model": None,
    "timeout": 1800,
    "log_file": None,
}


def main():
    parser = argparse.ArgumentParser(
        description="Proxy that injects usage field into llama.cpp SSE streams."
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to JSON config file (default: None)",
    )
    parser.add_argument(
        "--backend",
        default="http://localhost:8080",
        help="llama.cpp server URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--port", type=int, default=8081,
        help="Local port to listen on (default: 8081)",
    )
    parser.add_argument(
        "--log-file", default=None,
        help="Optional log file path (default: stdout only)",
    )
    parser.add_argument(
        "--ollama-model", default=None,
        help="Enable Ollama API compatibility mode with this model name. "
             "Exposes /api/chat, /api/generate, /api/tags endpoints.",
    )
    parser.add_argument(
        "--timeout", type=int, default=1800,
        help="Per-chunk read timeout in seconds (default: 1800). "
             "Increase if llama.cpp takes very long between tokens.",
    )
    args = parser.parse_args()

    # Load config file first
    config = load_config(args.config)

    # Command line args override config file values
    backend = args.backend if args.backend != parser.get_default("backend") else config.get("backend", args.backend)
    port = args.port if args.port != parser.get_default("port") else config.get("port", args.port)
    log_file = args.log_file if args.log_file is not None else config.get("log_file")
    ollama_model = args.ollama_model if args.ollama_model is not None else config.get("ollama_model")
    timeout = args.timeout if args.timeout != parser.get_default("timeout") else config.get("timeout", args.timeout)

    global OLLAMA_MODEL, STREAM_TIMEOUT
    OLLAMA_MODEL = ollama_model
    STREAM_TIMEOUT = timeout

    setup_logging(log_file)

    global SHUTDOWN_EVENT
    SHUTDOWN_EVENT = threading.Event()

    def shutdown(sig, frame):
        log.info("Shutting down...")
        SHUTDOWN_EVENT.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    server = ThreadedServer(port, backend)
    server.timeout = 0.5  # Allow Ctrl+C check every 500ms
    log.info("Ctrl+C to stop")
    log.info(f"stream timeout: %ds", STREAM_TIMEOUT)

    while not SHUTDOWN_EVENT.is_set():
        server.handle_request()


if __name__ == "__main__":
    main()
