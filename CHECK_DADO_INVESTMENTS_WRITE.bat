@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\google\google_investments_tool.py" check
if errorlevel 1 (
  echo.
  echo INVESTMENTS WRITE CHECK FAILED. Read the error above.
) else (
  echo.
  echo INVESTMENTS WRITE ACCESS VERIFIED. NO FILE WAS CHANGED.
)
pause
endlocal
