@echo off
title Start Dodo (FRP Depot)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; try { $c=New-Object Net.Sockets.TcpClient('127.0.0.1',8647); $ok=$c.Connected; $c.Close() } catch {}; if ($ok) { Write-Host 'Dodo is already running (port 8647).' } else { Start-Process -WindowStyle Hidden cmd -ArgumentList '/c','hermes -p dodo gateway run'; Write-Host 'Dodo is starting... give her about 15 seconds.' }"
pause
