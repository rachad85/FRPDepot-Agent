@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\google\google_extended_auth.py" connect
if errorlevel 1 (
  echo.
  echo AUTHORIZATION WAS SAVED IF THE BROWSER SAID SUCCESS.
  echo One or more Google APIs may still need enabling. Read the check above.
) else (
  echo.
  echo ALL FOUR READ-ONLY GOOGLE SERVICES ARE CONNECTED AND VERIFIED.
)
pause
endlocal
