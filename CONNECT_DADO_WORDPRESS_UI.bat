@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
set "SESSION=%~dp0Dado\Tools\woocommerce\wordpress_ui_session.py"
set "RUNNER=%~dp0Dado\Tools\watch\job_runner.py"

"%PY%" "%SESSION%" live-check >nul 2>&1
if not errorlevel 1 (
  echo WORDPRESS IS ALREADY CONNECTED IN DADO'S DEDICATED WINDOW.
  echo Keep that Edge window open while Dado uses approved WordPress access.
  pause
  exit /b 0
)

"%PY%" "%RUNNER%" start --service --name wordpress-ui-live -- "%PY%" "%SESSION%" connect
if errorlevel 1 (
  echo.
  echo WORDPRESS LIVE WINDOW FAILED TO START. Read the error above.
  pause
  exit /b 1
)

echo.
echo DADO'S DEDICATED WORDPRESS WINDOW IS OPENING.
echo Sign in directly there. Never paste passwords, codes, tokens, or keys into chat.
echo Open the WordPress Dashboard, then KEEP THAT EDGE WINDOW OPEN.
pause
endlocal
