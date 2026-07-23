@echo off
title Start Dado (FRP Depot)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; try { $c=New-Object Net.Sockets.TcpClient('127.0.0.1',8647); $ok=$c.Connected; $c.Close() } catch {}; if ($ok) { Write-Host 'Dado is already running (port 8647).' } else { Start-Process -WindowStyle Hidden cmd -ArgumentList '/c','hermes -p dado gateway run'; Write-Host 'Dado is starting... give her about 15 seconds.' }"
pause
