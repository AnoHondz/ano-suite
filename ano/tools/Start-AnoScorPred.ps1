param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$scorRoot = 'C:\Users\User\Documents\ScorPred-Surface-2026-10-24\ScorPred'
$logRoot = Join-Path $PSScriptRoot '..\.runtime\scorpred-morning'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$logFile = Join-Path $logRoot 'startup.log'
$guard = New-Object System.Threading.Mutex($false, 'Local\AnoScorPredMorning')
$ownsGuard = $false
try {
    try { $ownsGuard = $guard.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsGuard = $true }
    if (-not $ownsGuard) { exit 0 }
    Push-Location -LiteralPath $scorRoot
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scorRoot 'Start-ScorPred.ps1') *>> $logFile
        if ($LASTEXITCODE -ne 0) { throw 'ScorPred launcher failed; see startup.log.' }
        $runtime = Invoke-RestMethod 'http://127.0.0.1:5001/api/local-runtime' -TimeoutSec 10
        if ($runtime.app -ne 'ScorPred' -or $runtime.root -ne $scorRoot) { throw 'Unexpected local server; nothing sent.' }
        $digest = $runtime.daily_digest
        if (-not $digest.enabled -or $digest.recipient -ne 'you@example.com' -or $digest.time -ne '06:00' -or $digest.timezone -ne 'America/New_York') {
            throw 'Digest settings differ from the authorized morning report; nothing sent.'
        }
        $eastern = [System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'Eastern Standard Time')
        if (-not $CheckOnly -and $eastern.Hour -ge 6) {
            # Existing ScorPred command uses a locked recipient/day receipt and never
            # retries an ambiguous SMTP submission. No extra email sender is added.
            $deliveryOutput = & (Join-Path $scorRoot '.venv\Scripts\python.exe') (Join-Path $scorRoot 'scripts\daily_digest.py') send 2>> $logFile
            if ($LASTEXITCODE -ne 0) { throw 'Catch-up report failed; see startup.log.' }
            $deliveryResult = ($deliveryOutput -join "`n") | ConvertFrom-Json
            $deliveryResult | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot 'delivery.json') -Encoding UTF8
            if ($deliveryResult.state -notin @('sent','already_sent_or_needs_review','already_running')) {
                throw ('Catch-up report did not send: ' + $deliveryResult.state)
            }
            if ($deliveryResult.state -eq 'already_sent_or_needs_review' -and $deliveryResult.receipt_state -ne 'sent') {
                throw 'Previous email delivery is uncertain; manual review required, no retry made.'
            }
        }
        [ordered]@{checked_at=$eastern.ToString('o');app='ScorPred';scheduler_registered=$digest.scheduler_registered;
            next_run=$digest.next_run;delivery=$digest.delivery;check_only=[bool]$CheckOnly} |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot 'status.json') -Encoding UTF8
    } finally { Pop-Location }
} catch {
    Add-Content -LiteralPath $logFile -Value ((Get-Date -Format o) + ' ERROR: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($ownsGuard) { $guard.ReleaseMutex() }
    $guard.Dispose()
}
