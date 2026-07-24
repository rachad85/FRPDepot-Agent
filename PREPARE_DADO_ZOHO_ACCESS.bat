@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\zoho\zoho_tool.py" scope-list --copy
if errorlevel 1 (
  echo.
  echo PREPARATION FAILED. Read the error above.
  pause
  exit /b 1
)
start "Zoho API Console" "https://api-console.zoho.com/"
start "Zoho access instructions" notepad.exe "%~dp0Dado\Tools\zoho\ZOHO_SETUP.txt"
echo.
echo SCOPE LIST COPIED. The Zoho API Console and numbered instructions are opening.
pause
endlocal
