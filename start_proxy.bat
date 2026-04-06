@echo off
start "llama-sse-proxy" /min cmd /c "D:\vLLM\Python312\python.exe D:\AI\llama-sse-proxy\llama_sse_proxy.py --backend http://localhost:8080 --port 8081 --ollama-model Local-LLM-Model > D:\AI\llama-sse-proxy\proxy.log 2>&1"
timeout /t 3 /nobreak >nul
netstat -ano | findstr :8081
