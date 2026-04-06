Write-Host "=== Test 1: /api/tags ==="
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8081/api/tags" -TimeoutSec 5
    Write-Host "OK: $($r | ConvertTo-Json -Depth 3)"
} catch {
    Write-Host "FAIL: $_"
}

Write-Host ""
Write-Host "=== Test 2: /api/chat (no stream) ==="
try {
    $body = '{"model":"Local-LLM-Model","messages":[{"role":"user","content":"say hi"}],"stream":false}'
    $r = Invoke-RestMethod -Uri "http://localhost:8081/api/chat" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 30
    Write-Host "OK: $($r | ConvertTo-Json -Depth 3)"
} catch {
    Write-Host "FAIL: $_"
}

Write-Host ""
Write-Host "=== Test 3: /api/chat (stream) via curl ==="
try {
    $body = '{"model":"Local-LLM-Model","messages":[{"role":"user","content":"say hi"}],"stream":true}'
    $body | Out-File -FilePath "D:\AI\llama-sse-proxy\test_body.json" -Encoding ascii
    $output = cmd /c "curl -s --max-time 30 http://localhost:8081/api/chat -H ""Content-Type: application/json"" -d @D:\AI\llama-sse-proxy\test_body.json" 2>&1
    Write-Host "OK:"
    Write-Host $output
} catch {
    Write-Host "FAIL: $_"
}

Write-Host ""
Write-Host "=== Test 4: /api/generate (stream) via curl ==="
try {
    $body2 = '{"model":"Local-LLM-Model","prompt":"say hi","stream":true}'
    $body2 | Out-File -FilePath "D:\AI\llama-sse-proxy\test_body2.json" -Encoding ascii
    $output2 = cmd /c "curl -s --max-time 30 http://localhost:8081/api/generate -H ""Content-Type: application/json"" -d @D:\AI\llama-sse-proxy\test_body2.json" 2>&1
    Write-Host "OK:"
    Write-Host $output2
} catch {
    Write-Host "FAIL: $_"
}

Write-Host ""
Write-Host "=== DONE ==="
