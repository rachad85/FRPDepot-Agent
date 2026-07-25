@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: Dado's Python runtime is missing.
  pause
  exit /b 1
)
"%PY%" "C:\FRPDepot\Dado\Tools\woocommerce\woocommerce_common.py" connect
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Connection did not complete. No usable credentials were saved.
pause
exit /b %RC%
