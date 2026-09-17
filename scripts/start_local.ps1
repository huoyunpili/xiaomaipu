param([switch]$Restart)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskLocal = Join-Path $taskRoot '.local'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Project Python not found.' }
New-Item -ItemType Directory -Force -Path $taskLocal | Out-Null
$taskProcesses = @(Get-CimInstance Win32_Process)
$taskParents = @($taskProcesses | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($taskPython.Replace('/', '\')) -and
    $_.CommandLine -match 'manage.py runserver|celery.*app.config.celery'
})
if ($taskParents.Count -and -not $Restart) { throw 'Local services already running; use -Restart.' }
if ($Restart) {
    $taskIds = @($taskParents | ForEach-Object { $_.ProcessId })
    $taskChildren = @($taskProcesses | Where-Object {
        $_.ParentProcessId -in $taskIds -and $_.CommandLine -match 'manage.py runserver|celery.*app.config.celery'
    })
    foreach ($taskProcess in @($taskChildren) + @($taskParents)) {
        Stop-Process -Id $taskProcess.ProcessId -ErrorAction SilentlyContinue
    }
}
$taskSpecs = @(
    @{ Name='server'; Args=@('manage.py','runserver','127.0.0.1:8765','--noreload') },
    @{ Name='worker'; Args=@('-m','celery','-A','app.config.celery','worker','--pool=solo','--concurrency=1','--loglevel=INFO') },
    @{ Name='beat'; Args=@('-m','celery','-A','app.config.celery','beat','--schedule=.local/celerybeat-schedule','--loglevel=INFO') }
)
$taskStarted = @()
foreach ($taskSpec in $taskSpecs) {
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskSpec.Args -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLocal ($taskSpec.Name+'.out.log')) -RedirectStandardError (Join-Path $taskLocal ($taskSpec.Name+'.err.log'))
    $taskStarted += @{ name=$taskSpec.Name; pid=$taskProcess.Id }
}
$taskStarted | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $taskLocal 'runtime.json')
$taskDeadline = [DateTime]::UtcNow.AddSeconds(45)
$taskReady = $false
do {
    foreach ($taskEntry in $taskStarted) {
        if (-not (Get-Process -Id $taskEntry.pid -ErrorAction SilentlyContinue)) {
            throw "Service $($taskEntry.name) exited; inspect .local/$($taskEntry.name).err.log."
        }
    }
    try {
        $taskWeb = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://127.0.0.1:8765/health/ready/'
        if ($taskWeb.StatusCode -eq 200) {
            & $taskPython -m celery -A app.config.celery inspect ping --timeout=3 *> (Join-Path $taskLocal 'startup-worker-check.log')
            if ($LASTEXITCODE -eq 0) { $taskReady = $true; break }
        }
    } catch {}
    Start-Sleep -Seconds 1
} while ([DateTime]::UtcNow -lt $taskDeadline)
if (-not $taskReady) { throw 'Web or synchronization worker is not ready; inspect .local service logs.' }
& (Join-Path $PSScriptRoot 'start_supplier.ps1')
$taskStarted | ConvertTo-Json
