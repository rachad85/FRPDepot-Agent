@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
set "SESSION=%~dp0Dado\Tools\zoho\zoho_ui_session.py"
set "RUNNER=%~dp0Dado\Tools\watch\job_runner.py"

"%PY%" "%SESSION%" live-check >nul 2>&1
if not errorlevel 1 (
  echo ZOHO INVENTORY AND BOOKS ARE ALREADY CONNECTED IN DADO'S LIVE WINDOW.
  echo Keep the dedicated Edge window open while Dado is using Zoho UI access.
  pause
  exit /b 0
)

"%PY%" "%RUNNER%" start --name zoho-ui-live -- "%PY%" "%SESSION%" connect
if errorlevel 1 (
  echo.
  echo ZOHO LIVE WINDOW FAILED TO START. Read the error above.
  pause
  exit /b 1
)

echo.
echo DADO'S DEDICATED ZOHO WINDOW IS OPENING.
echo Sign in directly there. Never paste passwords, codes, tokens, or keys into chat.
echo Open both Zoho Inventory and Zoho Books, then KEEP THAT WINDOW OPEN.
pause
endlocal
