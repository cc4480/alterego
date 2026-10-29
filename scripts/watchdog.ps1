# PCBridge watchdog: keeps the tunnel and server alive.
# Runs every 5 minutes via the PCBridgeWatchdog scheduled task.
# Pure ASCII - PowerShell 5.1 safe.
#
# Design rules (fixed 2026-10-26, supervisor-watchdog build):
# - ALL paths are derived from $env:USERPROFILE. No machine-specific
#   absolute path is hardcoded anywhere in this script.
#   Repo = $env:USERPROFILE\alterego, falling back to
#   $env:USERPROFILE\pc-mcp-bridge when the first does not exist.
# - The tunnel is restarted in config-file mode ONLY:
#     cloudflared tunnel --config $env:USERPROFILE\.cloudflared\config.yml run
#   No tunnel name is hardcoded and token mode is never used.
# - Restarts prefer the PCBridgeServer / PCBridgeTunnel scheduled tasks
#   when those tasks exist; otherwise the component is started directly.
# - A failure must be confirmed by 3 probes about 20 seconds apart
#   before anything is killed or restarted. A single blip, and elapsed
#   time alone, never trips a restart.
# - Storm breaker: restarts are timestamped in a state file. If 5 or
#   more restarts happened within the last 15 minutes, the watchdog
#   HOLDs: it logs the verdict and restarts nothing this cycle.
# - -DryRun logs every intended action and kills/starts nothing.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File watchdog.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File watchdog.ps1 -DryRun

[CmdletBinding()]
param(
    [switch]$DryRun,
    [int]$ProbeCount = 3,
    [int]$ProbeIntervalSeconds = 20
)

$ErrorActionPreference = "SilentlyContinue"

# --- Derived paths (per-machine, never hardcoded) ---
$RepoPrimary = "$env:USERPROFILE\alterego"
$RepoFallback = "$env:USERPROFILE\pc-mcp-bridge"
if (Test-Path $RepoPrimary) {
    $Repo = $RepoPrimary
} elseif (Test-Path $RepoFallback) {
    $Repo = $RepoFallback
} else {
    $Repo = $RepoPrimary
}
$ConfigYml = "$env:USERPROFILE\.cloudflared\config.yml"
$LogDir = "$env:APPDATA\pc-mcp-bridge"
$Log = "$LogDir\watchdog.log"
$StateFile = "$LogDir\watchdog-state.txt"
$HeartbeatFile = "$LogDir\heartbeat.txt"
$StormWindowSeconds = 900
$StormMaxRestarts = 5

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory $LogDir -Force | Out-Null }

function WLog($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Out-File $Log -Append -Encoding ascii
    Write-Host "$ts $msg"
}

function Resolve-Cloudflared() {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $winget = "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
    if (Test-Path $winget) { return $winget }
    return "cloudflared"
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
    if (-not (Test-Path $HeartbeatFile)) { return $false }
    try {
        $hbTime = [int64](Get-Content $HeartbeatFile -Raw).Trim()
        $ageSec = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $hbTime
        return ($ageSec -lt 180)
    } catch { return $false }
}

# Probe a component $ProbeCount times, about $ProbeIntervalSeconds
# apart. Returns $true only when EVERY probe failed. Every failed
# probe is logged. The first probe runs immediately; a sleep happens
# only between probes, never before the first one.
function Confirm-Failure($Name, [scriptblock]$Probe) {
    for ($i = 1; $i -le $ProbeCount; $i++) {
        if (& $Probe) {
            if ($i -gt 1) { WLog "$Name probe $i/$ProbeCount OK - earlier failure was a blip, no action" }
            return $false
        }
        WLog "$Name probe $i/$ProbeCount FAILED"
        if ($i -lt $ProbeCount) { Start-Sleep -Seconds $ProbeIntervalSeconds }
    }
    WLog "$Name failure CONFIRMED after $ProbeCount probes - trip"
    return $true
}

# --- Storm breaker state ---
# State file holds one unix timestamp per restart, newest last.
function Get-RestartHistory() {
    if (-not (Test-Path $StateFile)) { return @() }
    $cutoff = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $StormWindowSeconds
    $kept = @()
    foreach ($line in (Get-Content $StateFile -ErrorAction SilentlyContinue)) {
        $t = [int64]0
        if ([int64]::TryParse($line.Trim(), [ref]$t) -and $t -ge $cutoff) { $kept += $t }
    }
    return $kept
}

function Test-StormBreaker() {
    $history = @(Get-RestartHistory)
    return ($history.Count -ge $StormMaxRestarts)
}

function Record-Restart($What) {
    if ($DryRun) {
        WLog "DRYRUN would record restart of $What in storm-breaker state ($StateFile)"
        return
    }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    "$now" | Out-File $StateFile -Append -Encoding ascii
    WLog "storm-breaker: recorded restart of $What (state: $StateFile)"
}

function Test-TaskExists($Name) {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    return ($null -ne $task)
}

