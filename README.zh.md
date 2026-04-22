<h4 align="center">
  <a href="./README.md">English</a> | 中文
</h4>

<br />

# llama-sse-proxy

> **📢 公告**
>
> 本项目的核心使命——解决 OpenClaw 的上下文用量追踪问题——已圆满完成。相关 Bug 已通过 PR [#64660](https://github.com/openclaw/openclaw/pull/64660) 被合并至 OpenClaw 主仓库，预计将在下一个 Release 版本中正式修复。

---

### 🚀 是什么？

本代理专为解决 **OpenClaw 的上下文用量追踪问题**而开发。由于主流本地推理后端（如 `llama.cpp`、`LM Studio`、`vLLM`）在流式响应中缺失或返回错误的 `usage` 字段，导致 OpenClaw 无法感知上下文消耗，从而使自动压缩（`/compact`）功能失效。

**本代理通过拦截并注入标准的 OpenAI/Ollama 格式 `usage` 数据，确保 OpenClaw 能完美工作。**

```text
OpenClaw → llama-sse-proxy :8081 → llama.cpp / LM Studio / vLLM :8080
```

### ✨ 核心特性

- **🌐 Web 配置界面 (零配置启动)**：无需手动编辑 `config.json`，直接通过浏览器进行图形化设置、后端连接测试与一键导出。
- **📊 实时监控面板**：提供美观的双语 Dashboard，实时显示 Token 用量、请求统计及后端状态。
- **🛠️ 高度兼容**：完美支持 Ollama 模式（`/api/chat` 等接口），并可自动合并 `reasoning_content` 到 `content` 以适配不支持思考模式的客户端。
- **⚡ 轻量高效**：仅使用 Python 标准库，无任何外部依赖；支持 append-only 日志记录与模型缓存 TTL。

### 🚀 快速上手 (零配置方案)

无需创建配置文件，直接运行即可启动：

```bash
python llama_sse_proxy.py
```

启动后，在浏览器访问 `http://localhost:8081/setup` 进行图形化配置：

![Setup UI](docs/images/setup.jpg)

- **一键测试**：输入 Backend URL 后点击 **Test** 验证连接。
- **实时生效**：除端口 (Port) 外，修改参数后保存即可立即应用。
- **导出配置**：满意后可点击 **Export JSON** 下载 `config.json` 以备下次使用。

---

### 🛠️ 启动方式对比

| 模式 | 命令 / 脚本 | 特点 |
| :--- | :--- | :--- |
| **推荐 (图形化)** | 访问 `/setup` 页面 | **零配置**，支持实时测试与导出 |
| **日常使用** | `start.bat` | 带控制台窗口，方便查看日志 |
| **后台运行** | `scripts/start_hidden.bat` | 完全隐藏窗口，静默运行 |
| **任务计划** | `scripts/start_proxy.ps1` | 模拟 Windows 服务启动 |

### ⚙️ 参数说明 (命令行)

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--backend` | `http://localhost:8080` | 后端地址 (llama.cpp / LM Studio / vLLM) |
| `--port` | `8081` | 代理监听端口 |
| `--ollama-model` | _(无)_ | 指定模型名以启用 Ollama API 兼容模式 |
| `--timeout` | `1800` | 流式读取超时时间 (秒) |

### 🔗 OpenClaw 配置示例

在 OpenClaw 中将 provider 指向本代理：

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
