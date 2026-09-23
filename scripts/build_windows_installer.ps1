[CmdletBinding()]
param(
    [string]$PostgresArchive = '',
    [string]$OutputDir = '',
    [string]$InnoCompiler = '',
    [switch]$SkipChecks
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot '.local-release/windows' }
$stageRoot = Join-Path $projectRoot '.local-release/windows-stage'
$pythonVersion = '3.13.7'
$pythonSha256 = 'f6cca216a359be84797cabb54149ce5e062afb16cc7567eb7fc51cacb2d86b65'
$postgresSha256 = 'b9424ee7bc60b52450ff910a3630225df32e633f3cb29c1d126d9299d59aea28'
$uvExe = Join-Path $projectRoot '.venv/Scripts/uv.exe'

function Download-Checked([string]$Url,[string]$Target,[string]$Sha256='') {
    if (-not (Test-Path -LiteralPath $Target)) { Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Target }
    if ($Sha256 -and (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
        throw "Downloaded file checksum failed: $Target"
    }
}

if (-not $SkipChecks) {
    & (Join-Path $projectRoot '.venv/Scripts/python.exe') (Join-Path $projectRoot 'scripts/check.py')
    if ($LASTEXITCODE) { throw 'Quality checks failed; no installer was built.' }
}
if (-not (Test-Path -LiteralPath $uvExe)) { throw 'The release environment is missing .venv/Scripts/uv.exe.' }
if (Test-Path -LiteralPath $stageRoot) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $stageRoot 'runtime/python'),(Join-Path $stageRoot 'runtime/site-packages'),(Join-Path $stageRoot 'scripts') | Out-Null

$cacheRoot = Join-Path $projectRoot '.local-release/downloads'
New-Item -ItemType Directory -Force -Path $cacheRoot,$OutputDir | Out-Null
if (-not $PostgresArchive) {
    $PostgresArchive = Join-Path $cacheRoot 'postgresql-17.11-windows-x64-binaries.zip'
    Download-Checked 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260569' $PostgresArchive $postgresSha256
}
if (-not (Test-Path -LiteralPath $PostgresArchive)) { throw 'Provide a PostgreSQL 17 Windows x64 binaries ZIP.' }
if ((Get-FileHash -LiteralPath $PostgresArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $postgresSha256) { throw 'PostgreSQL archive checksum failed.' }
$pythonZip = Join-Path $cacheRoot "python-$pythonVersion-embed-amd64.zip"
Download-Checked "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip" $pythonZip $pythonSha256
Expand-Archive -LiteralPath $pythonZip -DestinationPath (Join-Path $stageRoot 'runtime/python')

$pthFile = Get-ChildItem -LiteralPath (Join-Path $stageRoot 'runtime/python') -Filter 'python*._pth' | Select-Object -First 1
if (-not $pthFile) { throw 'Python embedded package layout is unexpected.' }
$stdlibZip = Get-ChildItem -LiteralPath (Join-Path $stageRoot 'runtime/python') -Filter 'python*.zip' | Select-Object -First 1
if (-not $stdlibZip) { throw 'Python embedded standard library ZIP is missing.' }
$pth = @(
    $stdlibZip.Name,
    '.',
    '../site-packages',
    '../site-packages/win32',
    '../site-packages/win32/lib',
    '../site-packages/win32com',
    '../../..',
    'import site'
)
[IO.File]::WriteAllLines($pthFile.FullName,$pth,(New-Object Text.UTF8Encoding($false)))

& $uvExe export --project $projectRoot --frozen --no-dev --no-emit-project --format requirements-txt --output-file (Join-Path $stageRoot 'requirements.txt')
if ($LASTEXITCODE) { throw 'Unable to export locked dependencies.' }
& $uvExe pip install --python (Join-Path $stageRoot 'runtime/python/python.exe') --target (Join-Path $stageRoot 'runtime/site-packages') --requirements (Join-Path $stageRoot 'requirements.txt')
if ($LASTEXITCODE) { throw 'Unable to prepare bundled Python dependencies.' }

$pgExtract = Join-Path $stageRoot 'postgres-extract'
New-Item -ItemType Directory -Force -Path $pgExtract | Out-Null
& tar.exe -xf ([IO.Path]::GetFullPath($PostgresArchive)) -C $pgExtract
if ($LASTEXITCODE) { throw 'PostgreSQL archive extraction failed.' }
$pgBin = Get-ChildItem -LiteralPath $pgExtract -Filter 'pg_ctl.exe' -File -Recurse | Select-Object -First 1
if (-not $pgBin) { throw 'pg_ctl.exe was not found in the PostgreSQL ZIP.' }
$pgRoot = Split-Path (Split-Path $pgBin.FullName -Parent) -Parent
Move-Item -LiteralPath $pgRoot -Destination (Join-Path $stageRoot 'runtime/postgresql')
Remove-Item -LiteralPath $pgExtract -Recurse -Force
$bundledPostgres = Join-Path $stageRoot 'runtime/postgresql'
foreach ($unusedName in @('doc','include','pgAdmin 4','StackBuilder')) {
    $unusedPath = Join-Path $bundledPostgres $unusedName
    if (Test-Path -LiteralPath $unusedPath) { Remove-Item -LiteralPath $unusedPath -Recurse -Force }
}
Get-ChildItem -LiteralPath (Join-Path $bundledPostgres 'lib') -File -Recurse |
    Where-Object { $_.Extension -in @('.a','.lib') } |
    Remove-Item -Force

Copy-Item -LiteralPath (Join-Path $projectRoot 'app') -Destination $stageRoot -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'manage.py') -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'scripts/windows_release.ps1') -Destination (Join-Path $stageRoot 'scripts')
Copy-Item -LiteralPath (Join-Path $projectRoot 'LICENSE') -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $projectRoot 'NOTICE') -Destination $stageRoot

$env:DJANGO_SETTINGS_MODULE='app.config.settings.windows_release'
$env:DJANGO_SECRET_KEY='build-only-secret-build-only-secret-build-only-secret-123456789'
$env:POSTGRES_PASSWORD='build-only'
$env:FISH_MANAGER_DATA=(Join-Path $stageRoot 'build-data')
Push-Location $stageRoot
try {
    & (Join-Path $stageRoot 'runtime/python/python.exe') manage.py collectstatic --noinput
    if ($LASTEXITCODE) { throw 'Static asset collection failed.' }
} finally { Pop-Location }
Remove-Item -LiteralPath (Join-Path $stageRoot 'build-data') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $stageRoot 'requirements.txt') -Force
Get-ChildItem -LiteralPath $stageRoot -Directory -Filter '__pycache__' -Recurse | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $stageRoot -File -Filter '*.pyc' -Recurse | Remove-Item -Force
Get-ChildItem -LiteralPath (Join-Path $stageRoot 'runtime/site-packages') -File -Filter '*.po' -Recurse | Remove-Item -Force

if (-not $InnoCompiler) {
    $candidates=@((Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'),(Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe'))
    $InnoCompiler=$candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) { throw 'Inno Setup 6 is required only on the release builder, not the end-user computer.' }

& $InnoCompiler "/DSourceRoot=$stageRoot" "/DOutputDir=$([IO.Path]::GetFullPath($OutputDir))" (Join-Path $projectRoot 'installer/fish-manager.iss')
if ($LASTEXITCODE) { throw 'Installer compilation failed.' }
Get-ChildItem -LiteralPath $OutputDir -Filter '*.exe' | Select-Object -ExpandProperty FullName
