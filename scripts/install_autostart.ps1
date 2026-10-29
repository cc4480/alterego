# Installs Windows Scheduled Tasks so the bridge survives reboots.
# Run once from an elevated PowerShell (approval dialog will appear).
# Pure ASCII - PowerShell 5.1 safe.
#
#   PCBridgeServer   - at user logon, interactive session (approval dialogs
#                      visible), visible console so the pairing code is readable.
#   PCBridgeTunnel   - at user logon, headless cloudflared in config-file
#                      mode: tunnel --config <user>\.cloudflared\config.yml run
#                      (no tunnel name is hardcoded, token mode is never used).
#   PCBridgeWatchdog - repeats every 5 minutes, hidden window, runs
#                      scripts\watchdog.ps1 from the derived repo path.
# All tasks restart automatically on failure.
# Note: all use HighestAvailable, so they only enumerate from an
# elevated shell - this is expected, not a problem.
#
# Every path below is derived from $env:USERPROFILE on the machine
# running this script:
#   Repo = $env:USERPROFILE\alterego, falling back to
#          $env:USERPROFILE\pc-mcp-bridge when the first does not exist.

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
$ConfigYml = "$env:USERPROFILE\.cloudflared\config.yml"
$CloudflaredCmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($CloudflaredCmd) {
    $Cloudflared = $CloudflaredCmd.Source
} else {
    $Cloudflared = "$env:USERPROFILE\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
}
$User = "$env:USERDOMAIN\$env:USERNAME"

# --- PCBridgeServer: logon trigger, interactive ---
$serverAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$Repo\scripts\start_bridge.ps1`"")
$serverTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$serverSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$serverPrincipal = New-ScheduledTaskPrincipal -UserId $User `
    -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "PCBridgeServer" -Action $serverAction `
    -Trigger $serverTrigger -Settings $serverSettings `
    -Principal $serverPrincipal -Force | Out-Null
Write-Host "PCBridgeServer task installed (at logon, interactive)."

# --- PCBridgeTunnel: logon trigger, headless, config-file mode ---
# Runs as the user at logon (same session as the server). Headless - no
# console window. cloudflared is in the user's WinGet package dir, so the
# task runs in his context where that path resolves.
$tunnelAction = New-ScheduledTaskAction -Execute $Cloudflared `
    -Argument "tunnel --config `"$ConfigYml`" run" `
    -WorkingDirectory "$env:USERPROFILE\.cloudflared"
$tunnelTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$tunnelSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$tunnelPrincipal = New-ScheduledTaskPrincipal -UserId $User `
    -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "PCBridgeTunnel" -Action $tunnelAction `
    -Trigger $tunnelTrigger -Settings $tunnelSettings `
    -Principal $tunnelPrincipal -Force | Out-Null
Write-Host "PCBridgeTunnel task installed (at logon, headless, config-file mode)."

# --- PCBridgeWatchdog: every 5 minutes, hidden window ---
# The repetition trigger starts one minute from now and repeats every
# 5 minutes indefinitely. The action itself launches PowerShell hidden,
# and the task's Hidden flag is set after registration so no window or
# task entry draws attention during normal use.
$watchdogAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Repo\scripts\watchdog.ps1`"")
$watchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration ([TimeSpan]::MaxValue)
$watchdogSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$watchdogPrincipal = New-ScheduledTaskPrincipal -UserId $User `
    -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "PCBridgeWatchdog" -Action $watchdogAction `
    -Trigger $watchdogTrigger -Settings $watchdogSettings `
    -Principal $watchdogPrincipal -Force | Out-Null
try {
    $watchdogTask = Get-ScheduledTask -TaskName "PCBridgeWatchdog"
    $watchdogTask.Settings.Hidden = $true
    Set-ScheduledTask -InputObject $watchdogTask | Out-Null
} catch {
    Write-Host "PCBridgeWatchdog installed; could not set Hidden flag: $($_.Exception.Message)"
}
Write-Host "PCBridgeWatchdog task installed (every 5 minutes, hidden window)."

Write-Host "Done. All tasks will start on next boot; server starts at your next logon, watchdog repeats every 5 minutes."
