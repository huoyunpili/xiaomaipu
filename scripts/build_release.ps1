[CmdletBinding()]
param(
    [string]$OutputDir = (Join-Path (Split-Path $PSScriptRoot -Parent) '.local-release/packages')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$version = '0.5.0-rc1'
$status = (& git -C $projectRoot status --porcelain | Out-String).Trim()
if ($LASTEXITCODE) { throw 'Unable to read Git status.' }
if ($status) { throw 'Release packaging requires a clean committed worktree.' }

$forbidden = '(^|/)(api_key\\.txt|config\\.env|private-media|backups)(/|$)|(^|/)\\.env($|\\.(?!example$))|\\.(log|dump)$'
$tracked = & git -C $projectRoot ls-files
if ($LASTEXITCODE) { throw 'Unable to read the Git distribution list.' }
$unsafe = @($tracked | Where-Object { $_ -match $forbidden })
if ($unsafe.Count) { throw ('Forbidden release files: ' + ($unsafe -join ', ')) }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$target = Join-Path ([IO.Path]::GetFullPath($OutputDir)) "xianyu-seller-$version.zip"
& git -C $projectRoot archive --format=zip --prefix="xianyu-seller-$version/" --output=$target HEAD
if ($LASTEXITCODE) { throw 'Git archive failed.' }
Write-Output $target
