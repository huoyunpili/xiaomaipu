[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskId = [Guid]::NewGuid().ToString('N').Substring(0, 10)
$taskNetwork = "seller-regression-$taskId"
$taskDatabase = "seller-regression-db-$taskId"
$taskImage = "xianyu-seller:regression-$taskId"
$taskPassword = "regression-$taskId-isolated"
$taskNetworkCreated = $false
$taskDatabaseCreated = $false
$taskImageCreated = $false
function Invoke-TaskDocker {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE) { throw "Release verification Docker command failed: $($Arguments[0])" }
}
try {
    Invoke-TaskDocker -Arguments @('build', '-f', (Join-Path $taskRoot 'deployment/Dockerfile'), '-t', $taskImage, '--build-arg', 'APP_VERSION=regression', $taskRoot)
    $taskImageCreated = $true
    Invoke-TaskDocker -Arguments @('network', 'create', '--internal', $taskNetwork)
    $taskNetworkCreated = $true
    Invoke-TaskDocker -Arguments @('run', '-d', '--name', $taskDatabase, '--network', $taskNetwork, '-e', 'POSTGRES_DB=regression_release', '-e', 'POSTGRES_USER=regression', '-e', "POSTGRES_PASSWORD=$taskPassword", 'postgres:17.6')
    $taskDatabaseCreated = $true
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        & docker exec $taskDatabase pg_isready -U regression -d regression_release *> $null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    if ($LASTEXITCODE) { throw 'Isolated verification database did not become ready.' }
    $taskProbe = Join-Path $taskRoot 'scripts/verify_daily_release.py'
    Invoke-TaskDocker -Arguments @('run', '--rm', '--network', $taskNetwork, '--mount', "type=bind,source=$taskProbe,target=/tmp/verify_daily_release.py,readonly", '-e', 'PYTHONPATH=/srv/app', '-e', 'DJANGO_SETTINGS_MODULE=app.config.settings.local_release', '-e', "DJANGO_SECRET_KEY=regression-only-isolated-secret-$taskId-$taskId-$taskId", '-e', 'DJANGO_ALLOWED_HOSTS=testserver,localhost', '-e', "POSTGRES_HOST=$taskDatabase", '-e', 'POSTGRES_PORT=5432', '-e', 'POSTGRES_DB=regression_release', '-e', 'POSTGRES_USER=regression', '-e', "POSTGRES_PASSWORD=$taskPassword", $taskImage, 'python', '/tmp/verify_daily_release.py')
    Write-Output 'Release image, migrations, rebuild and runtime checks passed.'
} finally {
    # These names are generated above and belong only to this isolated verification.
    if ($taskDatabaseCreated) { & docker rm --force --volumes $taskDatabase | Out-Null }
    if ($taskNetworkCreated) { & docker network rm $taskNetwork | Out-Null }
    if ($taskImageCreated) { & docker image rm $taskImage | Out-Null }
}
