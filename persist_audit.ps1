"--- services ---"
foreach ($s in "cloudflared","Tailscale","TermService") {
    $svc = Get-Service $s -ErrorAction SilentlyContinue
    if ($svc) { "$s : $($svc.Status) start=$($svc.StartType)" } else { "$s : NOT A SERVICE" }
}
"--- processes/ports ---"
foreach ($p in 8765,8766,3389,8085,55553) {
    $c = (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
    "port $p listening=$c"
}
$h = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*a2a_chozen1*' -and $_.Name -eq 'python.exe' }
"a2a handler procs: " + ($h | Measure-Object).Count
$t = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cloudflared.exe' }
"cloudflared procs: " + ($t | Measure-Object).Count
"--- startup bat ---"
Get-Content "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\start-bridge.bat"
