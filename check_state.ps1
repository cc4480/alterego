$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*a2a_chozen1*' -and $_.Name -eq 'python.exe' }
"handler python procs: " + ($p | Measure-Object).Count
$p | ForEach-Object { "  pid $($_.ProcessId) started $($_.CreationDate)" }
$srv = (Get-NetTCPConnection -LocalPort 8765 -State Listen).OwningProcess
$sp = Get-Process -Id $srv
"server pid $srv started $($sp.StartTime)"
