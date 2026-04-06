Unregister-ScheduledTask -TaskName "llama-sse-proxy" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "llama-sse-proxy scheduled task removed"
