@echo off
title Stop Dado (FRP Depot)
rem Sets the watchdog's disable flag FIRST, so the every-5-minute keep-alive does
rem not resurrect a gateway you deliberately shut down. START_DADO.bat clears it.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$flag='C:\FRPDepot\Dado\40_Logs\gateway_disabled.flag'; New-Item -ItemType Directory -Force -Path (Split-Path $flag) | Out-Null; Set-Content -Path $flag -Value ('stopped by STOP_DADO.bat ' + (Get-Date).ToString('s')) -Encoding utf8; hermes -p dado gateway stop 2>$null | Out-Null; Start-Sleep 2; $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(-p|--profile) dado gateway run' }; foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host 'Dado is stopped, and the keep-alive will leave her stopped until you run START_DADO.bat.'"
pause
