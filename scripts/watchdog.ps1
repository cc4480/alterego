# PCBridge watchdog: keeps the tunnel and server alive.
# Runs every 5 minutes via the PCBridgeWatchdog scheduled task.
# Pure ASCII - PowerShell 5.1 safe.

$ErrorActionPreference = "SilentlyContinue"
$Repo = "C:\Users\celos\.copilot\chats\2026-10-04\stunning-waffle-8502e108\pc-mcp-bridge"
$LogDir = "$env:APPDATA\pc-mcp-bridge"
$Log = "$LogDir\watchdog.log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory $LogDir | Out-Null }

function WLog($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File $Log -Append
}

# --- 1. Tunnel: is cloudflared running AND connected? ---
$tunnelProc = Get-Process cloudflared -ErrorAction SilentlyContinue
$tunnelOk = $false
if ($tunnelProc) {
    # Check the metrics endpoint for live HA connections
    try {
        $m = Invoke-WebRequest -Uri "http://127.0.0.1:20241/metrics" -TimeoutSec 5 -UseBasicParsing
        if ($m.Content -match 'cloudflared_tunnel_ha_connections (\d+)') {
            if ([int]$Matches[1] -gt 0) { $tunnelOk = $true }
        }
    } catch { }
}

if (-not $tunnelOk) {
    if ($tunnelProc) {
        WLog "tunnel process alive but 0 connections - killing"
        $tunnelProc | Stop-Process -Force
        Start-Sleep 3
    } else {
        WLog "tunnel DOWN - restarting"
    }
    Start-Process cloudflared -ArgumentList "tunnel run pc-bridge" -WindowStyle Hidden
    WLog "tunnel restart launched"
}

# --- 2. Server: is 8765 listening AND is the heartbeat fresh? ---
# A hung server can still hold the port, so check heartbeat.txt too.
# Stale = no heartbeat in 3+ minutes = hung, kill it.
$serverOk = $false
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    $hbFile = "$env:APPDATA\pc-mcp-bridge\heartbeat.txt"
    if (Test-Path $hbFile) {
        try {
            $hbTime = [int](Get-Content $hbFile -Raw)
            $ageSec = [int](Get-Date -UFormat "%s") - $hbTime
            if ($ageSec -lt 180) { $serverOk = $true }
            else { WLog "server heartbeat STALE (${ageSec}s) - hung, will restart" }
        } catch { }
    }
}

if (-not $serverOk) {
    if ($listening) { WLog "server HUNG (port held, heartbeat stale) - killing and restarting" }
    else { WLog "server DOWN (port not listening) - restarting via start_bridge.ps1" }
    # Kill orphans first
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$Repo\scripts\start_bridge.ps1`"" -WindowStyle Normal
    WLog "server restart launched"
}
