[CmdletBinding()]
param(
    [string]$OutputDir = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot '.local-release/packages' }
$version = '0.6.0'
$status = (& git -C $projectRoot status --porcelain | Out-String).Trim()
if ($LASTEXITCODE) { throw 'Unable to read Git status.' }
if ($status) { throw 'Release packaging requires a clean committed worktree.' }

$forbidden = '(^|/)(api_key\.txt|config\.env|private-media|backups)(/|$)|(^|/)\.env($|\.(?!example$))|\.(log|dump)$'
$tracked = & git -C $projectRoot ls-files
if ($LASTEXITCODE) { throw 'Unable to read the Git distribution list.' }
$unsafe = @($tracked | Where-Object { $_ -match $forbidden })
if ($unsafe.Count) { throw ('Forbidden release files: ' + ($unsafe -join ', ')) }

# A distributable must pass the same full gate as CI, including browser regressions.
$taskPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Release checks require the project virtual environment. Run uv sync first.'
}
Push-Location $projectRoot
try {
    & $taskPython scripts/check.py
    if ($LASTEXITCODE) { throw 'Release checks failed; no archive was produced.' }
    & (Join-Path $projectRoot 'scripts/verify_release_runtime.ps1')
} finally { Pop-Location }
$status = (& git -C $projectRoot status --porcelain | Out-String).Trim()
if ($LASTEXITCODE -or $status) { throw 'Worktree changed during release checks; refusing to package.' }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$target = Join-Path ([IO.Path]::GetFullPath($OutputDir)) "xianyu-seller-$version.zip"
& git -C $projectRoot archive --format=zip --prefix="xianyu-seller-$version/" --output=$target HEAD
if ($LASTEXITCODE) { throw 'Git archive failed.' }
Write-Output $target
