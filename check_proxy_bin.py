import sys
path = sys.argv[1] if len(sys.argv) > 1 else r'D:\AI\llama-sse-proxy\llama_sse_proxy.py'
with open(path, 'rb') as f:
    data = f.read()
print(f"File size: {len(data)} bytes")
print(f"Has 'import datetime': {b'import datetime' in data}")
print(f"Has 'compact SSE': {b'compact SSE' in data}")
print(f"Has 'ollama-model': {b'ollama-model' in data}")
print(f"Has 'double-space separator': {b'double-space separator' in data}")
