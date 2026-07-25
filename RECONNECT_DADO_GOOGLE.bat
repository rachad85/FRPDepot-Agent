@echo off
setlocal
rem FORCES a brand-new Gmail/Drive sign-in, even when the stored one still works.
rem
rem Use this - not CONNECT - whenever a NEW token is the point rather than a
rem merely working one. CONNECT returns early on a healthy token and never opens
rem a browser, so it cannot replace a token that needs replacing (for example one
rem minted before the OAuth app moved to "In production" on 2026-07-24, which
rem kept its old 7-day expiry).
rem
rem A BROWSER MUST OPEN and ask you to pick your account. If no browser opens,
rem nothing was replaced.
title FRP Depot - Force a new Google (Gmail + Drive) sign-in
echo Forcing a brand-new Gmail/Drive sign-in...
echo A browser will open. Pick your account and tick every box.
echo.
set "PY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe"
"%PY%" "%~dp0Dado\Tools\google\google_tool.py" reconnect
if errorlevel 1 (
  echo.
  echo RECONNECT FAILED. Read the error above. Your previous sign-in may still work.
) else (
  echo.
  echo NEW GMAIL/DRIVE SIGN-IN SAVED AND VERIFIED.
)
pause
endlocal
