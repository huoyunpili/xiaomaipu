[CmdletBinding()]
param(
    [ValidateSet('Install','Start','Restart','Stop','Status','Watch')]
    [string]$Action = 'Start',
    [string]$AppRoot = '',
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'XianyuSeller')
)

$ErrorActionPreference = 'Stop'
if (-not $AppRoot) { $AppRoot = Split-Path $PSScriptRoot -Parent }
$appRootPath = [IO.Path]::GetFullPath($AppRoot)
$dataRoot = [IO.Path]::GetFullPath($DataDir)
$runtimeRoot = Join-Path $appRootPath 'runtime'
$python = Join-Path $runtimeRoot 'python/python.exe'
$pgBin = Join-Path $runtimeRoot 'postgresql/bin'
$pgData = Join-Path $dataRoot 'postgres'
$configFile = Join-Path $dataRoot 'config.env'
$stateFile = Join-Path $dataRoot 'runtime.json'
$logRoot = Join-Path $dataRoot 'logs'
$releaseVersion = '0.6.0'

function Write-Info([string]$Message) { Write-Host "[Fish Manager] $Message" }
function New-RandomHex([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    ([BitConverter]::ToString($buffer)).Replace('-','').ToLowerInvariant()
}
function Assert-Layout {
    foreach ($path in @($python,(Join-Path $pgBin 'pg_ctl.exe'),(Join-Path $appRootPath 'manage.py'))) {
        if (-not (Test-Path -LiteralPath $path)) { throw "The installation is incomplete: $path" }
    }
}
function Ensure-Directories {
    $diskRoot = [IO.Path]::GetPathRoot($dataRoot)
    if ($dataRoot.TrimEnd([char]92) -eq $diskRoot.TrimEnd([char]92)) { throw 'The data directory cannot be a drive root.' }
    foreach ($path in @($dataRoot,$pgData,$logRoot,(Join-Path $dataRoot 'private-media'),(Join-Path $dataRoot 'backups'),(Join-Path $dataRoot 'queue/messages'),(Join-Path $dataRoot 'queue/processed'))) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}
function Save-InitialConfig {
    if (Test-Path -LiteralPath $configFile) { return }
    $lines = @(
        'DJANGO_SETTINGS_MODULE=app.config.settings.windows_release'
        ('DJANGO_SECRET_KEY=' + (New-RandomHex 48))
        'DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost'
        'POSTGRES_DB=seller'
        'POSTGRES_USER=seller'
        ('POSTGRES_PASSWORD=' + (New-RandomHex 24))
        'POSTGRES_HOST=127.0.0.1'
        'POSTGRES_PORT=55433'
        ('FISH_MANAGER_DATA=' + $dataRoot)
        ('RELEASE_VERSION=' + $releaseVersion)
        'PUBLIC_BASE_URL=http://127.0.0.1:8765'
        'XGJ_APP_KEY='
        'XGJ_APP_SECRET='
        'XGJ_BASE_URL=https://open.goofish.pro'
    )
    [IO.File]::WriteAllLines($configFile,$lines,(New-Object Text.UTF8Encoding($false)))
}
function Import-Config {
    foreach ($line in [IO.File]::ReadAllLines($configFile)) {
        if (-not $line -or $line.StartsWith('#')) { continue }
        $at = $line.IndexOf('=')
        if ($at -gt 0) { [Environment]::SetEnvironmentVariable($line.Substring(0,$at),$line.Substring($at+1),'Process') }
    }
    $env:PATH = "$pgBin;$runtimeRoot;$($env:PATH)"
    $env:PYTHONUTF8 = '1'
}
function Initialize-Postgres {
    if (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION')) { return }
    $passwordFile = Join-Path $dataRoot ('pg-password-' + (New-RandomHex 4) + '.tmp')
    try {
        [IO.File]::WriteAllText($passwordFile,$env:POSTGRES_PASSWORD,(New-Object Text.UTF8Encoding($false)))
        & (Join-Path $pgBin 'initdb.exe') --pgdata=$pgData --username=$env:POSTGRES_USER --pwfile=$passwordFile --auth=scram-sha-256 --encoding=UTF8 --locale=C
        if ($LASTEXITCODE) { throw 'The bundled database could not be initialized.' }
        Add-Content -LiteralPath (Join-Path $pgData 'postgresql.conf') -Value "`nlisten_addresses = '127.0.0.1'`nport = 55433`nmax_connections = 40`n"
    } finally { Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue }
}
function Start-Postgres {
    $status = & (Join-Path $pgBin 'pg_ctl.exe') status -D $pgData 2>&1
    if ($LASTEXITCODE -eq 0) { return }
    & (Join-Path $pgBin 'pg_ctl.exe') start -D $pgData -l (Join-Path $logRoot 'postgres.log') -w -t 60
    if ($LASTEXITCODE) { throw 'The bundled database did not start. See logs/postgres.log.' }
}
function Ensure-Database {
    $env:PGPASSWORD = $env:POSTGRES_PASSWORD
    $exists = & (Join-Path $pgBin 'psql.exe') --host=127.0.0.1 --port=$env:POSTGRES_PORT --username=$env:POSTGRES_USER --dbname=postgres --tuples-only --no-align --command="SELECT 1 FROM pg_database WHERE datname='$($env:POSTGRES_DB)'"
    if ($LASTEXITCODE) { throw 'Unable to inspect the bundled database.' }
    if (($exists | Out-String).Trim() -ne '1') {
        & (Join-Path $pgBin 'createdb.exe') --host=127.0.0.1 --port=$env:POSTGRES_PORT --username=$env:POSTGRES_USER --encoding=UTF8 $env:POSTGRES_DB
        if ($LASTEXITCODE) { throw 'Unable to create the application database.' }
    }
}
function Invoke-Manage([string[]]$Arguments) {
    & $python (Join-Path $appRootPath 'manage.py') @Arguments
    if ($LASTEXITCODE) { throw "Initialization command failed: $($Arguments -join ' ')" }
}
function Get-Watcher {
    @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($PSCommandPath) -and $_.CommandLine -match '(?i)-Action\s+Watch' })
}
function Start-Watcher {
    if ((Get-Watcher).Count) { return }
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',('"'+$PSCommandPath+'"'),'-Action','Watch','-AppRoot',('"'+$appRootPath+'"'),'-DataDir',('"'+$dataRoot+'"')) -WorkingDirectory $appRootPath -WindowStyle Hidden | Out-Null
}
function Wait-Ready {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://127.0.0.1:8765/health/ready/').StatusCode -eq 200) { return } } catch {}
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Fish Manager was not ready within 90 seconds. Check the data logs directory.'
}
function Start-Child([string]$Name,[string]$Executable,[string[]]$Arguments) {
    $stdout = Join-Path $logRoot ($Name + '.out.log')
    $stderr = Join-Path $logRoot ($Name + '.err.log')
    Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $appRootPath -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
}
function Watch-Services {
    $hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($dataRoot.ToLowerInvariant()))).Replace('-','').Substring(0,16)
    $mutex = [Threading.Mutex]::new($false,"Local\FishManager-$hash")
    $locked = $false
    try {
        try { $locked=$mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked=$true }
        if (-not $locked) { return }
        $children = @{}
        $specs = @{
            web=@($python,@('-m','waitress','--listen=127.0.0.1:8765','--threads=6','app.config.wsgi:application'))
            worker=@($python,@('-m','celery','-A','app.config.celery','worker','--pool=solo','--concurrency=1','--loglevel=INFO'))
            beat=@($python,@('-m','celery','-A','app.config.celery','beat',('--schedule='+(Join-Path $dataRoot 'celerybeat-schedule')),'--loglevel=INFO'))
        }
        while ($true) {
            Start-Postgres
            foreach ($name in $specs.Keys) {
                $current=$children[$name]
                if (-not $current -or $current.HasExited) { $children[$name]=Start-Child $name $specs[$name][0] $specs[$name][1] }
            }
            $state=[ordered]@{ watcher=$PID; updated_at=[DateTimeOffset]::Now.ToString('o'); processes=[ordered]@{} }
            foreach ($name in $children.Keys) { $state.processes[$name]=$children[$name].Id }
            $state | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath $stateFile
            Start-Sleep -Seconds 10
        }
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
function Stop-All {
    $ids=@()
    if (Test-Path -LiteralPath $stateFile) {
        try {
            $state=Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
            $ids += @($state.watcher)
            $ids += @($state.processes.PSObject.Properties.Value)
        } catch {}
    }
    $ids += @(Get-Watcher | ForEach-Object ProcessId)
    foreach ($id in ($ids | Where-Object { $_ } | Select-Object -Unique)) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    & (Join-Path $pgBin 'pg_ctl.exe') stop -D $pgData -m fast -w 2>$null
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
}
function Remove-ObsoleteTunnel {
    $target = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'cloudflared.exe'))
    $appPrefix = $appRootPath.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($appPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to remove an obsolete tunnel outside the application directory.'
    }
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
}

