<h4 align="center">
  English | <a href="./README.zh.md">中文</a>
</h4>

<br />

# llama-sse-proxy

> **📢 Announcement**
>
> The core mission of this project—solving the OpenClaw context usage tracking issue—has been successfully accomplished. The relevant bug has been fixed via PR [#64660](https://github.com/openclaw/openclaw/pull/64660) and merged into the main OpenClaw repository, with a fix expected in the upcoming release.

---

### 🚀 What is it?

This proxy is specifically built to solve the **OpenClaw context usage tracking problem**. Most local inference backends (like `llama.cpp`, `LM Studio`, or `vLLM`) return incorrect or missing `usage` fields in streaming responses, preventing OpenClaw from sensing context consumption and triggering auto-compaction (`/compact`).

**This proxy intercepts and injects standard OpenAI/Ollama formatted `usage` data to ensure seamless compatibility with OpenClaw.**

```text
OpenClaw → llama-sse-proxy :8081 → llama.cpp / LM Studio / vLLM :8080
```

### ✨ Core Features

- **🌐 Web Setup UI (Zero-Config)**: Configure everything via a beautiful browser interface—no manual JSON editing required. Includes live backend testing and one-click export.
- **📊 Real-time Dashboard**: A bilingual monitoring panel showing token usage, request statistics, and real-time backend connectivity.
- **🛠️ High Compatibility**: Full Ollama mode support (`/api/chat`, etc.) and optional `reasoning_content` merging for clients that don't support thinking modes.
- **⚡ Lightweight & Robust**: Zero external dependencies (Python standard library only); features append-only history, model caching with TTL, and slow-client protection.

### 🚀 Quick Start (Zero-Config)

Start the proxy directly without any configuration files:

```bash
python llama_sse_proxy.py
```

Once running, open `http://localhost:8081/setup` in your browser to configure:

![Setup UI](docs/images/setup.jpg)

- **One-click Test**: Enter your Backend URL and click **Test** to verify connectivity.
- **Live Apply**: All settings (except Port) take effect immediately upon saving.
- **Export Config**: Click **Export JSON** to download a `config.json` for future use.

---

### 🛠️ Launch Options Comparison

| Mode | Command / Script | Best For |
| :--- | :--- | :--- |
| **Recommended** | Access `/setup` via Web UI | **Zero-config**, live testing, and easy management |
| **Standard** | `start.bat` | Daily use with a visible console for logs |
| **Background** | `scripts/start_hidden.bat` | Running silently in the background |
| **Service-like** | `scripts/start_proxy.ps1` | Persistent startup via Windows Task Scheduler |

### ⚙️ CLI Arguments

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--backend` | `http://localhost:8080` | Backend URL (llama.cpp / LM Studio / vLLM) |
| `--port` | `8081` | Port this proxy listens on |
| `--ollama-model` | _(None)_ | Specify model name to enable Ollama API mode |
| `--timeout` | `1800` | Stream read timeout in seconds |

### 🔗 OpenClaw Configuration Example

Point OpenClaw's provider to this proxy instead of the backend directly:

```json
{
  "models": {
    "providers": {
      "llama-cpp": {
        "baseUrl": "http://localhost:8081/v1",
        "api": "openai-completions"
      }
    }
  }
}
```

---

MIT License
