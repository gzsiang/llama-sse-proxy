import sys
path = sys.argv[1] if len(sys.argv) > 1 else r'D:\AI\llama-sse-proxy\llama_sse_proxy.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
print(f"Total lines: {len(lines)}")
print()

checks = [
    (24, 'import datetime', 'datetime import'),
    (110, 'compact', 'SSE compact separator'),
    (924, 'ollama-model', 'ollama-model arg'),
]
for lineno, keyword, desc in checks:
    if lineno <= len(lines):
        line = lines[lineno - 1].rstrip()
        ok = keyword in line
        print(f"  [{'OK' if ok else 'MISSING'}] Line {lineno} ({desc}): {line}")
    else:
        print(f"  [SHORT] Line {lineno} - file only has {len(lines)} lines")
