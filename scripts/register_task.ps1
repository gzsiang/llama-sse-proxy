$action = New-ScheduledTaskAction -Execute "E:\AI\llama-sse-proxy\start_hidden.bat"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden

Register-ScheduledTask -TaskName "llama-sse-proxy" -Action $action -Trigger $trigger -Settings $settings -Description "llama-sse-proxy backend launcher" -Force
Write-Host "Done"
