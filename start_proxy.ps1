$action = New-ScheduledTaskAction -Execute "D:\vLLM\Python312\python.exe" -Argument "D:\AI\llama-sse-proxy\llama_sse_proxy.py --backend http://localhost:8080 --port 8081 --ollama-model Local-LLM-Model --log-file D:\AI\llama-sse-proxy\proxy.log" -WorkingDirectory "D:\AI\llama-sse-proxy"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -RunLevel Highest -LogonType ServiceAccount
Unregister-ScheduledTask -TaskName "llama-sse-proxy" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "llama-sse-proxy" -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "llama-sse-proxy"
Start-Sleep -Seconds 3
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8081/api/tags" -TimeoutSec 5
    Write-Host "Proxy running! Model: $($r.models[0].name)"
} catch {
    Write-Host "FAILED: $_"
}
