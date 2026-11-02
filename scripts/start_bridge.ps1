# Auto-start wrapper for the pc-mcp-bridge server.
# Launched by the PCBridgeServer scheduled task at user logon.
# Runs in the user's interactive session so approval dialogs are visible.

$ErrorActionPreference = "Stop"
$Repo = "C:\Users\celos\.copilot\chats\2026-10-04\stunning-waffle-8502e108\pc-mcp-bridge"

# Set the environment BEFORE the port check. These variables are read
# once when the server imports its approval module, so they must be in
# place before any launch path runs. An ambient
# PC_BRIDGE_PERMISSION_MODE would shadow PC_BRIDGE_FULL_ACCESS in the
# mode resolution, so clear it. (No FULL_ACCESS_CONFIRM here: the
# startup confirmation stays in place for normal operation.)
$env:PC_BRIDGE_FULL_ACCESS = "1"
Remove-Item Env:\PC_BRIDGE_PERMISSION_MODE -ErrorAction SilentlyContinue

# Don't stack servers: if the old server is still listening on
# 127.0.0.1:8765, stop it and relaunch instead of silently leaving the
# old process (and its posture) in place.
# Only a listener on 127.0.0.1 counts: a listener on any other address
# (for example tailscaled forwarding on a tailnet address) is not this
# server and must not block the launch.
$inUse = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    $ownerIds = $inUse | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($ownerId in $ownerIds) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerId" -ErrorAction SilentlyContinue
        if ($owner -and $owner.Name -like "python*" -and $owner.CommandLine -like "*pc-agent\server.py*") {
            Write-Host "Bridge already listening on 127.0.0.1:8765 (PID $ownerId) - stopping it to relaunch."
            Stop-Process -Id $ownerId -Force -ErrorAction SilentlyContinue
        } else {
            $ownerName = "unknown"
            if ($owner) { $ownerName = $owner.Name }
            Write-Host "ERROR: 127.0.0.1:8765 is already in use by $ownerName (PID $ownerId), not the python bridge server. Not killing a foreign process. Free the port and run this script again."
            exit 1
        }
    }
    Start-Sleep -Milliseconds 500
}

# Kill any orphaned server.py from a previous session that isn't listening.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*pc-agent\server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Set-Location $Repo
& python pc-agent\server.py
