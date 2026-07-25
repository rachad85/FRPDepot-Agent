# Keeps Dado's gateway (port 8647) alive.
#
# WHY THIS EXISTS (2026-07-25): Dado's gateway stopped some time between 01:19
# and 10:25 with no exit record, no Windows crash event and nothing in errors.log
# - and then the PC was restarted at 10:51. Nothing brought her back, because
# nothing ever started her automatically: TDI/Aze has a watchdog task, a Startup
# entry and a restart task, and FRP Depot had none of the three. Rachad could not
# reach her on Telegram for roughly thirteen hours and there was no signal that
# anything was wrong - the crons kept running, so every other health check looked
# fine.
#
# Two failures to survive, therefore, not one: a reboot AND a silent death.
# Running this on a short interval covers both.
#
# DELIBERATE STOPS ARE RESPECTED. STOP_DADO.bat writes the disable flag and
# START_DADO.bat clears it, so this can never fight the operator by resurrecting
# a gateway he just shut down.
[CmdletBinding()]
param([switch]$WhatIfOnly)

$ErrorActionPreference = 'Stop'
$Port        = 8647
$Root        = 'C:\FRPDepot'
$DisableFlag = Join-Path $Root 'Dado\40_Logs\gateway_disabled.flag'
$LogFile     = Join-Path $Root 'Dado\40_Logs\gateway_watchdog.log'
$Hermes      = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\hermes.exe'

function Write-Log([string]$Message) {
    try {
        $dir = Split-Path $LogFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
        Add-Content -Path $LogFile -Value "$stamp $Message" -Encoding utf8
    } catch { }
}

function Test-GatewayUp {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    return [bool]$listener
}

# 1. An operator stop wins over everything.
if (Test-Path $DisableFlag) {
    # Silent by design - this is the expected state after STOP_DADO.bat, and a
    # line every 5 minutes would bury the real events.
    exit 0
}

# 2. Healthy is the common case, and must stay silent.
if (Test-GatewayUp) { exit 0 }

# 3. Down and not deliberately stopped.
if ($WhatIfOnly) { Write-Log "WOULD START: port $Port has no listener"; exit 0 }

if (-not (Test-Path $Hermes)) {
    Write-Log "CANNOT START: hermes.exe not found at $Hermes"
    exit 1
}

Write-Log "port $Port has no listener - starting the gateway"
try {
    Start-Process -WindowStyle Hidden -FilePath 'cmd.exe' `
        -ArgumentList '/c', "`"$Hermes`" -p dado gateway run"
} catch {
    Write-Log "START FAILED: $($_.Exception.Message)"
    exit 1
}

# Confirm rather than assume. A start that silently fails every 5 minutes would
# be its own kind of invisible outage.
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 3
    if (Test-GatewayUp) {
        $pid8647 = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue).OwningProcess
        Write-Log "gateway is up (pid $pid8647)"
        exit 0
    }
}
Write-Log "STILL DOWN after 36s - backend attention needed"
exit 1
