# Auto-start wrapper for the pc-mcp-bridge server.
# Launched by the PCBridgeServer scheduled task at user logon.
# Runs in the user's interactive session so approval dialogs are visible.
# Pure ASCII - PowerShell 5.1 safe.
#
# The repo path is derived from $env:USERPROFILE on each machine:
# $env:USERPROFILE\alterego, falling back to $env:USERPROFILE\pc-mcp-bridge.
# No machine-specific absolute path is hardcoded.

$ErrorActionPreference = "Stop"
$RepoPrimary = "$env:USERPROFILE\alterego"
$RepoFallback = "$env:USERPROFILE\pc-mcp-bridge"
if (Test-Path $RepoPrimary) {
    $Repo = $RepoPrimary
} elseif (Test-Path $RepoFallback) {
    $Repo = $RepoFallback
} else {
    $Repo = $RepoPrimary
}

# Don't stack servers: if 127.0.0.1:8765 is already listening, exit quietly.
$inUse = Get-NetTCPConnection -LocalPort 8765 -LocalAddress 127.0.0.1 -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    Write-Host "Bridge already listening on 8765 (PID $($inUse.OwningProcess)) - not starting another."
    exit 0
}

# Kill any orphaned server.py from a previous session that isn't listening.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$env:PC_BRIDGE_FULL_ACCESS = "1"
Set-Location $Repo
& python pc-agent\server.py
