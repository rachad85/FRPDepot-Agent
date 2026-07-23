@echo off
setlocal
start "Microsoft Entra" "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
start "Outlook setup instructions" notepad.exe "%~dp0Dado\Tools\outlook\OUTLOOK_APP_SETUP.txt"
endlocal
