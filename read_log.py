import sys
path = sys.argv[1] if len(sys.argv) > 1 else r'D:\AI\llama-sse-proxy\proxy.log'
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
    # Only show last 30 lines
    for line in lines[-30:]:
        print(line.rstrip())