# --- Restart helpers ---
function Restart-Tunnel() {
    $cloudflared = Resolve-Cloudflared
    $tp = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($DryRun) {
        if ($tp) { WLog "DRYRUN would kill hung cloudflared process (PID $($tp.Id -join ','))" }
        if (Test-TaskExists "PCBridgeTunnel") {
            WLog "DRYRUN would run: Start-ScheduledTask -TaskName PCBridgeTunnel"
        } else {
            WLog "DRYRUN would run: Start-Process `"$cloudflared`" -ArgumentList `"tunnel --config $ConfigYml run`" -WindowStyle Hidden"
        }
        Record-Restart "tunnel"
        WLog "DRYRUN tunnel restart intended, verifying step skipped (nothing was started)"
        return
    }
    if ($tp) {
        WLog "tunnel process alive but 0 connections - killing"
        $tp | Stop-Process -Force
        Start-Sleep 3
    } else {
        WLog "tunnel DOWN - restarting"
    }
    if (Test-TaskExists "PCBridgeTunnel") {
        Stop-ScheduledTask -TaskName "PCBridgeTunnel" -ErrorAction SilentlyContinue
        Start-Sleep 2
        Start-ScheduledTask -TaskName "PCBridgeTunnel"
        WLog "tunnel restart launched via scheduled task PCBridgeTunnel, verifying reconnect..."
    } else {
        Start-Process $cloudflared -ArgumentList "tunnel --config `"$ConfigYml`" run" -WindowStyle Hidden
        WLog "tunnel restart launched directly (config-file mode), verifying reconnect..."
    }
    Record-Restart "tunnel"
    $reconnected = $false
    for ($i = 1; $i -le 3; $i++) {
        Start-Sleep 15
        if (Test-Tunnel) { $reconnected = $true; break }
        WLog "tunnel reconnect attempt $i/3 not yet connected"
    }
    if ($reconnected) { WLog "tunnel RECONNECTED and verified" }
    else { WLog "tunnel FAILED to reconnect after 3 attempts - retry next cycle" }
}

function Restart-Server() {
    $wasListening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($DryRun) {
        if ($wasListening) { WLog "DRYRUN server HUNG (port held, heartbeat stale) - would kill server process" }
        else { WLog "DRYRUN server DOWN (port not listening) - would restart" }
        WLog "DRYRUN would kill python.exe processes running pc-agent\server.py"
        if (Test-TaskExists "PCBridgeServer") {
            WLog "DRYRUN would run: Start-ScheduledTask -TaskName PCBridgeServer"
        } else {
            WLog "DRYRUN would run: Start-Process powershell -ArgumentList `"-NoProfile -ExecutionPolicy Bypass -File $Repo\scripts\start_bridge.ps1`" -WindowStyle Normal"
        }
        Record-Restart "server"
        WLog "DRYRUN server restart intended, verifying step skipped (nothing was started)"
        return
    }
    if ($wasListening) { WLog "server HUNG (port held, heartbeat stale) - killing" }
    else { WLog "server DOWN (port not listening) - restarting" }
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    if (Test-TaskExists "PCBridgeServer") {
        Stop-ScheduledTask -TaskName "PCBridgeServer" -ErrorAction SilentlyContinue
        Start-Sleep 2
        Start-ScheduledTask -TaskName "PCBridgeServer"
        WLog "server restart launched via scheduled task PCBridgeServer, verifying..."
    } else {
        Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$Repo\scripts\start_bridge.ps1`"" -WindowStyle Normal
        WLog "server restart launched directly via start_bridge.ps1, verifying..."
    }
    Record-Restart "server"
    $serverUp = $false
    for ($i = 1; $i -le 3; $i++) {
        Start-Sleep 20
        if (Test-Server) { $serverUp = $true; break }
        $portUp = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        if ($portUp) { WLog "server restart attempt $i/3 port listening, heartbeat not fresh yet" }
        else { WLog "server restart attempt $i/3 port not yet listening" }
    }
    if ($serverUp) { WLog "server RESTARTED and verified (port listening, heartbeat fresh)" }
    else { WLog "server FAILED to restart after 3 attempts - retry next cycle" }
}

# --- Main ---
if ($DryRun) { WLog "watchdog cycle start (DRYRUN - no kills, no starts) repo=$Repo" }
else { WLog "watchdog cycle start repo=$Repo" }

if (Test-StormBreaker) {
    $count = @(Get-RestartHistory).Count
    WLog "HOLD storm breaker: $count restarts within the last 15 minutes (>= $StormMaxRestarts) - restarting NOTHING this cycle"
    WLog "watchdog cycle end (HOLD)"
    return
}

# --- 1. Tunnel ---
if (-not (Test-Tunnel)) {
    WLog "tunnel initial probe FAILED - starting confirmation probes"
    if (Confirm-Failure "tunnel" { Test-Tunnel }) {
        if (Test-StormBreaker) {
            WLog "HOLD storm breaker tripped before tunnel restart - restarting NOTHING this cycle"
        } else {
            Restart-Tunnel
        }
    }
} else {
    WLog "tunnel OK (metrics ha_connections > 0)"
}

# --- 2. Server ---
if (-not (Test-Server)) {
    WLog "server initial probe FAILED - starting confirmation probes"
    if (Confirm-Failure "server" { Test-Server }) {
        if (Test-StormBreaker) {
            WLog "HOLD storm breaker tripped before server restart - restarting NOTHING this cycle"
        } else {
            Restart-Server
        }
    }
} else {
    WLog "server OK (port 8765 listening, heartbeat fresh)"
}

WLog "watchdog cycle end"
