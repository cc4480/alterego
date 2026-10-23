$val = '"C:\Program Files\Docker\Docker\Docker Desktop.exe"'
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "DockerDesktop" -Value $val
"run key: " + (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run").DockerDesktop
for ($i = 0; $i -lt 24; $i++) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { "daemon UP after ~$($i*5)s"; break }
    Start-Sleep 5
}
docker ps -a --filter name=msf-rpc --format "{{.Names}} {{.Status}}"
