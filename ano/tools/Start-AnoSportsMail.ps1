$ErrorActionPreference = 'Stop'
$anoRoot = 'C:\Users\User\Documents\Ano'
$python = 'C:\Users\User\Documents\ScorPred-Surface-2026-10-24\ScorPred\.venv\Scripts\pythonw.exe'
# Service itself has a cross-process lock and persistent opt-in check.
Start-Process -FilePath $python -ArgumentList @('-m','ano.sports_mail','serve') -WorkingDirectory $anoRoot -WindowStyle Hidden
