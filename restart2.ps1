$log = "$env:USERPROFILE\pc-mcp-bridge\restart.log"
"START $(Get-Date)" | Out-File $log
Start-Sleep -Seconds 2
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
"KILLED $(Get-Date)" | Out-File $log -Append
Start-Sleep -Seconds 3
cd "$env:USERPROFILE\pc-mcp-bridge"
$env:PC_BRIDGE_FULL_ACCESS = "1"
"STARTING SERVER" | Out-File $log -Append
Start-Process -FilePath "python" -ArgumentList "pc-agent\server.py" -WindowStyle Minimized -RedirectStandardOutput "$env:USERPROFILE\pc-mcp-bridge\server-out.log" -RedirectStandardError "$env:USERPROFILE\pc-mcp-bridge\server-err.log"
"LAUNCHED $(Get-Date)" | Out-File $log -Append
