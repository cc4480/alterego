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

function Test-Tunnel() {
    $p = Get-Process cloudflared -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    try {
        $m = Invoke-WebRequest -Uri "http://127.0.0.1:20241/metrics" -TimeoutSec 5 -UseBasicParsing
        if ($m.Content -match 'cloudflared_tunnel_ha_connections (\d+)') {
            return ([int]$Matches[1] -gt 0)
        }
    } catch { }
    return $false
}

function Test-Server() {
    $listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) { return $false }
    $hbFile = "$env:APPDATA\pc-mcp-bridge\heartbeat.txt"
    if (-not (Test-Path $hbFile)) { return $false }
    try {
        $hbTime = [int](Get-Content $hbFile -Raw)
        $ageSec = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $hbTime
        return ($ageSec -lt 180)
    } catch { return $false }
}

# --- 1. Tunnel ---
if (-not (Test-Tunnel)) {
    $tp = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($tp) {
        WLog "tunnel process alive but 0 connections - killing"
        $tp | Stop-Process -Force
        Start-Sleep 3
    } else {
        WLog "tunnel DOWN - restarting"
    }
    Start-Process cloudflared -ArgumentList "tunnel run pc-bridge" -WindowStyle Hidden
    WLog "tunnel restart launched, verifying reconnect..."
    $reconnected = $false
    for ($i = 1; $i -le 3; $i++) {
        Start-Sleep 15
        if (Test-Tunnel) { $reconnected = $true; break }
        WLog "tunnel reconnect attempt $i/3 not yet connected"
    }
    if ($reconnected) { WLog "tunnel RECONNECTED and verified" }
    else { WLog "tunnel FAILED to reconnect after 3 attempts - retry next cycle" }
}

# --- 2. Server ---
if (-not (Test-Server)) {
    $wasListening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($wasListening) { WLog "server HUNG (port held, heartbeat stale) - killing" }
    else { WLog "server DOWN (port not listening) - restarting" }
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$Repo\scripts\start_bridge.ps1`"" -WindowStyle Normal
    WLog "server restart launched, verifying..."
    $serverUp = $false
    for ($i = 1; $i -le 3; $i++) {
        Start-Sleep 20
        $portUp = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        if ($portUp) { $serverUp = $true; break }
        WLog "server restart attempt $i/3 port not yet listening"
    }
    if ($serverUp) { WLog "server RESTARTED and port listening" }
    else { WLog "server FAILED to restart after 3 attempts - retry next cycle" }
}
