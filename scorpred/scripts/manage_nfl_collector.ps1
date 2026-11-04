param(
    [ValidateSet('Install','Start','Reload','Stop','Status')]
    [string]$Action = 'Status'
)
$ErrorActionPreference = 'Stop'
$collectorRepo = Split-Path -Parent $PSScriptRoot
$collectorPython = Join-Path $collectorRepo '.venv\Scripts\python.exe'
$collectorPythonWindowless = Join-Path $collectorRepo '.venv\Scripts\pythonw.exe'
$collectorDatabase = Join-Path $collectorRepo 'cache\nflverse\nflverse.sqlite3'
$collectorState = Join-Path $collectorRepo 'cache\nflverse\collector'
$collectorTaskName = 'ScorPred NFL Collector'
$collectorArguments = '-m scripts.nfl_collector --database "' + $collectorDatabase + '" --state-dir "' + $collectorState + '"'
$collectorTask = Get-ScheduledTask -TaskName $collectorTaskName -ErrorAction SilentlyContinue
if ($collectorTask) {
    $collectorExpectedSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $collectorAccount = New-Object System.Security.Principal.NTAccount($collectorTask.Principal.UserId)
    $collectorActualSid = $collectorAccount.Translate([System.Security.Principal.SecurityIdentifier]).Value
    if ($collectorTask.Actions.Count -ne 1 -or
        $collectorActualSid -ne $collectorExpectedSid -or
        $collectorTask.Actions[0].Execute -ne $collectorPythonWindowless -or
        $collectorTask.Actions[0].Arguments -ne $collectorArguments -or
        $collectorTask.Actions[0].WorkingDirectory -ne $collectorRepo) {
        throw 'The existing task is not the identified ScorPred collector; refusing to alter it.'
    }
}
if ($Action -eq 'Install') {
    if (-not $collectorTask) {
        $collectorUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $collectorTaskAction = New-ScheduledTaskAction -Execute $collectorPythonWindowless -Argument $collectorArguments -WorkingDirectory $collectorRepo
        $collectorTrigger = New-ScheduledTaskTrigger -AtLogOn -User $collectorUser
        $collectorPrincipal = New-ScheduledTaskPrincipal -UserId $collectorUser -LogonType Interactive -RunLevel Limited
        $collectorSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $collectorTaskName -Action $collectorTaskAction -Trigger $collectorTrigger -Principal $collectorPrincipal -Settings $collectorSettings -Description 'Local singleton NFL collection only; uses existing importer, no app startup or model activation.' | Out-Null
    }
    Start-ScheduledTask -TaskName $collectorTaskName
}
if ($Action -in 'Reload','Stop') {
    Push-Location $collectorRepo
    try { & $collectorPython -m scripts.nfl_collector --state-dir $collectorState --stop } finally { Pop-Location }
    $collectorDeadline = (Get-Date).AddMinutes(3)
    do {
        $collectorStatusPath = Join-Path $collectorState 'status.json'
        $collectorStatus = if (Test-Path -LiteralPath $collectorStatusPath) { Get-Content -LiteralPath $collectorStatusPath -Raw | ConvertFrom-Json } else { $null }
        $collectorTask = Get-ScheduledTask -TaskName $collectorTaskName -ErrorAction SilentlyContinue
        if ($collectorTask -and $collectorTask.State -eq 'Running' -and $collectorStatus -and $collectorStatus.state -eq 'stopped') {
            Start-Sleep -Seconds 1
        } elseif (($null -eq $collectorTask -or $collectorTask.State -ne 'Running') -and ($null -eq $collectorStatus -or $collectorStatus.state -eq 'stopped')) {
            break
        } else {
            Start-Sleep -Seconds 1
        }
        if ((Get-Date) -gt $collectorDeadline) { throw 'Collector did not stop gracefully; no process was terminated.' }
    } while ($true)
    if ($Action -eq 'Reload') { Start-ScheduledTask -TaskName $collectorTaskName }
}
if ($Action -eq 'Start') { Start-ScheduledTask -TaskName $collectorTaskName }
$collectorTask = Get-ScheduledTask -TaskName $collectorTaskName -ErrorAction SilentlyContinue
[PSCustomObject]@{ task=$collectorTaskName; state=if ($collectorTask) { [string]$collectorTask.State } else { 'not_installed' }; database=$collectorDatabase; status_file=(Join-Path $collectorState 'status.json') } | ConvertTo-Json
