import json, urllib.request

url = "http://localhost:8081/v1/chat/completions"
req_body = json.dumps({
    "model": "default",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True
}).encode()

req = urllib.request.Request(url, data=req_body, method="POST")
req.add_header("Content-Type", "application/json")

with urllib.request.urlopen(req, timeout=30) as resp:
    raw = resp.read()
    print(f"Total bytes: {len(raw)}")
    print(f"Raw (first 500): {raw[:500]}")
    print()
    # Check if we can find "data: " boundaries
    text = raw.decode("utf-8", errors="replace")
    # Split by "data: " to see how many chunks
    parts = text.split("data: ")
    print(f"Number of 'data: ' occurrences: {len(parts) - 1}")
    for i, p in enumerate(parts):
        p = p.strip()
        if p:
            if p == "[DONE]":
                print(f"  Part {i}: [DONE]")
            else:
                try:
                    obj = json.loads(p)
                    choices = obj.get("choices", [])
                    usage = obj.get("usage")
                    delta = choices[0].get("delta", {}) if choices else {}
                    content = delta.get("content", "")
                    finish = choices[0].get("finish_reason") if choices else None
                    print(f"  Part {i}: content='{content}' finish={finish} usage={usage}")
                except:
                    print(f"  Part {i}: PARSE ERROR, first 100 chars: {p[:100]}")
