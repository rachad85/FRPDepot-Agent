@echo off
setlocal
start "Discord Developer Portal" "https://discord.com/developers/applications"
start "Discord setup instructions" notepad.exe "%~dp0Dado\Tools\discord\DISCORD_SETUP.txt"
endlocal
