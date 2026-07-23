@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\outlook\outlook_tool.py" check
if errorlevel 1 (
  echo.
  echo CHECK FAILED. Read the error above.
) else (
  echo.
  echo CHECK PASSED.
)
pause
endlocal
