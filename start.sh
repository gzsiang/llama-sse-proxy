#!/bin/bash
# llama-sse-proxy launcher
# Edit BACKEND and PORT below to match your setup

BACKEND="http://localhost:8080"
PORT=8081

echo "Starting llama-sse-proxy..."
echo "  Proxy:   http://localhost:$PORT"
echo "  Backend: $BACKEND"
echo ""
echo "Press Ctrl+C to stop."
echo ""

python3 "$(dirname "$0")/llama_sse_proxy.py" --backend "$BACKEND" --port "$PORT"
