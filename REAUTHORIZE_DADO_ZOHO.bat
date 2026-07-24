@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\zoho\zoho_tool.py" reauthorize
if errorlevel 1 (
  echo.
  echo REAUTHORIZATION FAILED. Read the error above. The existing vault was not changed.
) else (
  echo.
  echo ZOHO REAUTHORIZED. Double-click CHECK_DADO_ZOHO.bat next.
)
pause
endlocal
