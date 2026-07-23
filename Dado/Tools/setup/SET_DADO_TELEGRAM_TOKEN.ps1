# SET_DADO_TELEGRAM_TOKEN.ps1 — store Dado's Telegram bot token on THIS PC only.
# Saves to a local vault AND into Dado's Hermes profile .env. The token is
# never uploaded to GitHub, never shown on screen, never pasted in chat.

$ErrorActionPreference = "Stop"

$vaultDir = Join-Path $env:LOCALAPPDATA "FRPDepot-Dado"
New-Item -ItemType Directory -Force -Path $vaultDir | Out-Null
$vault = Join-Path $vaultDir "telegram.env"
$profileEnv = Join-Path $env:LOCALAPPDATA "hermes\profiles\dado\.env"

Write-Host "==============================================================="
Write-Host "  Set Dado's Telegram bot token  (THIS PC only)"
Write-Host "==============================================================="
Write-Host ""
Write-Host "Where to get the token: open Telegram, message @BotFather, send"
Write-Host "  /mybots  ->  pick Dado's bot  ->  API Token."
Write-Host ""
Write-Host "It is saved only on this PC. It is never uploaded and never shown."
Write-Host ""

$sec = Read-Host "Paste Dado's Telegram BOT TOKEN, then press Enter" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$tok = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

if (-not $tok -or $tok.Trim().Length -lt 20 -or $tok -notmatch ':') {
  Write-Host ""
  Write-Host "That does not look like a bot token (expected something like"
  Write-Host "1234567890:AA...). Nothing was saved. Run this again to retry."
  exit 1
}
$tok = $tok.Trim()

if (-not (Test-Path $profileEnv)) {
  Write-Host "Dado's profile was not found on this PC. Nothing was saved."
  exit 1
}

# 1) Vault copy (local backup, outside any repo)
$text = "TELEGRAM_BOT_TOKEN=$tok`r`nTELEGRAM_ALLOWED_USERS=891365639`r`n"
[System.IO.File]::WriteAllText($vault, $text, (New-Object System.Text.UTF8Encoding($false)))

# 2) Inject into Dado's profile .env (replace-or-append, ASCII only)
$lines = @(Get-Content $profileEnv -ErrorAction SilentlyContinue) | Where-Object {
  $_ -notmatch '^\s*TELEGRAM_BOT_TOKEN\s*=' -and $_ -notmatch '^\s*TELEGRAM_ALLOWED_USERS\s*='
}
$lines += "TELEGRAM_BOT_TOKEN=$tok"
$lines += "TELEGRAM_ALLOWED_USERS=891365639"
[System.IO.File]::WriteAllText($profileEnv, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Saved. Only your Telegram account (891365639) can talk to Dado."
Write-Host ""
Write-Host "NEXT STEP: double-click START_DADO.bat (or STOP then START if she"
Write-Host "was already running). Then send her bot a message from your phone."
