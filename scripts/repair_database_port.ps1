param([string]$AppRoot = '', [string]$DataRoot = '', [string]$ReportPath = '')
$ErrorActionPreference = 'Stop'
$repairDirectory = if ($env:FM_REPAIR_SELF) { [IO.Path]::GetDirectoryName($env:FM_REPAIR_SELF) } else { $PSScriptRoot }
$reportName = 'FishManager-repair-result-v2.txt'
$report = New-Object System.Collections.Generic.List[string]
$repairExit = 1
$candidate = $null
$phase = 'locate-installation'
function Test-FishManagerRoot([string]$Path) {
    if (-not $Path -or $Path.Trim().Trim('"') -notmatch '^[A-Za-z]:[\\/]') { return $false }
    try {
        $root = [IO.Path]::GetFullPath($Path.Trim().Trim('"'))
        foreach ($name in @('FishManager.exe','runtime\postgresql\bin\postgres.exe','runtime\postgresql\bin\pg_ctl.exe')) {
            if (-not [IO.File]::Exists([IO.Path]::Combine($root, $name))) { return $false }
        }
        return $true
    } catch { return $false }
}
function Find-FishManagerRoot([string]$ExplicitRoot, [string]$RepairDirectory) {
    if ($ExplicitRoot) {
        if (-not (Test-FishManagerRoot $ExplicitRoot)) { throw 'The selected folder does not contain FishManager and its bundled database. No files changed.' }
        return [IO.Path]::GetFullPath($ExplicitRoot.Trim().Trim('"'))
    }
    # A repair placed beside the application is an explicit local selection.
    if (Test-FishManagerRoot $RepairDirectory) { return [IO.Path]::GetFullPath($RepairDirectory) }
    $found = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $registryPaths = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
    foreach ($entry in @(Get-ItemProperty -Path $registryPaths -ErrorAction SilentlyContinue)) {
        if ($entry.PSChildName -ne '{6B9201EF-7FA7-47CC-8E97-8ACB452F2A3F}_is1' -and $entry.DisplayName -notmatch '^(\u9c7c\u7ba1\u5bb6|Fish\s?Manager)(\s|$)') { continue }
        # .NET path and file APIs return false for missing drives. Join-Path
        # resolves PowerShell drives and would abort on an unrelated stale E:.
        if (Test-FishManagerRoot $entry.InstallLocation) {
            [void]$found.Add([IO.Path]::GetFullPath($entry.InstallLocation.Trim().Trim('"')))
        }
    }
    if ($found.Count -eq 0 -and $env:LOCALAPPDATA) {
        $defaultRoot = [IO.Path]::Combine($env:LOCALAPPDATA, 'Programs\FishManager')
        if (Test-FishManagerRoot $defaultRoot) { [void]$found.Add($defaultRoot) }
    }
    if ($found.Count -gt 1) { throw 'Multiple FishManager installations found. Put this repair file beside the FishManager.exe you use, then run it again. No files changed.' }
    if ($found.Count -eq 0) { throw 'FishManager installation not found. Put this repair file beside FishManager.exe, then run it again. No files changed.' }
    return @($found)[0]
}
function Probe([string]$exe, [string]$arguments) {
    $p = New-Object Diagnostics.Process
    $p.StartInfo.FileName = $exe
    $p.StartInfo.Arguments = $arguments
    $p.StartInfo.WorkingDirectory = Split-Path $exe -Parent
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.CreateNoWindow = $true
    $p.StartInfo.RedirectStandardOutput = $true
    $p.StartInfo.RedirectStandardError = $true
    try {
        [void]$p.Start()
        $stdout = $p.StandardOutput.ReadToEndAsync()
        $stderr = $p.StandardError.ReadToEndAsync()
        if (-not $p.WaitForExit(15000)) { $p.Kill(); $p.WaitForExit(); throw 'Database check timed out; no repair applied.' }
        [pscustomobject]@{Code=$p.ExitCode; Output=$stdout.Result; Text=($stdout.Result + $stderr.Result)}
    } finally { $p.Dispose() }
}
try {
    $report.Add('FishManager database port repair v2 - safe installation discovery')
    $report.Add((Get-Date -Format o))
    $AppRoot = Find-FishManagerRoot $AppRoot $repairDirectory
    if (-not $DataRoot) { $DataRoot = [IO.Path]::Combine($env:LOCALAPPDATA, 'XianyuSeller') }
    $DataRoot = [IO.Path]::GetFullPath($DataRoot)
    $report.Add('App=' + $AppRoot)
    $report.Add('Data=' + $DataRoot)
    $phase = 'check-existing-database'
    if (-not [IO.File]::Exists([IO.Path]::Combine($DataRoot, 'postgres\PG_VERSION'))) { throw 'Existing database not found in the selected data folder. No database was created or changed.' }
    $cluster = Join-Path $DataRoot 'postgres'
    $config = Join-Path $cluster 'postgresql.conf'
    $postgres = Join-Path $AppRoot 'runtime\postgresql\bin\postgres.exe'
    $pgctl = Join-Path $AppRoot 'runtime\postgresql\bin\pg_ctl.exe'
    $state = Probe $pgctl ('status -D "' + $cluster + '"')
    if ($state.Code -eq 0) { throw 'Database is running. Fully exit FishManager, then retry.' }
    if ($state.Code -ne 3) { throw 'Cannot verify that the database is stopped. No repair applied.' }
    $phase = 'validate-configuration'
    $original = [IO.File]::ReadAllBytes($config)
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    $text = $utf8.GetString($original)
    $before = Probe $postgres ('-D "' + $cluster + '" -C port')
    if ($before.Code -eq 0) {
        $report.Add('Configuration already valid. No files changed.')
        $repairExit = 0
    } else {
        $report.Add('PostgreSQL configuration check exit=' + $before.Code)
        $phase = 'repair-known-port-block'
        # Restrict repair to the exact three settings appended by our installer.
        $pattern = '(?m)^(listen_addresses = ''127\.0\.0\.1''\r?\n)port = [^\r\n]*(\r?\nmax_connections = 40\r?$)'
        $matches = [regex]::Matches($text, $pattern)
        if ($matches.Count -ne 1) { throw 'Configuration does not match the known installer block. No files changed.' }
        $fixed = [regex]::Replace($text, $pattern, '${1}port = 55433${2}')
        if ($fixed -eq $text) { throw 'Port is already correct; another syntax error needs inspection. No files changed.' }
        $candidate = $config + '.repair-' + [Guid]::NewGuid().ToString('N')
        [IO.File]::WriteAllText($candidate, $fixed, $utf8)
        $verified = Probe $postgres ('-D "' + $cluster + '" -c "config_file=' + $candidate + '" -C port')
        if ($verified.Code -ne 0 -or $verified.Output.Trim() -ne '55433') { throw 'Repaired candidate did not pass PostgreSQL validation. Original preserved.' }
        # Recheck for another launch or edits before atomically replacing the file.
        $state = Probe $pgctl ('status -D "' + $cluster + '"')
        if ($state.Code -ne 3) { throw 'Database state changed. Original preserved.' }
        if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($config)) -ne [Convert]::ToBase64String($original)) { throw 'Configuration changed during diagnosis. Original preserved.' }
        $backup = $config + '.before-repair-' + [Guid]::NewGuid().ToString('N') + '.bak'
        [IO.File]::Replace($candidate, $config, $backup)
        $candidate = $null
        if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($backup)) -ne [Convert]::ToBase64String($original)) { throw 'Backup verification failed. Stop and send this report.' }
        $after = Probe $postgres ('-D "' + $cluster + '" -C port')
        if ($after.Code -ne 0) {
            [IO.File]::Copy($backup, $config, $true)
            throw 'Final validation failed. Original configuration restored.'
        }
        $report.Add('SUCCESS: repaired port setting and verified with PostgreSQL.')
        $report.Add('Original configuration backup: ' + [IO.Path]::GetFileName($backup))
        $report.Add('No databases, orders, accounts, or API settings were changed.')
        $report.Add('Please reopen FishManager. This checks configuration, not full application startup.')
        $repairExit = 0
    }
} catch {
    $report.Add('Phase=' + $phase)
    $report.Add('STOPPED: ' + $_.Exception.Message)
    $report.Add('Please send this report for follow-up.')
} finally {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { Remove-Item -LiteralPath $candidate -Force }
    $saved = $false
    $targets = if ($ReportPath) { @($ReportPath) } else {
        @([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), $reportName), [IO.Path]::Combine($repairDirectory, $reportName), [IO.Path]::Combine([IO.Path]::GetTempPath(), $reportName))
    }
    foreach ($target in $targets) {
        try {
            [IO.File]::WriteAllLines($target, $report, (New-Object Text.UTF8Encoding($true)))
            $ReportPath = $target; $saved = $true; break
        } catch { }
    }
    $report | ForEach-Object { Write-Host $_ }
    if ($saved) { Write-Host ('Report: ' + $ReportPath) } else { Write-Host 'Report could not be saved. Please capture this window.'; $repairExit = 1 }
}
exit $repairExit
