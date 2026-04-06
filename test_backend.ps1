# Test if llama.cpp natively supports Ollama API endpoints
Write-Host "=== Test 1: /api/tags (Ollama) ==="
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8080/api/tags" -TimeoutSec 5
    Write-Host ($r | ConvertTo-Json -Depth 3)
} catch {
    Write-Host "NOT SUPPORTED: $_"
}

Write-Host ""
Write-Host "=== Test 2: /api/chat non-stream (Ollama) ==="
$body = '{"model":"default","messages":[{"role":"user","content":"say hi"}],"stream":false}'
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8080/api/chat" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 30
    Write-Host ($r | ConvertTo-Json -Depth 3)
} catch {
    Write-Host "NOT SUPPORTED: $_"
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        Write-Host "Body: $($reader.ReadToEnd())"
    }
}

Write-Host ""
Write-Host "=== Test 3: /v1/chat/completions non-stream (OpenAI) ==="
$body2 = '{"model":"default","messages":[{"role":"user","content":"say hi"}],"stream":false}'
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8080/v1/chat/completions" -Method POST -ContentType "application/json" -Body $body2 -TimeoutSec 30
    Write-Host ($r | ConvertTo-Json -Depth 5)
} catch {
    Write-Host "FAIL: $_"
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        Write-Host "Body: $($reader.ReadToEnd())"
    }
}

Write-Host ""
Write-Host "=== Test 4: /v1/chat/completions stream (OpenAI) ==="
$body3 = '{"model":"default","messages":[{"role":"user","content":"say hi"}],"stream":true}'
$body3 | Out-File -FilePath "D:\AI\llama-sse-proxy\tb3.json" -Encoding ascii
$out = cmd /c "curl -s --max-time 30 http://localhost:8080/v1/chat/completions -H ""Content-Type: application/json"" -d @D:\AI\llama-sse-proxy\tb3.json" 2>&1
Write-Host $out

Write-Host ""
Write-Host "=== DONE ==="
