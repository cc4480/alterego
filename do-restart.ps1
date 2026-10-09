Start-Sleep -Seconds 10
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
$env:PC_BRIDGE_FULL_ACCESS = "1"
$env:SEARXNG_URL = "http://127.0.0.1:8888"
$repo = "$env:USERPROFILE\pc-mcp-bridge"
Start-Process python -ArgumentList "$repo\pc-agent\server.py" -WorkingDirectory $repo -WindowStyle Minimized
