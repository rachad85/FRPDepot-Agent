@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\zoho\zoho_tool.py" check
if errorlevel 1 (
  echo.
  echo CHECK FAILED. Read the error above.
) else (
  echo.
  echo CHECK PASSED: INVENTORY READ-ONLY; BOOKS CUSTOMER AND DRAFT QUOTE CREATE ONLY.
)
pause
endlocal
