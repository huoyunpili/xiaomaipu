param([switch]$Once)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskLocal = Join-Path $taskRoot '.local'
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskStatePath = Join-Path $taskLocal 'supplier-runtime.json'
New-Item -ItemType Directory -Force -Path $taskLocal | Out-Null
$taskHash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot.ToLowerInvariant()))).Replace('-', '').Substring(0,16)
$taskMutex = [Threading.Mutex]::new($false, "Local\SellerSupplier-$taskHash")
$taskLocked = $false
try {
    try { $taskLocked = $taskMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $taskLocked = $true }
    if (-not $taskLocked) { Write-Output 'Supplier supervisor already running.'; exit 0 }
    Set-Content -LiteralPath (Join-Path $taskLocal 'supplier-watch.pid') -Value $PID
    function Save-State($state) {
        $taskTemp = "$taskStatePath.tmp"
        $state | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $taskTemp
        Move-Item -LiteralPath $taskTemp -Destination $taskStatePath -Force
    }
    function Write-Event($message) {
        $taskLog = Join-Path $taskLocal 'supplier-watch.log'
        if ((Test-Path $taskLog) -and (Get-Item $taskLog).Length -gt 2MB) {
            Move-Item -LiteralPath $taskLog -Destination "$taskLog.previous" -Force
        }
        Add-Content -LiteralPath $taskLog -Value "$(Get-Date -Format o) $message"
    }
    function Test-Gateway($port) {
        try {
            $taskHealth = Invoke-RestMethod -TimeoutSec 3 -Uri "http://127.0.0.1:$port/supplier/health/"
            return $taskHealth.service -eq 'supplier-upload-gateway'
        } catch { return $false }
    }
    $taskGatewayChild = $null
    do {
        try {
            if (Test-Path -LiteralPath $taskStatePath) {
                $taskState = Get-Content -LiteralPath $taskStatePath -Raw | ConvertFrom-Json
            } else {
                $taskState = [pscustomobject]@{port=18766; gateway=0; tunnel=0; url=''; urlSaved=$false}
                Save-State $taskState
            }
            if ($taskState.port -lt 1024 -or $taskState.port -gt 65535) { throw 'Invalid supplier port.' }
            if (-not (Test-Gateway $taskState.port)) {
                # Never stop active uploads or another application occupying the port.
                $taskListener = @(Get-NetTCPConnection -LocalPort $taskState.port -State Listen -ErrorAction SilentlyContinue)
                if ($taskListener.Count) { throw 'Port occupied but health check failed; no process was stopped.' }
                if ($taskGatewayChild) {
                    $taskGatewayChild.Refresh()
                    if (-not $taskGatewayChild.HasExited) { throw 'Gateway is still starting; waiting.' }
                }
                $taskGatewayChild = Start-Process -FilePath $taskPython -ArgumentList @('manage.py','serve_supplier',"--port=$($taskState.port)",'--settings=app.config.settings.supplier') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLocal 'supplier.out.log') -RedirectStandardError (Join-Path $taskLocal 'supplier.err.log')
                $taskState.gateway = $taskGatewayChild.Id
                Save-State $taskState
                Write-Event 'Started supplier gateway; existing tunnel and URL preserved.'
                $taskDeadline = [DateTime]::UtcNow.AddSeconds(40)
                while (-not (Test-Gateway $taskState.port)) {
                    $taskGatewayChild.Refresh()
                    if ($taskGatewayChild.HasExited -or [DateTime]::UtcNow -gt $taskDeadline) { throw 'Gateway did not become ready.' }
                    Start-Sleep -Seconds 1
                }
            }
            $taskTunnel = Join-Path $taskLocal 'tools/cloudflared-verified.exe'
            if (-not (Test-Path -LiteralPath $taskTunnel)) { $taskTunnel = Join-Path $taskLocal 'tools/cloudflared.exe' }
            $taskCloud = $null
            if ([int]$taskState.tunnel -gt 0) {
                $taskCloud = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$taskState.tunnel)" -ErrorAction SilentlyContinue
            }
            if ($taskCloud -and (-not $taskCloud.ExecutablePath -or [IO.Path]::GetFullPath($taskCloud.ExecutablePath) -ne [IO.Path]::GetFullPath($taskTunnel) -or $taskCloud.CommandLine -notlike "*http://127.0.0.1:$($taskState.port)*")) {
                Write-Event 'Stale tunnel PID belongs to another process; left it untouched.'
                $taskCloud = $null
            }
            if (-not $taskCloud) {
                if (-not (Test-Path -LiteralPath $taskTunnel)) { throw 'Install cloudflared in .local/tools first.' }
                $taskTunnelChild = Start-Process -FilePath $taskTunnel -ArgumentList @('tunnel','--url',"http://127.0.0.1:$($taskState.port)",'--no-autoupdate','--protocol','http2') -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLocal 'supplier-tunnel.out.log') -RedirectStandardError (Join-Path $taskLocal 'supplier-tunnel.err.log')
                $taskState.tunnel = $taskTunnelChild.Id
                $taskState.url = ''
                $taskState | Add-Member -NotePropertyName urlSaved -NotePropertyValue $false -Force
                Save-State $taskState
                Write-Event 'Started temporary tunnel. A new URL must be shared with suppliers.'
            }
            if (-not $taskState.url) {
                $taskContent = Get-Content -LiteralPath (Join-Path $taskLocal 'supplier-tunnel.err.log') -Raw -ErrorAction SilentlyContinue
                $taskMatch = [regex]::Match([string]$taskContent, 'https://[a-z0-9-]+\.trycloudflare\.com')
                if (-not $taskMatch.Success) { throw 'Waiting for temporary tunnel URL.' }
                $taskState.url = $taskMatch.Value
                Save-State $taskState
            }
            if ($taskState.PSObject.Properties['urlSaved'] -and -not $taskState.urlSaved) {
                Push-Location $taskRoot
                try {
                    & $taskPython manage.py set_supplier_url $taskState.url
                    if ($LASTEXITCODE) { throw 'Database not ready to save URL; will retry.' }
                } finally { Pop-Location }
                $taskState.urlSaved = $true
                Save-State $taskState
                Write-Event 'Saved current supplier URL to workspace settings.'
            }
            if ($Once) { Write-Output 'Supplier gateway and tunnel are running.' }
        } catch {
            Write-Event $_.Exception.Message
            if ($Once) { throw }
        }
        if (-not $Once) { Start-Sleep -Seconds 10 }
    } while (-not $Once)
} finally {
    if ($taskLocked) { $taskMutex.ReleaseMutex() }
    $taskMutex.Dispose()
}
