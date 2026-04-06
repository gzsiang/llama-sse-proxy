$body = '{"model":"Local-LLM-Model","messages":[{"role":"user","content":"say hi"}],"stream":true}'
$body | Out-File -FilePath "D:\AI\llama-sse-proxy\chat_stream.json" -Encoding ascii
Write-Host "=== /api/chat streaming via proxy ==="
$out = cmd /c "curl -v --max-time 60 http://localhost:8081/api/chat -H ""Content-Type: application/json"" -d @D:\AI\llama-sse-proxy\chat_stream.json" 2>&1
Write-Host $out
