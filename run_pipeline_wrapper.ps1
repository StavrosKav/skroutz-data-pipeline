# run_pipeline_wrapper.ps1
# Launches run_pipeline.py and catches failures that happen BEFORE Python's own
# alerting can run (missing interpreter, broken imports, crash before logging
# is initialized). Deliberately dependency-free of Python/pip -- only curl.exe
# (ships with Windows 10/11) is used to talk to Telegram.
#
# Alerting rule (to avoid duplicate alerts with run_pipeline.py's own
# send_failure_alert/tg_send calls):
#   Alert here ONLY IF the exit code is non-zero AND today's pipeline log file
#   (logs/pipeline_YYYY-MM-DD.log) was NOT created/updated during this run.
#   run_pipeline.py opens that log file as one of the very first things it does
#   (before any pipeline logic), so if the file's timestamp never moved past
#   the start of this run, Python either never launched at all, or crashed
#   before it could reach -- let alone use -- its own alerting path. Any
#   failure where the log WAS touched means Python got far enough to run its
#   own fatal-stage alert, so we stay silent and just propagate the exit code.

param()

$ErrorActionPreference = "Stop"

$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python  = Join-Path $Project ".venv\Scripts\python.exe"
$Script  = Join-Path $Project "run_pipeline.py"
$EnvFile = Join-Path $Project ".env"
$LogFile = Join-Path $Project ("logs\pipeline_{0:yyyy-MM-dd}.log" -f (Get-Date))

Set-Location $Project

$preRunStamp = if (Test-Path $LogFile) { (Get-Item $LogFile).LastWriteTimeUtc } else { $null }
$runStart    = [DateTime]::UtcNow

$exitCode = $null
$launchError = $null

try {
    & $Python $Script
    $exitCode = $LASTEXITCODE
} catch {
    # & throws a terminating error if $Python itself cannot be found/started.
    $exitCode = 9009
    $launchError = $_.Exception.Message
}

if ($exitCode -eq 0) {
    exit 0
}

$logTouched = $false
if (Test-Path $LogFile) {
    $postRunStamp = (Get-Item $LogFile).LastWriteTimeUtc
    if ($null -eq $preRunStamp -or $postRunStamp -gt $preRunStamp) {
        if ($postRunStamp -ge $runStart) {
            $logTouched = $true
        }
    }
}

if ($logTouched) {
    # Python ran far enough to log and already sent its own Telegram alert.
    exit $exitCode
}

# Python never ran, or crashed before it could set up logging/alerting -- alert here.
function Get-EnvValue {
    param([string]$Key)
    if (-not (Test-Path $EnvFile)) { return $null }
    $line = Select-String -Path $EnvFile -Pattern "^$Key=" | Select-Object -First 1
    if (-not $line) { return $null }
    return $line.Line.Substring($Key.Length + 1).Trim()
}

$botToken = Get-EnvValue -Key "TELEGRAM_BOT_TOKEN"
$chatId   = Get-EnvValue -Key "TELEGRAM_CHAT_ID"

if ($botToken -and $chatId) {
    $hint = "pipeline never started - check interpreter/imports"
    $text = "ALERT: Pipeline launch failed`nExit code: $exitCode`nHint: $hint"
    if ($launchError) {
        $text += "`nDetail: $launchError"
    }
    $url = "https://api.telegram.org/bot$botToken/sendMessage"
    & curl.exe -s -X POST $url --data-urlencode "chat_id=$chatId" --data-urlencode "text=$text" | Out-Null
}

exit $exitCode
