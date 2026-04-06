# llama-sse-proxy 本地改动记录

**对比时间**: 2026-04-06  
**远程仓库**: https://github.com/gzsiang/llama-sse-proxy  
**本地路径**: D:\AI\llama-sse-proxy

---

## 一、核心代码改动 (llama_sse_proxy.py)

### 1. 新增流式超时配置

**位置**: 全局变量 + 命令行参数

```python
# 新增全局变量 (第44-45行)
# Stream timeout (seconds) — per-chunk interval for both fetch and main thread
STREAM_TIMEOUT = 1800

# 新增命令行参数 (第964-968行)
parser.add_argument(
    "--timeout", type=int, default=1800,
    help="Per-chunk read timeout in seconds (default: 1800). "
         "Increase if llama.cpp takes very long between tokens.",
)

# 启动时应用 (第971-973行)
global OLLAMA_MODEL, STREAM_TIMEOUT
OLLAMA_MODEL = args.ollama_model
STREAM_TIMEOUT = args.timeout

# 启动日志 (第990行)
log.info(f"stream timeout: %ds", STREAM_TIMEOUT)
```

**影响**: 所有流式读取的超时时间从硬编码的 300/600 秒改为可配置的 1800 秒（30分钟）

---

### 2. SSE Chunk 分割逻辑改进

**位置**: `curl_request()` 函数中的 `fetch()` 内部

**原代码** (远程版本):
```python
# Split SSE chunks — llama.cpp sometimes sends compact format:
#   standard:    ...}\n\ndata: {...}\n\n
#   double-sp:  ...}  data: {...}
#   no sep:     ...}data: {...}   (most aggressive)
while True:
    sep = buf.find(b"\n\n")
    sep_len = 2
    if sep == -1:
        sep = buf.find(b"  ")  # ← 按双空格分割
        sep_len = 2
    if sep == -1:
        ds = buf.find(b"}data: ")
        if ds != -1:
            sep = ds + 1
            sep_len = 0
    # ... 直接 put chunk
    data_queue.put(buf[:sep])
```

**新代码** (本地版本):
```python
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
        # 移除了双空格分割逻辑
        ds = buf.find(b"}data: ")
        if ds != -1:
            sep = ds + 1
            sep_len = 0
    # ... 新增 JSON 验证
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
```

**改动原因**:
- **问题**: 双空格分割会导致中文内容（可能包含连续空格）被错误分割，破坏 JSON 完整性
- **解决**: 移除双空格分割，改用更严格的 JSON 验证，只转发有效的 SSE 数据

---

### 3. 超时时间统一替换

**位置**: `_stream_post()` 和 `_collect_stream_chunks()` 函数

| 位置 | 原值 | 新值 |
|------|------|------|
| `chunks.get(timeout=60)` | 60 | `STREAM_TIMEOUT` |
| `thread.join(timeout=600)` | 600 | `STREAM_TIMEOUT` |
| `data_queue.get(timeout=300)` | 300 | `STREAM_TIMEOUT` |

---

## 二、文件差异汇总

### 本地新增的文件（测试/工具脚本）

| 文件名 | 用途 |
|--------|------|
| `chat_stream.json` | 聊天流测试数据 |
| `check_proxy.py` | 代理检查工具 |
| `check_proxy_bin.py` | 二进制代理检查 |
| `find_python.ps1` | 查找 Python 解释器 |
| `read_log.py` | 日志读取工具 |
| `start_proxy.bat` | Windows 启动脚本 |
| `start_proxy.ps1` | PowerShell 启动脚本 |
| `tb3.json` | 测试数据 |
| `test_backend.ps1` | 后端测试脚本 |
| `test_body.json` | 请求体测试数据 |
| `test_body2.json` | 请求体测试数据 2 |
| `test_chat_body.json` | 聊天请求测试数据 |
| `test_chat_stream.ps1` | 聊天流测试脚本 |
| `test_ollama_api.ps1` | Ollama API 测试 |
| `test_openai_stream.py` | OpenAI 流测试 |
| `test_stream.py` | 流式测试 |
| `test_stream_body.json` | 流请求体测试数据 |

### 远程有但本地没有的文件

| 文件名 | 用途 |
|--------|------|
| `.gitignore` | Git 忽略配置 |
| `config.bat.example` | 配置示例 |
| `issue-comment.md` | Issue 评论模板 |
| `LICENSE` | 许可证 |
| `README.md` | 项目说明 |
| `register_task.ps1` | 注册任务脚本 |
| `requirements.txt` | Python 依赖 |
| `setup_startup.ps1` | 开机启动设置 |
| `start.bat` | 启动脚本 |
| `start.sh` | Linux 启动脚本 |
| `start_hidden.bat` | 隐藏窗口启动 |
| `test_usage_fallback.py` | usage 回退测试 |
| `unregister_task.ps1` | 卸载任务脚本 |

---

## 三、改动总结

### 解决的问题
1. **中文内容分割错误** - 移除双空格分割，避免中文文本被错误截断
2. **JSON 完整性验证** - 新增 chunk 验证，丢弃损坏的数据
3. **超时时间可配置** - 从硬编码改为命令行参数，适应不同场景

### 建议操作
1. 将核心代码改动推送到远程仓库
2. 整理测试脚本，决定是否加入版本控制
3. 更新 README 说明新的 `--timeout` 参数

---

*生成时间: 2026-04-06 17:11*
