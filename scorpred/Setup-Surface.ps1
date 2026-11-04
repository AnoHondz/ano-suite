$ErrorActionPreference = 'Stop'
$transferRoot = $PSScriptRoot
Push-Location $transferRoot
try {
    $transferPython = Join-Path $transferRoot '.runtime\python\python.exe'
    $transferNode = Join-Path $transferRoot '.runtime\node'
    $transferVenv = Join-Path $transferRoot '.venv'
    if (-not (Test-Path -LiteralPath $transferPython)) { throw 'Extract the whole ZIP first. The bundled Python runtime is missing.' }
    if (-not (Test-Path -LiteralPath (Join-Path $transferNode 'node.exe'))) { throw 'Extract the whole ZIP first. The bundled Node runtime is missing.' }
    $env:PATH = "$transferNode;$env:PATH"
    Write-Host 'Preparing ScorPred for this computer...'
    # Recreate only venv launchers/configuration for the new absolute path.
    # Existing installed packages and local data remain in place.
    & $transferPython -m venv --upgrade $transferVenv
    if ($LASTEXITCODE -ne 0) { throw 'Python could not prepare its environment. Use Windows 11 on an ARM Surface.' }
    & (Join-Path $transferVenv 'Scripts\python.exe') -c 'import flask, flask_sqlalchemy, flask_limiter, apscheduler, requests, numpy, pandas, sklearn; print(sklearn.__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'Bundled Python package verification failed.' }
    Write-Host 'Python packages ready.'
    & (Join-Path $transferRoot 'Start-ScorPred.ps1')
    Start-Process 'http://127.0.0.1:5173'
} finally {
    Pop-Location
}
