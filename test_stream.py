import sys, json, urllib.request

url = "http://localhost:8081/api/chat"
req_body = json.dumps({
    "model": "Local-LLM-Model",
    "messages": [{"role": "user", "content": "说一个字"}],
    "stream": True
}).encode()

req = urllib.request.Request(url, data=req_body, method="POST")
req.add_header("Content-Type", "application/json")

with urllib.request.urlopen(req, timeout=30) as resp:
    raw = resp.read()
    lines = raw.decode("utf-8").strip().split("\n")
    print(f"Total lines received: {len(lines)}")
    print(f"Total bytes: {len(raw)}")
    print()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            done = obj.get("done", False)
            msg = obj.get("message", {}).get("content", "")
            eval_count = obj.get("eval_count", "")
            print(f"  Line {i}: done={done} content='{msg}' eval_count={eval_count}")
        except json.JSONDecodeError as e:
            print(f"  Line {i}: JSON PARSE ERROR: {e}")
            print(f"    Raw: {line[:200]}")
