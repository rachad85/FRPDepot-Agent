# SET_DADO_DISCORD_TOKEN.ps1 - store Dado's OWN Discord bot token on THIS PC only.
# Saves to a local vault AND into Dado's Hermes profile .env. The token is never
# uploaded to GitHub, never shown on screen, never pasted in chat.
#
# WHY THE REFUSAL BELOW EXISTS (2026-08-04). Dado's gateway once came up on
# Discord as "Aze#1753" - Troy Dualam's bot - because her profile .env had NO
# DISCORD_BOT_TOKEN and hermes deliberately does not scrub credential keys from
# the process environment. She then served Rachad's DMs through TDI's identity
# into an FRP Depot session. This script refuses outright to save a token that
# another hermes profile already holds, because the gateway lock is keyed by
# sha256(token) and is machine-local - so a match is a POSITIVE identification
# of a shared bot, not a guess.
#
# PROACTIVE MESSAGES DELIBERATELY STAY ON TELEGRAM. No DISCORD_HOME_CHANNEL is
# written. Her crons (inbox watch, follow-up digest, job watch, conduct review)
# keep delivering to Telegram exactly as they do today - Discord is a lane she
# ANSWERS on, not a second place her alerts get duplicated to.

$ErrorActionPreference = "Stop"

$vaultDir   = Join-Path $env:LOCALAPPDATA "FRPDepot-Dado"
$vault      = Join-Path $vaultDir "discord.env"
$profileEnv = Join-Path $env:LOCALAPPDATA "hermes\profiles\dado\.env"
$lockDir    = Join-Path $env:USERPROFILE ".local\state\hermes\gateway-locks"

Write-Host "==============================================================="
Write-Host "  Set Dado's OWN Discord bot token  (THIS PC only)"
Write-Host "==============================================================="
Write-Host ""
Write-Host "This must be an FRP DEPOT Discord application that YOU created."
Write-Host "Do NOT reuse Troy Dualam's bot - this script will refuse it."
Write-Host ""
Write-Host "Where to get it: https://discord.com/developers/applications"
Write-Host "  -> your FRP Depot app -> Bot -> Reset Token -> Copy."
Write-Host ""
Write-Host "It is saved only on this PC. It is never uploaded and never shown."
Write-Host ""

if (-not (Test-Path $profileEnv)) {
  Write-Host "Dado's profile was not found on this PC. Nothing was saved."
  exit 1
}

# ---------------------------------------------------------------- token ----
$sec  = Read-Host "Paste Dado's DISCORD BOT TOKEN, then press Enter" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$tok  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

if ($tok) { $tok = $tok.Trim() }

if (-not $tok -or $tok.Length -lt 50 -or ($tok.ToCharArray() | Where-Object { $_ -eq '.' }).Count -lt 2 -or $tok -match '\s') {
  Write-Host ""
  Write-Host "That does not look like a Discord bot token. They are ~70 characters"
  Write-Host "with two dots and no spaces (for example MTM4...xyz.Gh1jK2.abc...)."
  Write-Host "Nothing was saved. Run this again to retry."
  Write-Host ""
  Write-Host "TIP: the Developer Portal shows the token ONCE. If you missed it,"
  Write-Host "     click Reset Token and copy the new one."
  exit 1
}

if ($tok.StartsWith("Bot ")) {
  Write-Host ""
  Write-Host "Paste the token only - drop the leading 'Bot ' prefix. Nothing was saved."
  exit 1
}

# --------------------------------------------- refuse another profile's bot ----
$sha   = [System.Security.Cryptography.SHA256]::Create()
$bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($tok))
$sha.Dispose()
$hash  = -join ($bytes | ForEach-Object { $_.ToString("x2") })
$hash  = $hash.Substring(0, 16)
$lock  = Join-Path $lockDir "discord-bot-token-$hash.lock"

if (Test-Path $lock) {
  $owner = $null
  try {
    $record = Get-Content $lock -Raw | ConvertFrom-Json
    if ($record.hermes_home) { $owner = Split-Path $record.hermes_home -Leaf }
  } catch { }

  if ($owner -and $owner -ne 'dado') {
    Write-Host ""
    Write-Host "REFUSED. NOTHING WAS SAVED."
    Write-Host ""
    Write-Host "  That token already belongs to the hermes profile '$owner'."
    if ($owner -eq 'aze') {
      Write-Host "  That is TROY DUALAM'S BOT, not FRP Depot's."
      Write-Host ""
      Write-Host "  This is exactly what went wrong on 2026-08-04: Dado connected as"
      Write-Host "  Aze#1753 and answered your DMs through TDI's Discord identity."
    }
    Write-Host ""
    Write-Host "  Two gateways cannot share one Discord bot - they steal it from"
    Write-Host "  each other. Create a SEPARATE application for FRP Depot at"
    Write-Host "  https://discord.com/developers/applications and use its token."
    exit 1
  }
}

