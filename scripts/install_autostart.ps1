# Installs Windows Scheduled Tasks so the bridge survives reboots.
# Run once from an elevated PowerShell (approval dialog will appear).
#
#   PCBridgeServer — at user logon, interactive session (approval dialogs
#                    visible), visible console so the pairing code is readable.
#   PCBridgeTunnel — at system startup, headless cloudflared named tunnel.
# Both restart automatically on failure.

$ErrorActionPreference = "Stop"

$Repo       = "C:\Users\celos\.copilot\chats\2026-10-04\stunning-waffle-8502e108\pc-mcp-bridge"
$Cloudflared = "C:\Users\celos\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe\cloudflared.exe"
$User        = "$env:USERDOMAIN\$env:USERNAME"

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

# --- PCBridgeTunnel: startup trigger, headless ---
# Runs as SYSTEM with an explicit --config (absolute paths inside config.yml,
# so the user profile doesn't need to resolve).
$tunnelAction = New-ScheduledTaskAction -Execute $Cloudflared `
    -Argument "tunnel --config `"C:\Users\celos\.cloudflared\config.yml`" run pc-bridge" `
    -WorkingDirectory "C:\Users\celos\.cloudflared"
$tunnelTrigger = New-ScheduledTaskTrigger -AtStartup
$tunnelSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$tunnelPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "PCBridgeTunnel" -Action $tunnelAction `
    -Trigger $tunnelTrigger -Settings $tunnelSettings `
    -Principal $tunnelPrincipal -Force | Out-Null
Write-Host "PCBridgeTunnel task installed (at startup, SYSTEM)."

Write-Host "Done. Both tasks will start on next boot; server starts at your next logon."
