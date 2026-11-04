# Save the latest Ano + ScorPred source to this repo and push it to GitHub.
# Usage (PowerShell):  & "$HOME\Documents\ano-suite\sync.ps1" "what changed"
#
# Copies source only. Never copies: .env files, virtual environments, databases, caches, ScorPred's data/persistent
# folders, Ano's builds and screenshots. Every word listed in .private-words.txt (one per line, never committed) is
# replaced with a placeholder in the copy, and the push stops if anything that looks like a key is found.
param([string]$Message = "Sync Ano + ScorPred source")
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$sources = @{ 'ano' = "$HOME\Documents\Ano"; 'scorpred' = "$HOME\Documents\ScorPred-Surface-2026-10-24\ScorPred" }
$skipDirs = @('.venv', '.runtime', '__pycache__', '.pytest_cache', 'node_modules', 'dist', '.git', '.claude', '.codex', '.vscode',
              'releases', 'previews', 'finetune', 'MagicMock', 'ano_desktop.egg-info',
              'cache', 'data', 'persistent', '.tmp', 'transfer-metadata', 'playwright-report', 'test-results', 'github_data_research')
$skipFiles = @('*.pyc', '*.sqlite3', '*.db', '*.log', '.env', '.env.bak*', 'transfer-manifest.json', '*.pkl', '*.joblib', '*.parquet')

foreach ($name in $sources.Keys) {
    robocopy $sources[$name] (Join-Path $root $name) /MIR /NFL /NDL /NJH /NJS /NP /XD $skipDirs /XF $skipFiles | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying $name failed (robocopy exit $LASTEXITCODE)" }
}

# Scrub private words (your email address and similar) from every copied text file.
$wordsFile = Join-Path $root '.private-words.txt'
$words = if (Test-Path $wordsFile) { Get-Content $wordsFile | Where-Object { $_.Trim() } } else { @() }
$utf8 = New-Object System.Text.UTF8Encoding($false)
$text = '\.(py|ps1|cmd|md|txt|json|toml|cfg|ini|yaml|yml|html|css|js|ts|tsx|qml|sh)$'
# Excluded folders are never copied, but robocopy does not delete ones already here, so remove them explicitly.
Get-ChildItem $root -Recurse -Directory | Where-Object { $_.Name -in @('github_data_research', '__pycache__', 'node_modules', '.venv') } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$own = @('sync.ps1', '.private-words.txt')  # this script holds the key pattern; the word list holds the words
$copied = { Get-ChildItem $root -Recurse -File | Where-Object { $_.FullName -notmatch '\\\.git\\' -and
                                                                 -not ($_.DirectoryName -eq $root -and $_.Name -in $own) } }
foreach ($file in & $copied | Where-Object { $_.Name -match $text }) {
    $content = [IO.File]::ReadAllText($file.FullName)
    $clean = $content
    foreach ($word in $words) { $clean = $clean.Replace($word.Trim(), 'you@example.com') }
    if ($clean -ne $content) { [IO.File]::WriteAllText($file.FullName, $clean, $utf8) }
}

# Refuse to push anything that looks like a real key or token.
$keyPattern = 'sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|[0-9a-f]{32}-[0-9a-f]{32}'
$hits = & $copied | Where-Object { $_.Name -match $text } | Select-String -Pattern $keyPattern -List
if ($hits) { $hits | ForEach-Object { Write-Warning "$($_.Path):$($_.LineNumber) looks like a key" }; throw 'Possible secret found; nothing was pushed.' }
foreach ($word in $words) {
    $left = & $copied | Select-String -SimpleMatch $word.Trim() -List
    if ($left) { $left | ForEach-Object { Write-Warning "$($_.Path) still contains a private word" }; throw 'A private word is still present; nothing was pushed.' }
}

git -C $root add -A
if (git -C $root status --porcelain) {
    git -C $root commit -m $Message | Out-Null
    git -C $root push
    Write-Host "Saved and pushed: $Message"
} else {
    Write-Host 'Nothing changed since the last save.'
}
