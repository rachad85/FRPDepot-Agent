@echo off
setlocal
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "C:\FRPDepot\Dado\Tools\watch\job_runner.py" start --name woo-store-audit --note "FRP Depot authenticated WooCommerce audit" -- "%PY%" "C:\FRPDepot\Dado\Tools\woocommerce\woocommerce_audit_tool.py" store
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Store audit started. Dado will notify Rachad when it finishes.
pause
exit /b %RC%
