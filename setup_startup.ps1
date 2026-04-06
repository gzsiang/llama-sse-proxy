$startup = [Environment]::GetFolderPath("StartMenu") + "\Programs\Startup"
$lnkPath = Join-Path $startup "llama-sse-proxy.lnk"

$wscript = New-Object -ComObject WScript.Shell
$shortcut = $wscript.CreateShortcut($lnkPath)
$shortcut.TargetPath = "E:\AI\llama-sse-proxy\start.bat"
$shortcut.WindowStyle = 1   # 1 = SW_SHOWNORMAL (normal window)
$shortcut.Description = "llama-sse-proxy backend launcher"
$shortcut.Save()
Write-Host "Shortcut created at: $lnkPath"
