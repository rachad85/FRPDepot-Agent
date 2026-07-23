@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\zoho\zoho_tool.py" check
if errorlevel 1 (
  echo.
  echo CHECK FAILED. Read the error above.
) else (
  echo.
  echo CHECK PASSED: BOOKS CUSTOMER/DRAFT QUOTE AND INVENTORY ITEM CATALOG ACCESS VERIFIED.
)
pause
endlocal
