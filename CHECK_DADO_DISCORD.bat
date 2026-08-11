@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\discord\dado_discord_check.py"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo CHECK PASSED.
) else if "%RC%"=="2" (
  echo CHECK INCOMPLETE - read the [WARN] lines above. This is NOT a pass:
  echo something could not be confirmed, usually because she is not running yet.
) else (
  echo CHECK FAILED. Read the problems above.
)
pause
endlocal