# ------------------------------------------------- ASK DISCORD WHOSE IT IS ----
# The lock check above only fires while the other gateway is RUNNING, so it
# cannot be the whole defence -- stop Aze's gateway and her token would sail
# straight through. Ask Discord itself who the token belongs to, show Rachad the
# answer, and record the id so CHECK_DADO_DISCORD.bat can prove it later.
# This is a READ (GET /users/@me). It sends no message to anyone.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host ""
Write-Host "Asking Discord who this token belongs to..."
$botId   = $null
$botName = $null
try {
  $me = Invoke-RestMethod -Uri "https://discord.com/api/v10/users/@me" -Method Get `
        -Headers @{ Authorization = "Bot $tok" } -TimeoutSec 20
  $botId   = [string]$me.id
  $botName = [string]$me.username
} catch {
  Write-Host ""
  Write-Host "Discord would not accept that token. NOTHING WAS SAVED."
  Write-Host "  $($_.Exception.Message)"
  Write-Host ""
  Write-Host "Most likely it was copied incompletely, or it was reset again in the"
  Write-Host "Developer Portal after you copied it. Click Reset Token and retry."
  exit 1
}

Write-Host ""
Write-Host "  Discord says this token is:  $botName   (id $botId)"
Write-Host ""

if ($botName -match '(?i)aze') {
  Write-Host "STOP. That is Troy Dualam's bot, not FRP Depot's. NOTHING WAS SAVED."
  Write-Host "Create a separate application for FRP Depot and use its token."
  exit 1
}

Write-Host "Is that YOUR FRP Depot bot - the application you just created?"
$confirm = Read-Host "Type YES to save it, or anything else to cancel"
if ($confirm -ne 'YES') {
  Write-Host ""
  Write-Host "Cancelled. NOTHING WAS SAVED."
  exit 1
}

# ------------------------------------------------------------- user id ----
Write-Host ""
Write-Host "Now your own Discord USER ID (not a username - the long number)."
Write-Host "Discord -> Settings -> Advanced -> Developer Mode ON, then"
Write-Host "right-click your name -> Copy User ID."
Write-Host ""
$userId = Read-Host "Paste YOUR Discord user id"
if ($userId) { $userId = $userId.Trim() }

if (-not $userId -or $userId -notmatch '^\d{15,25}$') {
  Write-Host ""
  Write-Host "A Discord user id is 17-20 digits, nothing else. Nothing was saved."
  Write-Host "Run this again once you have copied it."
  exit 1
}

# ---------------------------------------------------------------- save ----
New-Item -ItemType Directory -Force -Path $vaultDir | Out-Null

# 1) Vault copy (local backup, outside any repo)
$text = "DISCORD_BOT_TOKEN=$tok`r`nDISCORD_ALLOWED_USERS=$userId`r`nDISCORD_EXPECTED_BOT_ID=$botId`r`n"
[System.IO.File]::WriteAllText($vault, $text, (New-Object System.Text.UTF8Encoding($false)))

# 2) Inject into Dado's profile .env (replace-or-append, ASCII only).
#    Writing it EXPLICITLY here is the fix for the inherited-token bug: the
#    profile .env is loaded with override=True, so her own value now wins over
#    whatever the process environment carries.
#
#    NOTE THE DOUBLE @( ). The outer one wraps the WHOLE pipeline, not just
#    Get-Content. Without it, a filter that leaves one line returns a bare
#    STRING, and `+=` then does string CONCATENATION instead of appending to an
#    array -- which would fuse her existing keys into one corrupt line and take
#    the Telegram lane down with it.
$lines = @(@(Get-Content $profileEnv -ErrorAction SilentlyContinue) | Where-Object {
  $_ -notmatch '^\s*DISCORD_BOT_TOKEN\s*=' -and
  $_ -notmatch '^\s*DISCORD_ALLOWED_USERS\s*=' -and
  $_ -notmatch '^\s*DISCORD_EXPECTED_BOT_ID\s*='
})
$lines += "DISCORD_BOT_TOKEN=$tok"
$lines += "DISCORD_ALLOWED_USERS=$userId"
$lines += "DISCORD_EXPECTED_BOT_ID=$botId"
[System.IO.File]::WriteAllText($profileEnv, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Saved. Only your Discord account ($userId) can talk to Dado there,"
Write-Host "and she is pinned to bot $botName ($botId) - if that ever changes,"
Write-Host "CHECK_DADO_DISCORD.bat will fail instead of quietly saying ALL CLEAR."
Write-Host ""
Write-Host "NEXT STEP: double-click STOP_DADO.bat, then START_DADO.bat."
Write-Host "Then run CHECK_DADO_DISCORD.bat - it asks Discord which bot she is,"
Write-Host "so you can see for yourself that she is FRP Depot's and not TDI's."
