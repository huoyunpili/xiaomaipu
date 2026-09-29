[CmdletBinding()]
param(
    [ValidateSet('Install','Start','Restart','Stop','Status','Watch','Prepare')]
    [string]$Action = 'Start',
    [string]$AppRoot = '',
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'XianyuSeller'),
    [switch]$NoBrowser,
    [switch]$NoErrorDialog
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
$launcherPath = Join-Path $appRootPath 'scripts\windows_release.ps1'
$releaseVersion = '0.7.1'

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
    foreach ($path in @($dataRoot,$pgData,$logRoot,(Join-Path $dataRoot 'private-media'),(Join-Path $dataRoot 'backups'),(Join-Path $dataRoot 'queue/messages'),(Join-Path $dataRoot 'queue/processed'),(Join-Path $dataRoot 'queue/control'))) {
        New-Item -ItemType Directory -Force -Path $path | Out-Null
    }
}
function Save-InitialConfig {
    if (Test-Path -LiteralPath $configFile) { return }
    $webPort = 0
    # Desktop binds its own port-zero socket after preparation. This URL is
    # only a legacy configuration default, never a desktop readiness check.
    if ($Action -eq 'Prepare') { $webPort = 8765 }
    foreach ($candidate in $(if ($webPort) { @() } else { 8765..8795 })) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$candidate)
        try { $listener.Start(); $webPort = $candidate; break } catch {} finally { $listener.Stop() }
    }
    if (-not $webPort) { throw 'No available local web port was found.' }
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
        ('PUBLIC_BASE_URL=http://127.0.0.1:' + $webPort)
        'XGJ_APP_KEY='
        'XGJ_APP_SECRET='
        'XGJ_BASE_URL=https://open.goofish.pro'
    )
    [IO.File]::WriteAllLines($configFile,$lines,(New-Object Text.UTF8Encoding($false)))
}
function Get-DatabasePort([string]$Value, [int]$Fallback = 55433) {
    $parsed = 0
    if ($Value -match '^\d{1,5}$' -and [int]::TryParse($Value, [ref]$parsed) -and $parsed -ge 1 -and $parsed -le 65535) { return $parsed }
    return $Fallback
}
function Import-Config {
    foreach ($line in [IO.File]::ReadAllLines($configFile)) {
        if (-not $line -or $line.StartsWith('#')) { continue }
        $at = $line.IndexOf('=')
        if ($at -gt 0) { [Environment]::SetEnvironmentVariable($line.Substring(0,$at),$line.Substring($at+1),'Process') }
    }
    $env:PATH = "$pgBin;$runtimeRoot;$($env:PATH)"
    $env:PYTHONUTF8 = '1'
    $env:FISH_MANAGER_DATA = $dataRoot
    $env:RELEASE_VERSION = $releaseVersion
    $databasePortFile = Join-Path $dataRoot 'database-port.txt'
    $selectedPort = Get-DatabasePort $env:POSTGRES_PORT
    if (Test-Path -LiteralPath $databasePortFile) { $selectedPort = Get-DatabasePort ([IO.File]::ReadAllText($databasePortFile).Trim()) $selectedPort }
    $env:POSTGRES_PORT = [string]$selectedPort
    $urlFile = Join-Path $dataRoot 'local-web-url.txt'
    if (Test-Path -LiteralPath $urlFile) { $env:PUBLIC_BASE_URL = [IO.File]::ReadAllText($urlFile).Trim() }
    $script:baseUrl = [Uri]$env:PUBLIC_BASE_URL
    if ($baseUrl.Scheme -ne 'http' -or $baseUrl.Host -notin @('localhost','127.0.0.1')) { throw 'PUBLIC_BASE_URL must be a local HTTP address.' }
}
function Resolve-WebAddress {
    if (@(Get-Watcher).Count) { return }
    foreach ($candidate in $baseUrl.Port..($baseUrl.Port + 30)) {
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$candidate)
        try {
            $listener.Start()
            $script:baseUrl = [Uri]("http://127.0.0.1:" + $candidate)
            $env:PUBLIC_BASE_URL = $baseUrl.AbsoluteUri.TrimEnd('/')
            [IO.File]::WriteAllText((Join-Path $dataRoot 'local-web-url.txt'),$env:PUBLIC_BASE_URL)
            return
        } catch [Net.Sockets.SocketException] {} finally { $listener.Stop() }
    }
    throw 'No available local web port was found.'
}
function Initialize-Postgres {
    if (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION')) { return }
    # Publish the cluster only after initdb succeeds. Closing the desktop during
    # first launch must not leave a half-initialized database as the next target.
    $pending = [IO.Path]::GetFullPath((Join-Path $dataRoot 'postgres-initializing'))
    $dataPrefix = [IO.Path]::GetFullPath($dataRoot).TrimEnd([char]92) + [char]92
    if (-not $pending.StartsWith($dataPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid initialization directory.' }
    if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending -Recurse -Force }
    $passwordFile = Join-Path $dataRoot ('pg-password-' + (New-RandomHex 4) + '.tmp')
    try {
        [IO.File]::WriteAllText($passwordFile,$env:POSTGRES_PASSWORD,(New-Object Text.UTF8Encoding($false)))
        & (Join-Path $pgBin 'initdb.exe') --pgdata=$pending --username=$env:POSTGRES_USER --pwfile=$passwordFile --auth=scram-sha-256 --encoding=UTF8 --locale=C
        if ($LASTEXITCODE) { throw 'The bundled database could not be initialized.' }
        # A truncated port cache must never become invalid PostgreSQL syntax.
        $initialPort = Get-DatabasePort $env:POSTGRES_PORT
        [IO.File]::AppendAllText((Join-Path $pending 'postgresql.conf'), "`nlisten_addresses = '127.0.0.1'`nport = $initialPort`nmax_connections = 40`n", (New-Object Text.UTF8Encoding($false)))
        if (@(Get-ChildItem -LiteralPath $pgData -Force).Count) { throw 'An incomplete database exists. Its files were preserved for recovery.' }
        Remove-Item -LiteralPath $pgData
        Move-Item -LiteralPath $pending -Destination $pgData
    } finally { Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue }
}
function Test-PostgresConfig([string]$ConfigPath) {
    $process = New-Object Diagnostics.Process
    $process.StartInfo.FileName = Join-Path $pgBin 'postgres.exe'
    $process.StartInfo.Arguments = '-D "' + $pgData + '" -c "config_file=' + $ConfigPath + '" -C port'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        [void]$process.Start()
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) { $process.Kill(); $process.WaitForExit(); throw 'Database configuration check timed out.' }
        $detail = $errors.Result
        if ($detail) { [IO.File]::AppendAllText((Join-Path $logRoot 'postgres.log'), $detail, (New-Object Text.UTF8Encoding($false))) }
        return ($process.ExitCode -eq 0 -and (Get-DatabasePort $output.Result.Trim() 0) -ne 0)
    } finally { $process.Dispose() }
}
function Ensure-PostgresConfig {
    $config = Join-Path $pgData 'postgresql.conf'
    if (Test-PostgresConfig $config) { return }
    $original = [IO.File]::ReadAllBytes($config)
    $encoding = New-Object Text.UTF8Encoding($false, $true)
    $text = $encoding.GetString($original)
    # Repair only our installer-owned block, never unrelated/custom settings.
    $pattern = '(?m)^(listen_addresses = ''127\.0\.0\.1''\r?\n)port = [^\r\n]*(\r?\nmax_connections = 40\r?$)'
    if ([regex]::Matches($text, $pattern).Count -ne 1) { throw 'Database configuration is invalid. Original preserved; see logs/postgres.log.' }
    $fixed = [regex]::Replace($text, $pattern, '${1}port = 55433${2}')
    if ($fixed -eq $text) { throw 'Database configuration is invalid. Original preserved; see logs/postgres.log.' }
    $candidate = $config + '.repair-' + [Guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($candidate, $fixed, $encoding)
        if (-not (Test-PostgresConfig $candidate)) { throw 'Database configuration repair did not validate. Original preserved; see logs/postgres.log.' }
        if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($config)) -ne [Convert]::ToBase64String($original)) { throw 'Database configuration changed during repair. Original preserved.' }
        $backup = $config + '.before-repair-' + [Guid]::NewGuid().ToString('N') + '.bak'
        [IO.File]::Replace($candidate, $config, $backup)
        if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backup)) -ne [Convert]::ToBase64String($original)) { throw 'Database configuration backup verification failed.' }
        if (-not (Test-PostgresConfig $config)) {
            [IO.File]::Copy($backup, $config, $true)
            throw 'Database configuration validation failed. Original restored.'
        }
        Write-Info 'Repaired invalid database port configuration; original configuration backed up.'
    } finally { if (Test-Path -LiteralPath $candidate) { Remove-Item -LiteralPath $candidate -Force } }
}
function Start-Postgres {
    if (Test-PostgresRunning) { return }
    Ensure-PostgresConfig
    & (Join-Path $pgBin 'pg_ctl.exe') start -D $pgData -l (Join-Path $logRoot 'postgres.log') -o "-p $($env:POSTGRES_PORT)" -w -t 180
    if ($LASTEXITCODE) { throw 'The bundled database did not start. See logs/postgres.log.' }
}
function Select-DatabasePort {
    # Desktop mode owns the cluster. Let Windows allocate an available port;
    # pg_ctl verifies the actual bind before any migration or worker starts.
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    try {
        $listener.Start()
        $env:POSTGRES_PORT = [string]$listener.LocalEndpoint.Port
    } finally { $listener.Stop() }
    [IO.File]::WriteAllText((Join-Path $dataRoot 'database-port.txt'),$env:POSTGRES_PORT)
}
function Test-PostgresRunning {
    # pg_ctl status/stop report an absent cluster on stderr. Under Windows
    # PowerShell 5.1, redirecting that stderr with Stop aborts a fresh install.
    if (-not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) { return $false }
    $ErrorActionPreference = 'Continue'
    & (Join-Path $pgBin 'pg_ctl.exe') status -D $pgData 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
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
function Backup-Database {
    $backup = Join-Path $dataRoot ('backups/pre-initialize-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff') + '.dump')
    & (Join-Path $pgBin 'pg_dump.exe') --host=127.0.0.1 --port=$env:POSTGRES_PORT --username=$env:POSTGRES_USER --dbname=$env:POSTGRES_DB --format=custom --file=$backup
    if ($LASTEXITCODE) { throw 'Database backup failed; initialization was not attempted.' }
    & (Join-Path $pgBin 'pg_restore.exe') --list $backup | Out-Null
    if ($LASTEXITCODE) { throw 'Database backup verification failed; initialization was not attempted.' }
}
function Ensure-Schema {
    & $python (Join-Path $appRootPath 'manage.py') migrate --check
    $pending = $LASTEXITCODE -ne 0
    $marker = Join-Path $dataRoot 'initialized.txt'
    if (-not $pending -and (Test-Path -LiteralPath $marker)) { return }
    Stop-ServiceProcesses
    Backup-Database
    Invoke-Manage @('migrate','--noinput')
    Invoke-Manage @('bootstrap')
    Invoke-Manage @('rebuild_workspace')
    Invoke-Manage @('migrate','--check')
    [IO.File]::WriteAllText($marker,$releaseVersion)
}
function Get-Watcher {
    @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($launcherPath,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.CommandLine -match '(?i)-Action\s+Watch' })
}
function Start-Watcher {
    if (@(Get-Watcher).Count) { return }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$baseUrl.Port)
    try { $listener.Start() } catch { throw ('The local web port is occupied: ' + $baseUrl.Port + '. Change PUBLIC_BASE_URL in config.env to a free local port.') } finally { $listener.Stop() }
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',('"'+$PSCommandPath+'"'),'-Action','Watch','-AppRoot',('"'+$appRootPath+'"'),'-DataDir',('"'+$dataRoot+'"')) -WorkingDirectory $appRootPath -WindowStyle Hidden | Out-Null
}
function Wait-Ready {
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri ($baseUrl.AbsoluteUri.TrimEnd('/') + '/health/ready/')
            if ($response.StatusCode -eq 200 -and ($response.Content | ConvertFrom-Json).status -eq 'ok') { return }
        } catch {}
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
            web=@($python,@('-m','waitress',('--listen=127.0.0.1:' + $baseUrl.Port),'--threads=6','app.config.wsgi:application'))
            worker=@($python,@('-m','celery','-A','app.config.celery','worker','--pool=solo','--concurrency=1','--loglevel=INFO'))
            beat=@($python,@('-m','celery','-A','app.config.celery','beat',('"--schedule='+(Join-Path $dataRoot 'celerybeat-schedule')+'"'),'--loglevel=INFO'))
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
function Stop-ServiceProcesses {
    $ids=@()
    if (Test-Path -LiteralPath $stateFile) {
        try {
            $state=Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
            $ids += @($state.watcher)
            $ids += @($state.processes.PSObject.Properties.Value)
        } catch {}
    }
    $ids += @(Get-Watcher | ForEach-Object ProcessId)
    foreach ($id in ($ids | Where-Object { $_ } | Select-Object -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
        # runtime.json can survive a reboot and contain reused PIDs.
        if ($process -and (($process.ExecutablePath -and $process.ExecutablePath.StartsWith($runtimeRoot + '\',[StringComparison]::OrdinalIgnoreCase)) -or ($process.CommandLine -and $process.CommandLine.IndexOf($launcherPath,[StringComparison]::OrdinalIgnoreCase) -ge 0 -and $process.CommandLine -match '(?i)-Action\s+Watch'))) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $stateFile -Force -ErrorAction SilentlyContinue
}
function Stop-All {
    Stop-ServiceProcesses
    if (Test-PostgresRunning) {
        & (Join-Path $pgBin 'pg_ctl.exe') stop -D $pgData -m fast -w -t 180
        if ($LASTEXITCODE) { throw 'Unable to stop the bundled database safely.' }
    }
}
function Remove-ObsoleteTunnel {
    $target = [IO.Path]::GetFullPath((Join-Path $runtimeRoot 'cloudflared.exe'))
    $appPrefix = $appRootPath.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($appPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to remove an obsolete tunnel outside the application directory.'
    }
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
}

try {
Assert-Layout
Ensure-Directories
if ($Action -ne 'Watch') { Start-Transcript -Path (Join-Path $logRoot ('launcher-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff') + '.log')) | Out-Null }
if ($Action -ne 'Watch') {
    $lockHash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($dataRoot.ToLowerInvariant()))).Replace('-','')
    $launchMutex = [Threading.Mutex]::new($false,"Local\FishManager-Launch-$lockHash")
    try { $launchLocked = $launchMutex.WaitOne(300000) } catch [Threading.AbandonedMutexException] { $launchLocked = $true }
    if (-not $launchLocked) { throw 'Another startup operation is still running. Please retry shortly.' }
}
Save-InitialConfig
Import-Config
if ($Action -in @('Install','Start','Restart')) { Resolve-WebAddress }
switch ($Action) {
    'Prepare' {
        Stop-All
        Remove-ObsoleteTunnel
        Initialize-Postgres
        Select-DatabasePort
        Start-Postgres
        Ensure-Database
        Ensure-Schema
        Write-Info 'Database preparation completed.'
    }
    'Install' {
        Stop-All
        Remove-ObsoleteTunnel
        Initialize-Postgres; Start-Postgres; Ensure-Database
        Ensure-Schema
        Start-Watcher; Wait-Ready
        Write-Info ('Installation completed. Opening ' + $baseUrl.AbsoluteUri)
        if (-not $NoBrowser) { Start-Process $baseUrl.AbsoluteUri }
    }
    'Start' { Initialize-Postgres; Start-Postgres; Ensure-Database; Ensure-Schema; Start-Watcher; Wait-Ready; if (-not $NoBrowser) { Start-Process $baseUrl.AbsoluteUri } }
    'Restart' { Stop-All; Initialize-Postgres; Start-Postgres; Ensure-Database; Ensure-Schema; Start-Watcher; Wait-Ready; if (-not $NoBrowser) { Start-Process $baseUrl.AbsoluteUri } }
    'Stop' { Stop-All; Write-Info 'Services stopped. Business data and backups were preserved.' }
    'Status' { if (@(Get-Watcher).Count) { Write-Info ('Running: ' + $baseUrl.AbsoluteUri) } else { Write-Info 'Not running.' } }
    'Watch' { Start-Postgres; Invoke-Manage @('migrate','--check'); Watch-Services }
}
} catch {
    Write-Host ('Startup failed: ' + $_.Exception.Message)
    if (-not $NoErrorDialog -and $Action -ne 'Watch') {
        $shell = New-Object -ComObject WScript.Shell
        $shell.Popup("Fish Manager could not start. Your data is preserved.`nPlease check: $logRoot",0,'Fish Manager',16) | Out-Null
    }
    exit 1
} finally {
    if ($launchLocked) { $launchMutex.ReleaseMutex() }
    if ($launchMutex) { $launchMutex.Dispose() }
    if ($Action -ne 'Watch') { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null }
}