Assert-Layout
Ensure-Directories
Save-InitialConfig
Import-Config
switch ($Action) {
    'Install' {
        Stop-All
        Remove-ObsoleteTunnel
        Initialize-Postgres; Start-Postgres; Ensure-Database
        Invoke-Manage @('migrate','--noinput'); Invoke-Manage @('bootstrap'); Invoke-Manage @('rebuild_workspace')
        Start-Watcher; Wait-Ready
        Write-Info 'Installation completed. Opening http://127.0.0.1:8765'
        Start-Process 'http://127.0.0.1:8765'
    }
    'Start' { Initialize-Postgres; Start-Postgres; Ensure-Database; Start-Watcher; Wait-Ready; Start-Process 'http://127.0.0.1:8765' }
    'Restart' { Stop-All; Initialize-Postgres; Start-Postgres; Ensure-Database; Start-Watcher; Wait-Ready; Start-Process 'http://127.0.0.1:8765' }
    'Stop' { Stop-All; Write-Info 'Services stopped. Business data and backups were preserved.' }
    'Status' { if ((Get-Watcher).Count) { Write-Info 'Running: http://127.0.0.1:8765' } else { Write-Info 'Not running.' } }
    'Watch' { Initialize-Postgres; Start-Postgres; Ensure-Database; Watch-Services }
}
