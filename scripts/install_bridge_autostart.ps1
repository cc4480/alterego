# Installs persistence for the pc-mcp-bridge: a per-user logon task
# that starts the bridge in showcase (dontAsk) mode at every sign-in.
#
# Why a logon task and not a Windows service: the bridge's screenshot,
# UIA and window tools must run inside the user's interactive session;
# a service runs in session 0 with no desktop and those tools break.
# The task restarts the bridge on failure (3 tries, 1 minute apart)
# and re-runs the showcase launcher, which is single-instance safe
# (it stops an existing listener and relaunches in showcase mode).
#
# Run once, from any PowerShell in the repo:
#   powershell -ExecutionPolicy Bypass -File scripts\install_bridge_autostart.ps1
# Remove with:
#   Unregister-ScheduledTask -TaskName 'AlterEgo Bridge (showcase)' -Confirm:$false

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $Repo 'scripts\start_bridge_showcase.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`"" `
    -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName 'AlterEgo Bridge (showcase)' `
    -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null

Write-Host 'Installed: "AlterEgo Bridge (showcase)" now starts at every logon, in showcase mode.'
