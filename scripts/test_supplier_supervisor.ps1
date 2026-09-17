$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskTest = Join-Path $taskRoot '.local/supervisor-test'
New-Item -ItemType Directory -Force $taskTest | Out-Null
$taskSource = Get-Content (Join-Path $taskRoot 'scripts/watch_supplier.ps1') -Raw
$taskSource = $taskSource.Replace("Join-Path `$taskRoot '.local'", "Join-Path `$taskRoot '.local/supervisor-test'")
$taskSource = $taskSource.Replace('Local\SellerSupplier-', 'Local\TestSellerSupplier-')
$taskScript = Join-Path $taskRoot '.local/test-watch.ps1'
Set-Content -Encoding UTF8 $taskScript $taskSource
$taskSocket = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
$taskSocket.Start()
$global:taskTestPort = $taskSocket.LocalEndpoint.Port
$taskSocket.Stop()
$global:taskRealTunnel = Join-Path $taskRoot '.local/tools/cloudflared-verified.exe'
New-Item -ItemType Directory -Force (Join-Path $taskTest 'tools') | Out-Null
$global:taskFakeTunnel = Join-Path $taskTest 'tools/cloudflared-verified.exe'
if (-not (Test-Path $global:taskFakeTunnel)) { New-Item -ItemType File $global:taskFakeTunnel | Out-Null }
function Get-CimInstance { [pscustomobject]@{ExecutablePath=$global:taskFakeTunnel;CommandLine="tunnel --url http://127.0.0.1:$global:taskTestPort"} }
@{port=$global:taskTestPort;gateway=0;tunnel=12345;url='https://test-preserved.trycloudflare.com'} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $taskTest 'supplier-runtime.json')
try {
    & $taskScript -Once
    $taskFirst = Get-NetTCPConnection -LocalPort $global:taskTestPort -State Listen
    Stop-Process -Id $taskFirst.OwningProcess
    Start-Sleep -Seconds 1
    & $taskScript -Once
    $taskSecond = Get-NetTCPConnection -LocalPort $global:taskTestPort -State Listen
    if ($taskSecond.OwningProcess -eq $taskFirst.OwningProcess) { throw 'Gateway was not replaced.' }
    $taskFinal = Get-Content (Join-Path $taskTest 'supplier-runtime.json') -Raw | ConvertFrom-Json
    if ($taskFinal.url -ne 'https://test-preserved.trycloudflare.com') { throw 'URL changed.' }
    Write-Output 'PASS: isolated gateway startup, crash recovery, preserved URL.'
} finally {
    Get-NetTCPConnection -LocalPort $global:taskTestPort -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue }
}
