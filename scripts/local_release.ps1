[CmdletBinding()]
param(
    [ValidateSet('Install','Start','Stop','Restart','Status','Backup','Restore','Upgrade','Configure','ScheduleBackup','RemoveBackupSchedule','Validate')]
    [string]$Action = 'Status',
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'XianyuSeller'),
    [int]$Port = 8765,
    [string]$BindAddress = '127.0.0.1',
    [string]$PublicUrl = '',
    [string]$BackupPath = '',
    [switch]$ConfirmRestore,
    [switch]$SkipAdmin,
    [switch]$NoAutoBackup
)

$ErrorActionPreference = 'Stop'
$env:COMPOSE_BAKE = 'false'
$releaseVersion = '0.6.0'
$projectRoot = Split-Path $PSScriptRoot -Parent
$composeFile = Join-Path $projectRoot 'deployment/compose.local.yaml'
$dataRoot = [IO.Path]::GetFullPath($DataDir)
$configFile = Join-Path $dataRoot 'config.env'
$script:config = @{}

function Write-Info([string]$Message) { Write-Host "[闲鱼小卖家后台] $Message" }
function New-RandomHex([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($buffer) } finally { $generator.Dispose() }
    ([BitConverter]::ToString($buffer)).Replace('-','').ToLowerInvariant()
}
function Get-ProjectName {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($dataRoot.ToLowerInvariant()))
    } finally {
        $hasher.Dispose()
    }
    'xianyu-seller-' + (([BitConverter]::ToString($digest)).Replace('-','').Substring(0,8).ToLowerInvariant())
}
function Assert-SafeRoot {
    $diskRoot = [IO.Path]::GetPathRoot($dataRoot)
    if ($dataRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) -eq $diskRoot.TrimEnd([IO.Path]::DirectorySeparatorChar)) {
        throw '数据目录不能是磁盘根目录。'
    }
    if ($dataRoot -eq [IO.Path]::GetFullPath($projectRoot)) { throw '数据目录必须与源码目录分开。' }
}
function Assert-Child([string]$Path) {
    $candidate = [IO.Path]::GetFullPath($Path)
    $prefix = $dataRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作数据目录之外的路径：$candidate"
    }
    $candidate
}
function Load-Config {
    $script:config = @{}
    if (-not (Test-Path -LiteralPath $configFile)) { return }
    foreach ($line in [IO.File]::ReadAllLines($configFile)) {
        if (-not $line -or $line.StartsWith('#')) { continue }
        $at = $line.IndexOf('=')
        if ($at -gt 0) { $script:config[$line.Substring(0,$at)] = $line.Substring($at + 1) }
    }
}
function Save-Config {
    $keys = @('COMPOSE_PROJECT_NAME','SELLER_DATA_DIR','DJANGO_SECRET_KEY','POSTGRES_DB','POSTGRES_USER',
        'POSTGRES_PASSWORD','APP_IMAGE','RELEASE_VERSION','APP_PORT','BIND_ADDRESS','PUBLIC_BASE_URL',
        'DJANGO_ALLOWED_HOSTS','DJANGO_CSRF_TRUSTED_ORIGINS','AUTO_BACKUP_HOURS','XGJ_APP_KEY',
        'XGJ_APP_SECRET','XGJ_BASE_URL')
    $lines = foreach ($key in $keys) {
        $value = if ($script:config.ContainsKey($key)) { [string]$script:config[$key] } else { '' }
        if ($value.Contains([char]10) -or $value.Contains([char]13)) { throw "配置项 $key 不能包含换行。" }
        "$key=$value"
    }
    [IO.File]::WriteAllLines($configFile, $lines, (New-Object Text.UTF8Encoding($false)))
}
function Get-WebConfig([string]$Url,[string]$Address,[int]$ListenPort) {
    if (-not $Url) {
        $hostName = if ($Address -eq '0.0.0.0') { '127.0.0.1' } else { $Address }
        $Url = 'http://{0}:{1}' -f $hostName,$ListenPort
    }
    try { $uri = [Uri]$Url } catch { throw 'PublicUrl 必须是完整的 http:// 或 https:// 地址。' }
    if ($uri.Scheme -notin @('http','https') -or -not $uri.Host) { throw 'PublicUrl 格式无效。' }
    if ($Address -eq '0.0.0.0' -and $uri.Host -in @('127.0.0.1','localhost')) {
        throw '局域网模式需要使用本机局域网 IP。'
    }
    $origin = $uri.GetLeftPart([UriPartial]::Authority)
    @{
        Url=$origin
        Hosts=(($uri.Host,'127.0.0.1','localhost' | Select-Object -Unique) -join ',')
        Origins=(($origin,('http://127.0.0.1:{0}' -f $ListenPort),('http://localhost:{0}' -f $ListenPort) | Select-Object -Unique) -join ',')
    }
}
function Ensure-Config {
    Assert-SafeRoot
    New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
    foreach ($name in @('postgres','redis','private-media','beat','backups')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot $name) | Out-Null
    }
    Load-Config
    if ($script:config.Count) { return }
    $web = Get-WebConfig $PublicUrl $BindAddress $Port
    $script:config = @{
        COMPOSE_PROJECT_NAME=Get-ProjectName; SELLER_DATA_DIR=$dataRoot.Replace([char]92,'/')
        DJANGO_SECRET_KEY=New-RandomHex 48; POSTGRES_DB='seller'; POSTGRES_USER='seller'
        POSTGRES_PASSWORD=New-RandomHex 24; APP_IMAGE="xianyu-seller-local:$releaseVersion"
        RELEASE_VERSION=$releaseVersion; APP_PORT=[string]$Port; BIND_ADDRESS=$BindAddress
        PUBLIC_BASE_URL=$web.Url; DJANGO_ALLOWED_HOSTS=$web.Hosts
        DJANGO_CSRF_TRUSTED_ORIGINS=$web.Origins; AUTO_BACKUP_HOURS='24'
        XGJ_APP_KEY=''; XGJ_APP_SECRET=''; XGJ_BASE_URL=''
    }
    Save-Config
    Write-Info "已生成私有配置：$configFile"
}
function Assert-Docker {
    & docker version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE) { throw 'Docker Desktop 未运行或当前用户无法访问 Docker。' }
    & docker compose version --short | Out-Null
    if ($LASTEXITCODE) { throw '未找到 Docker Compose v2。' }
}
function Compose {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    & docker compose --env-file $configFile -f $composeFile @Arguments
    if ($LASTEXITCODE) { throw "Docker Compose 命令失败：$($Arguments -join ' ')" }
}
function Compose-Text {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    $result = & docker compose --env-file $configFile -f $composeFile @Arguments
    if ($LASTEXITCODE) { throw "Docker Compose 命令失败：$($Arguments -join ' ')" }
    (($result | Out-String).Trim())
}
function Is-Running { -not [string]::IsNullOrWhiteSpace((Compose-Text ps --status running --quiet web)) }
function Assert-Port {
    if (Is-Running) { return }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse($script:config.BIND_ADDRESS),[int]$script:config.APP_PORT)
    try { $listener.Start() } catch { throw "端口 $($script:config.APP_PORT) 已被占用。" } finally { $listener.Stop() }
}
function Migrate {
    Compose run --rm -T web python manage.py migrate --noinput
    Compose run --rm -T web python manage.py bootstrap
    Compose run --rm -T web python manage.py rebuild_workspace
    Compose run --rm -T web python manage.py check
}
function Wait-Ready {
    $url = "$($script:config.PUBLIC_BASE_URL)/health/ready/"
    $deadline = [DateTime]::UtcNow.AddMinutes(2)
    do {
        try {
            if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Uri $url).StatusCode -eq 200) {
                foreach ($service in @('worker','beat')) {
                    if ([string]::IsNullOrWhiteSpace((Compose-Text ps --status running --quiet $service))) {
                        throw "后台服务未运行：$service"
                    }
                }
                Compose exec -T worker celery -A app.config inspect ping --timeout=5
                Write-Info "服务已就绪：$($script:config.PUBLIC_BASE_URL)"
                return
            }
        } catch {}
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw '网页或后台同步服务未在两分钟内就绪，请运行 Status 并检查 web、worker、beat 日志。'
}
function Db-Counts([string]$Database) {
    $sql = "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    $tables = Compose-Text exec -T postgres psql --username $script:config.POSTGRES_USER --dbname $Database --no-align --tuples-only --command $sql
    $counts = [ordered]@{}
    foreach ($raw in $tables.Split([char]10)) {
        $table = $raw.Trim([char]13)
        if (-not $table) { continue }
        if ($table -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "不安全的表名：$table" }
        $countSql = 'SELECT count(*) FROM "{0}"' -f $table
        $counts[$table] = [long](Compose-Text exec -T postgres psql --username $script:config.POSTGRES_USER --dbname $Database --no-align --tuples-only --command $countSql)
    }
    $counts
}
function Media-Hashes([string]$Root) {
    $hashes = [ordered]@{}
    if (Test-Path -LiteralPath $Root) {
        foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }) {
            $rootPrefix = [IO.Path]::GetFullPath($Root).TrimEnd([char]92,[char]47) + [IO.Path]::DirectorySeparatorChar
            if (-not $file.FullName.StartsWith($rootPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw '附件路径越界。' }
            $relative = $file.FullName.Substring($rootPrefix.Length).Replace([char]92,'/')
            $hashes[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $hashes
}
function Db-Fingerprint([string]$Database) {
    if ($Database -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw '数据库名称无效。' }
    $helper = Join-Path $projectRoot 'app/common/backup.py'
    Compose-Text run --rm -T --no-deps --env "POSTGRES_DB=$Database" --volume "${helper}:/tmp/backup_fingerprint.py:ro" web python /tmp/backup_fingerprint.py
}
function Backup {
    Ensure-Config
    Assert-Docker
    $wasRunning = Is-Running
    $stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss') + '-' + (New-RandomHex 3)
    $target = Assert-Child (Join-Path (Join-Path $dataRoot 'backups') $stamp)
    New-Item -ItemType Directory -Path $target | Out-Null
    $dumpName = "backup-$stamp.dump"
    $scratch = 'seller_restore_check_' + (New-RandomHex 6)
    try {
        if ($wasRunning) { Compose stop caddy web worker beat }
        Compose up --detach --wait postgres redis
        Compose run --rm -T web python manage.py reconcile_business_data --check
        Compose exec -T postgres pg_dump --username $script:config.POSTGRES_USER --dbname $script:config.POSTGRES_DB --format=custom --file "/tmp/$dumpName"
        Compose cp "postgres:/tmp/$dumpName" (Join-Path $target 'database.dump')
        $sourceMedia = Assert-Child (Join-Path $dataRoot 'private-media')
        $backupMedia = Join-Path $target 'private-media'
        New-Item -ItemType Directory -Path $backupMedia | Out-Null
        foreach ($item in Get-ChildItem -LiteralPath $sourceMedia -Force) {
            if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                Copy-Item -LiteralPath $item.FullName -Destination $backupMedia -Recurse
            }
        }
        Compose exec -T postgres createdb --username $script:config.POSTGRES_USER $scratch
        try {
            Compose exec -T postgres pg_restore --username $script:config.POSTGRES_USER --dbname $scratch --exit-on-error --no-owner "/tmp/$dumpName"
            $before = Db-Fingerprint $script:config.POSTGRES_DB
            $after = Db-Fingerprint $scratch
            if ($before -ne $after) { throw '独立恢复库的逐表内容不一致。' }
        } finally {
            Compose exec -T postgres dropdb --username $script:config.POSTGRES_USER --if-exists $scratch
        }
        $manifest = [ordered]@{
            schema=2; created_at=[DateTimeOffset]::Now.ToString('o'); release_version=$script:config.RELEASE_VERSION
            database=$script:config.POSTGRES_DB
            dump_sha256=(Get-FileHash -LiteralPath (Join-Path $target 'database.dump') -Algorithm SHA256).Hash.ToLowerInvariant()
            media_hashes=Media-Hashes $backupMedia; verified_tables=@(($before | ConvertFrom-Json).PSObject.Properties).Count; restored=$true; reconciliation_checked=$true
            content_verified=$true; content_fingerprints=($before | ConvertFrom-Json)
        }
        $manifestJson = $manifest | ConvertTo-Json -Depth 8
        [IO.File]::WriteAllText(
            (Join-Path $target 'manifest.json'),
            $manifestJson,
            (New-Object Text.UTF8Encoding($false))
        )
        Write-Info "备份和独立恢复校验完成：$target"
    } finally {
        try { Compose exec -T postgres rm --force "/tmp/$dumpName" } catch {}
        if ($wasRunning) { Compose up --detach static web worker beat caddy }
    }
    $target
}
function Backup-Due {
    if ($NoAutoBackup) { return $false }
    $latest = Get-ChildItem -LiteralPath (Join-Path $dataRoot 'backups') -Filter manifest.json -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if (-not $latest) { return $true }
    $latest.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddHours(-[int]$script:config.AUTO_BACKUP_HOURS)
}
function Start-App([switch]$Initialize) {
    Ensure-Config
    Assert-Docker
    Assert-Port
    Compose up --detach --wait postgres redis
    Migrate
    Compose up --detach static web worker beat caddy
    Wait-Ready
    if (Backup-Due) {
        Write-Info '没有近期可恢复备份，正在自动备份。'
        Backup | Out-Host
        Wait-Ready
    }
}
function Restore {
    if (-not $ConfirmRestore) { throw '恢复会覆盖当前业务数据；请加 -ConfirmRestore。' }
    Ensure-Config
    Assert-Docker
    if (-not $BackupPath) { throw 'Restore 必须指定 -BackupPath。' }
    $selected = [IO.Path]::GetFullPath($BackupPath)
    $backupRoot = Assert-Child (Join-Path $dataRoot 'backups')
    $prefix = $backupRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $selected.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw '只能恢复当前数据目录中的备份。' }
    $manifestPath = Join-Path $selected 'manifest.json'
    $dumpPath = Join-Path $selected 'database.dump'
    if (-not (Test-Path -LiteralPath $manifestPath) -or -not (Test-Path -LiteralPath $dumpPath)) { throw '备份文件不完整。' }
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    if ((Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.dump_sha256) { throw '备份哈希不匹配。' }
    $expected = [ordered]@{}
    foreach ($property in $manifest.media_hashes.PSObject.Properties) { $expected[$property.Name]=$property.Value }
    if (((Media-Hashes (Join-Path $selected 'private-media')) | ConvertTo-Json -Compress) -ne ($expected | ConvertTo-Json -Compress)) { throw '备份附件哈希不一致，尚未覆盖当前数据。' }
    Write-Info '先为当前数据创建安全备份。'
    Backup | Out-Host
    $remoteDump = 'restore-' + (New-RandomHex 5) + '.dump'
    $mediaRoot = Assert-Child (Join-Path $dataRoot 'private-media')
    Compose stop caddy web worker beat
    try {
        Compose cp $dumpPath "postgres:/tmp/$remoteDump"
        $sql = "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$($script:config.POSTGRES_DB)' AND pid <> pg_backend_pid();"
        Compose exec -T postgres psql --username $script:config.POSTGRES_USER --dbname postgres --command $sql
        Compose exec -T postgres dropdb --username $script:config.POSTGRES_USER --if-exists $script:config.POSTGRES_DB
        Compose exec -T postgres createdb --username $script:config.POSTGRES_USER $script:config.POSTGRES_DB
        Compose exec -T postgres pg_restore --username $script:config.POSTGRES_USER --dbname $script:config.POSTGRES_DB --exit-on-error --no-owner "/tmp/$remoteDump"
        if ($manifest.content_verified) {
            $actualContent = (Db-Fingerprint $script:config.POSTGRES_DB) | ConvertFrom-Json | ConvertTo-Json -Depth 8 -Compress
            $expectedContent = $manifest.content_fingerprints | ConvertTo-Json -Depth 8 -Compress
            if ($actualContent -ne $expectedContent) { throw '恢复后的数据库内容摘要不一致。' }
        }
        if (Test-Path -LiteralPath $mediaRoot) {
            Assert-Child $mediaRoot | Out-Null
            Remove-Item -LiteralPath $mediaRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $mediaRoot | Out-Null
        $backupMedia = Join-Path $selected 'private-media'
        if (Test-Path -LiteralPath $backupMedia) {
            foreach ($item in Get-ChildItem -LiteralPath $backupMedia -Force) {
                if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { Copy-Item -LiteralPath $item.FullName -Destination $mediaRoot -Recurse }
            }
        }
        $expected = [ordered]@{}
        foreach ($property in $manifest.media_hashes.PSObject.Properties) { $expected[$property.Name]=$property.Value }
        if (((Media-Hashes $mediaRoot) | ConvertTo-Json -Compress) -ne ($expected | ConvertTo-Json -Compress)) { throw '恢复后的附件哈希不一致。' }
        Migrate
        Compose run --rm -T web python manage.py reconcile_business_data --check
        Compose up --detach static web worker beat caddy
        Wait-Ready
        Write-Info "已恢复备份：$selected"
    } finally {
        try { Compose exec -T postgres rm --force "/tmp/$remoteDump" } catch {}
    }
}
function Backup-TaskName { 'XianyuSellerBackup-' + $script:config.COMPOSE_PROJECT_NAME }
function Schedule-Backup {
    Ensure-Config
    $taskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Action Backup -DataDir "{1}"' -f [IO.Path]::GetFullPath($PSCommandPath),$dataRoot
    $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments
    $taskTrigger = New-ScheduledTaskTrigger -Daily -At '03:00'
    Register-ScheduledTask -TaskName (Backup-TaskName) -Action $taskAction -Trigger $taskTrigger -Description 'Daily verified backup for Xianyu Seller Local' -Force | Out-Null
    Write-Info '已注册每天 03:00 的备份任务；关机漏执行时由下一次 Start 补备。'
}
function Remove-BackupSchedule {
    Ensure-Config
    Unregister-ScheduledTask -TaskName (Backup-TaskName) -Confirm:$false -ErrorAction SilentlyContinue
    Write-Info '已移除定时备份任务。'
}

Assert-SafeRoot
switch ($Action) {
    'Install' { Ensure-Config; Assert-Docker; Assert-Port; Compose build --pull; Start-App -Initialize }
    'Start' { Start-App }
    'Restart' { Ensure-Config; Assert-Docker; Compose stop caddy web worker beat; Start-App }
    'Stop' { Ensure-Config; Assert-Docker; Compose stop; Write-Info '后台已停止，数据和备份均已保留。' }
    'Status' {
        Ensure-Config; Assert-Docker
        Write-Info "数据目录：$dataRoot"; Write-Info "访问地址：$($script:config.PUBLIC_BASE_URL)"; Compose ps
        $latest = Get-ChildItem -LiteralPath (Join-Path $dataRoot 'backups') -Filter manifest.json -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        if ($latest) { Write-Info "最近备份：$($latest.DirectoryName)" } else { Write-Info '尚无已验证备份。' }
    }
    'Backup' { Backup | Out-Host }
    'Restore' { Restore }
    'Upgrade' {
        Ensure-Config; Assert-Docker
        if ((Test-Path -LiteralPath (Join-Path $dataRoot 'postgres/PG_VERSION')) -or -not [string]::IsNullOrWhiteSpace((Compose-Text ps --all --quiet postgres))) { Backup | Out-Host }
        $script:config.RELEASE_VERSION=$releaseVersion
        $script:config.APP_IMAGE="xianyu-seller-local:$releaseVersion"
        Save-Config
        Compose build --pull
        Start-App
    }
    'Configure' {
        Ensure-Config
        $web = Get-WebConfig $PublicUrl $BindAddress $Port
        $script:config.APP_PORT=[string]$Port; $script:config.BIND_ADDRESS=$BindAddress
        $script:config.PUBLIC_BASE_URL=$web.Url; $script:config.DJANGO_ALLOWED_HOSTS=$web.Hosts
        $script:config.DJANGO_CSRF_TRUSTED_ORIGINS=$web.Origins
        Save-Config
        Write-Info "访问地址已更新为 $($web.Url)，重新 Start 后生效。"
    }
    'ScheduleBackup' { Schedule-Backup }
    'RemoveBackupSchedule' { Remove-BackupSchedule }
    'Validate' { Ensure-Config; Assert-Docker; Compose config --quiet; Compose run --rm -T web python manage.py check; Write-Info '配置检查通过。' }
}
