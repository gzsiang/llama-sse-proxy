Get-ChildItem -Path C:\Users\Gaba -Filter python.exe -Recurse -Depth 6 -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
