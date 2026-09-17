[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$taskLocal = Join-Path $taskRoot '.local'
$taskWatch = Join-Path $PSScriptRoot 'watch_supplier.ps1'
New-Item -ItemType Directory -Force -Path $taskLocal | Out-Null
$taskExisting = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($taskWatch)
})
if (-not $taskExisting.Count) {
    Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$taskWatch+'"')) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLocal 'supplier-watch.out.log') -RedirectStandardError (Join-Path $taskLocal 'supplier-watch.err.log') | Out-Null
}
Write-Output 'Supplier supervisor enabled. Status: .local/supplier-watch.log; current URL: .local/supplier-runtime.json'
