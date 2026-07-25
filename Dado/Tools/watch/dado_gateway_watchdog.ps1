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
$VenvPython  = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\python.exe'
$Alerter     = Join-Path $Root 'Dado\Tools\watch\dado_urgent_alert.py'

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
        # Recovered - retire any outstanding alert so the next real outage is
        # not swallowed by the cooldown, and clear the desktop marker.
        try { & $VenvPython $Alerter --clear --reason gateway_down | Out-Null } catch { }
        exit 0
    }
}

# Could not recover. This is the case Rachad asked to be told about, and it is
# the one case where the normal Telegram path cannot be trusted: send_clean, the
# cron `deliver: telegram` and Dado herself all run through hermes, which is
# exactly what is suspect here. dado_urgent_alert.py talks straight to the
# Telegram Bot API with the standard library - no hermes, no gateway - and drops
# a file on the Desktop if even that fails.
Write-Log "STILL DOWN after 36s - raising an out-of-band alert"
$msg = "DADO IS DOWN. Her gateway (port 8647) is not running and the keep-alive could not restart it after 36s, so Telegram replies from her will not work. Nothing else is affected - the crons are still running. To fix: double-click START_DADO.bat in C:\FRPDepot. Sent out-of-band because the normal path goes through the part that is broken."
try {
    & $VenvPython $Alerter --reason gateway_down --message $msg 2>&1 | ForEach-Object { Write-Log "alerter: $_" }
} catch {
    Write-Log "ALERTER FAILED: $($_.Exception.Message)"
}
exit 1
