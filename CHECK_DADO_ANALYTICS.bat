@echo off
setlocal
rem Lists the Google Analytics accounts and properties Dado can read.
rem READ-ONLY. TDI marketing analytics were opened to Dado 2026-07-24;
rem TDI files, mailbox, Drive and Zoho remain walled off.
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\google\analytics_tool.py" list
if errorlevel 1 (
  echo.
  echo CHECK FAILED. Read the error above.
) else (
  echo.
  echo CHECK PASSED.
)
pause
endlocal
