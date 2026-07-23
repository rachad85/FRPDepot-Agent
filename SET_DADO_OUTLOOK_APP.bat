@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\outlook\outlook_tool.py" configure
if errorlevel 1 (
  echo.
  echo SETUP FAILED. Read the error above.
) else (
  echo.
  echo SETUP SAVED. Run CONNECT_DADO_OUTLOOK.bat next.
)
pause
endlocal
