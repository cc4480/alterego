# Showcase launcher for the pc-mcp-bridge server.
#
# Purpose: run the bridge with ZERO approval dialogs for a live demo /
# showcase. Sets dontAsk mode AND bypasses the dontAsk startup
# confirmation, so no window appears at startup or during the session.
# Every call is still authenticated and written to the audit log.
#
# Use start_bridge.ps1 instead for normal day-to-day operation, where
# the startup confirmation and approval dialogs are the point.
#
# The repo path is derived from this script's location, so it works in
# any clone. Runs in the user's interactive session.

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot

# Don't stack servers: if 127.0.0.1:8765 is already listening, exit quietly.
$inUse = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Write-Host "Bridge already listening on 8765 (PID $($inUse.OwningProcess)) - not starting another."
    Write-Host "Restart the existing server with this script if it is not in showcase mode."
    exit 0
}

# Kill any orphaned server.py from a previous session that isn't listening.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$env:PC_BRIDGE_FULL_ACCESS = "1"
$env:PC_BRIDGE_FULL_ACCESS_CONFIRM = "0"

Write-Host "=============================================================="
Write-Host " SHOWCASE MODE: approval dialogs are OFF for this session."
Write-Host " All bridge calls execute immediately and are audit-logged."
Write-Host " End this session (Ctrl+C / close) and start start_bridge.ps1"
Write-Host " to return to normal approval-dialog operation."
Write-Host "=============================================================="

Set-Location $Repo
& python pc-agent\server.py
