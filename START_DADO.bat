@echo off
title Start Dado (FRP Depot)
rem Clears the watchdog's disable flag first. STOP_DADO.bat sets that flag, so
rem without clearing it here a hand-start would leave Dado running with no
rem keep-alive - unprotected against exactly the silent death of 2026-07-25.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$flag='C:\FRPDepot\Dado\40_Logs\gateway_disabled.flag'; if (Test-Path $flag) { Add-Content -Path 'C:\FRPDepot\Dado\40_Logs\gateway_stops.log' -Value ((Get-Date).ToString('s') + ' disable flag cleared by START_DADO.bat') -Encoding utf8; Remove-Item $flag -Force -ErrorAction SilentlyContinue; Write-Host 'Keep-alive re-enabled.' }; $ok=$false; try { $c=New-Object Net.Sockets.TcpClient('127.0.0.1',8647); $ok=$c.Connected; $c.Close() } catch {}; if ($ok) { Write-Host 'Dado is already running (port 8647).' } else { Start-Process -WindowStyle Hidden cmd -ArgumentList '/c','hermes -p dado gateway run'; Write-Host 'Dado is starting... give her about 15 seconds.' }"
pause
