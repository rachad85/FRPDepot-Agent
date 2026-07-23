@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\zoho\zoho_tool.py" connect
if errorlevel 1 (
  echo.
  echo CONNECTION FAILED. Read the error above.
) else (
  echo.
  echo ZOHO CONNECTED WITH THE RESTRICTED NAMED WRITE TOOLS.
)
pause
endlocal
