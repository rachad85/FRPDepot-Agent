@echo off
title Stop Dado (FRP Depot)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "hermes -p dado gateway stop 2>$null | Out-Null; Start-Sleep 2; $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '-p dado gateway run' }; foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host 'Dado is stopped.'"
pause
