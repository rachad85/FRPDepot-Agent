@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo ERROR: Dado's Python runtime is missing.
  pause
  exit /b 1
)
"%PY%" "C:\FRPDepot\Dado\Tools\woocommerce\woocommerce_common.py" check
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
