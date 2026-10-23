<#
.SYNOPSIS
  Single admin console for the AlterEgo pc-mcp-bridge (replaces the eight
  one-off *.ps1 scripts: check_state, do-restart, docker_fix, fix_handler,
  persist_audit, restart-bridge, restart-server, restart2).

.USAGE
  .\bridge-admin.ps1 status          # server / ports / processes / services
  .\bridge-admin.ps1 audit           # persist_audit: full environment snapshot
  .\bridge-admin.ps1 restart         # stop python, start server (logs to restart.log)
  .\bridge-admin.ps1 restart -PermissionMode acceptEdits
  .\bridge-admin.ps1 fix-duplicates # kill duplicate handler procs (keeps first)
  .\bridge-admin.ps1 docker         # ensure Docker Desktop autostart + daemon up
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "audit", "restart", "fix-duplicates", "docker")]
    [string]$Command = "status",

    [ValidateSet("default", "plan", "acceptEdits", "dontAsk")]
    [string]$PermissionMode = "dontAsk"
)

$ErrorActionPreference = "SilentlyContinue"
$Repo = "$env:USERPROFILE\pc-mcp-bridge"
$Log = "$Repo\restart.log"

function Get-HandlerProcs {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like '*a2a_chozen1*' -and $_.Name -eq 'python.exe' }
}

function Show-Status {
    $p = Get-HandlerProcs
    "handler python procs: " + ($p | Measure-Object).Count
    $p | ForEach-Object { "  pid $($_.ProcessId) started $($_.CreationDate)" }
    $srv = (Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess
    if ($srv) {
        $sp = Get-Process -Id $srv
        "server pid $srv started $($sp.StartTime)"
    } else {
        "port 8765: NOT LISTENING"
    }
    $t = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cloudflared.exe' }
    "cloudflared procs: " + ($t | Measure-Object).Count
}

function Show-Audit {
    "--- services ---"
    foreach ($s in "cloudflared", "Tailscale", "TermService") {
        $svc = Get-Service $s -ErrorAction SilentlyContinue
        if ($svc) { "$s : $($svc.Status) start=$($svc.StartType)" }
        else { "$s : NOT A SERVICE" }
    }
    "--- processes/ports ---"
    foreach ($p in 8765, 8766, 3389, 8085, 55553) {
        $c = (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
              Measure-Object).Count
        "port $p listening=$c"
    }
    "a2a handler procs: " + ((Get-HandlerProcs | Measure-Object).Count)
    "--- startup bat ---"
    Get-Content "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\start-bridge.bat"
}

function Restart-Bridge {
    "START $(Get-Date)" | Out-File $Log
    Start-Sleep -Seconds 2
    Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
    "KILLED $(Get-Date)" | Out-File $Log -Append
    Start-Sleep -Seconds 3
    if ($PermissionMode -eq "dontAsk") {
        # Back-compat: FULL_ACCESS=1 is treated as dontAsk by the server.
        $env:PC_BRIDGE_FULL_ACCESS = "1"
        Remove-Item Env:\PC_BRIDGE_PERMISSION_MODE -ErrorAction SilentlyContinue
    } else {
        Remove-Item Env:\PC_BRIDGE_FULL_ACCESS -ErrorAction SilentlyContinue
        $env:PC_BRIDGE_PERMISSION_MODE = $PermissionMode
    }
    "STARTING SERVER (mode=$PermissionMode)" | Out-File $Log -Append
    Start-Process -FilePath "python" `
        -ArgumentList "pc-agent\server.py" `
        -WorkingDirectory $Repo `
        -WindowStyle Minimized `
        -RedirectStandardOutput "$Repo\server-out.log" `
        -RedirectStandardError "$Repo\server-err.log"
    "LAUNCHED $(Get-Date)" | Out-File $Log -Append
    "restarted in $PermissionMode mode — log: $Log"
}

function Fix-Duplicates {
    $ps = Get-HandlerProcs
    foreach ($p in $ps) {
        "pid $($p.ProcessId) parent $($p.ParentProcessId) exe $($p.ExecutablePath)"
    }
    $real = $ps | Where-Object {
        $_.ExecutablePath -like '*Python312*' -or $_.ExecutablePath -like '*Python3*'
    }
    if ((($ps | Measure-Object).Count -gt 1) -and (($real | Measure-Object).Count -gt 1)) {
        $kill = $real | Select-Object -Skip 1
        foreach ($k in $kill) {
            Stop-Process -Id $k.ProcessId -Force
            "killed duplicate $($k.ProcessId)"
        }
    } else {
        "no true duplicate (shim + child pair)"
    }
}

function Ensure-Docker {
    $val = '"C:\Program Files\Docker\Docker\Docker Desktop.exe"'
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
        -Name "DockerDesktop" -Value $val
    "run key: " + (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run").DockerDesktop
    for ($i = 0; $i -lt 24; $i++) {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { "daemon UP after ~$($i * 5)s"; break }
        Start-Sleep 5
    }
    docker ps -a --filter name=msf-rpc --format "{{.Names}} {{.Status}}"
}

switch ($Command) {
    "status"         { Show-Status }
    "audit"          { Show-Audit }
    "restart"        { Restart-Bridge }
    "fix-duplicates" { Fix-Duplicates }
    "docker"         { Ensure-Docker }
}
