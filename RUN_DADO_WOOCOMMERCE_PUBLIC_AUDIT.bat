@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "C:\FRPDepot\Dado\Tools\watch\job_runner.py" start --name woo-public-audit --note "FRP Depot public website crawl; no credentials" -- "%PY%" "C:\FRPDepot\Dado\Tools\woocommerce\woocommerce_audit_tool.py" public
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Public audit started. Dado will notify Rachad when it finishes.
pause
exit /b %RC%
