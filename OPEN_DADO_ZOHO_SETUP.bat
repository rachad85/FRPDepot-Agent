@echo off
setlocal
start "Zoho API Console" "https://api-console.zoho.com/"
start "Zoho setup instructions" notepad.exe "%~dp0Dado\Tools\zoho\ZOHO_SETUP.txt"
endlocal
