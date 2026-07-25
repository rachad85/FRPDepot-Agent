@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\google\google_extended_auth.py" check
if errorlevel 1 (
  echo.
  echo CHECK FAILED. Read the exact blocker above.
) else (
  echo.
  echo CHECK PASSED. ALL FOUR SERVICES ARE READ-ONLY AND LIVE.
)
pause
endlocal
