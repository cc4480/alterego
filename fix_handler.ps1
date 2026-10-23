$ps = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*a2a_chozen1*' -and $_.Name -eq 'python.exe' }
foreach ($p in $ps) { "pid $($p.ProcessId) parent $($p.ParentProcessId) exe $($p.ExecutablePath)" }
$real = $ps | Where-Object { $_.ExecutablePath -like '*Python312*' -or $_.ExecutablePath -like '*Python3*' }
if (($ps | Measure-Object).Count -gt 1 -and ($real | Measure-Object).Count -gt 1) {
    $kill = $real | Select-Object -Skip 1
    foreach ($k in $kill) { Stop-Process -Id $k.ProcessId -Force; "killed duplicate $($k.ProcessId)" }
} else { "no true duplicate (shim + child pair)" }
