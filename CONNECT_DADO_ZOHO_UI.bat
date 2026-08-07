@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\zoho\zoho_ui_session.py" connect
if errorlevel 1 (
  echo.
  echo ZOHO UI CONNECTION FAILED. Read the error above.
) else (
  echo.
  echo ZOHO INVENTORY UI SESSION CONNECTED. NO BUSINESS RECORD WAS CHANGED.
)
pause
endlocal
