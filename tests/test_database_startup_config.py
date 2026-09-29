import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell regression")
ROOT = Path(__file__).parents[1]


def run_script(tmp_path, body):
    launcher = str(ROOT / "scripts/windows_release.ps1").replace("'", "''")
    script = tmp_path / "check.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) {{ throw 'Launcher syntax error' }}
foreach ($name in @('Get-DatabasePort','Import-Config','Ensure-PostgresConfig')) {{
    $node = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }}, $true)
    Invoke-Expression $node.Extent.Text
}}
function Write-Info($value) {{ }}
$dataRoot = $PSScriptRoot
$pgData = Join-Path $dataRoot 'postgres'
New-Item -ItemType Directory -Path $pgData | Out-Null
"""
        + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace") + result.stderr.decode(
        errors="replace"
    )


def test_empty_or_invalid_cached_port_does_not_override_valid_configuration(tmp_path):
    run_script(
        tmp_path,
        """
$configFile = Join-Path $dataRoot 'config.env'
[IO.File]::WriteAllText($configFile, "POSTGRES_PORT=55434`nPUBLIC_BASE_URL=http://127.0.0.1:8765`n")
foreach ($value in @('', '0', '65536', 'abc', "'55435'")) {
    [IO.File]::WriteAllText((Join-Path $dataRoot 'database-port.txt'), $value)
    Import-Config
    if ($env:POSTGRES_PORT -ne '55434') { throw 'Invalid cache overrode configuration' }
}
[IO.File]::WriteAllText((Join-Path $dataRoot 'database-port.txt'), '55435')
Import-Config
if ($env:POSTGRES_PORT -ne '55435') { throw 'Valid cache ignored' }
if ((Get-DatabasePort '') -ne 55433) { throw 'Default port missing' }
""",
    )


def test_port_repair_preserves_backup_and_refuses_unrelated_errors(tmp_path):
    run_script(
        tmp_path,
        """
function Test-PostgresConfig($path) {
    $contents = [IO.File]::ReadAllText($path)
    return ($contents -match '(?m)^port = 55433$' -and $contents -notmatch 'unrelated-invalid')
}
$config = Join-Path $pgData 'postgresql.conf'
$broken = "# preserved comment`nlisten_addresses = '127.0.0.1'`nport = `nmax_connections = 40`n"
[IO.File]::WriteAllText($config, $broken)
Ensure-PostgresConfig
$backup = @(Get-ChildItem -LiteralPath $pgData -Filter '*.bak')
if ($backup.Count -ne 1 -or [IO.File]::ReadAllText($backup[0].FullName) -ne $broken) { throw 'Original backup not preserved' }
Ensure-PostgresConfig
if (@(Get-ChildItem -LiteralPath $pgData -Filter '*.bak').Count -ne 1) { throw 'Repeated repair changed valid configuration' }
$other = $broken + 'unrelated-invalid'
[IO.File]::WriteAllText($config, $other)
$failed = $false
try { Ensure-PostgresConfig } catch { $failed = $true }
if (-not $failed -or [IO.File]::ReadAllText($config) -ne $other) { throw 'Unrelated error changed original configuration' }
if (@(Get-ChildItem -LiteralPath $pgData -Filter '*.repair-*').Count) { throw 'Candidate file left behind' }
""",
    )
