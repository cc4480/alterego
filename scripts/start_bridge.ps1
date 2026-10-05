# Auto-start wrapper for the pc-mcp-bridge server.
# Launched by the PCBridgeServer scheduled task at user logon.
# Runs in the user's interactive session so approval dialogs are visible.

$ErrorActionPreference = "Stop"
$Repo = "C:\Users\celos\.copilot\chats\2026-10-04\stunning-waffle-8502e108\pc-mcp-bridge"

# Don't stack servers: if 127.0.0.1:8765 is already listening, exit quietly.
$inUse = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Write-Host "Bridge already listening on 8765 (PID $($inUse.OwningProcess)) - not starting another."
    exit 0
}

# Kill any orphaned server.py from a previous session that isn't listening.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$env:PC_BRIDGE_AUTO_APPROVE = "1"
Set-Location $Repo
& python pc-agent\server.py
