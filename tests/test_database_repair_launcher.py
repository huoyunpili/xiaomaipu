import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows repair launcher")
def test_discovery_skips_missing_drives_and_refuses_ambiguous_installs(tmp_path):
    repair = str(ROOT / "scripts/repair_database_port.ps1").replace("'", "''")
    script = tmp_path / "discovery.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{repair}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'Parse error' }}
foreach ($name in @('Test-FishManagerRoot','Find-FishManagerRoot')) {{
    $node=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}},$true)
    Invoke-Expression $node.Extent.Text
}}
$missing=@('Z','Y','X','W','V','U' | Where-Object {{ -not (Get-PSDrive -Name $_ -ErrorAction SilentlyContinue) }})[0]
if (-not $missing) {{ throw 'Missing drive fixture unavailable' }}
$stale=$missing + ':\\retired-app'
$oldFailed=$false
try {{ Join-Path $stale 'FishManager.exe' | Out-Null }} catch {{ $oldFailed=$true }}
if (-not $oldFailed) {{ throw 'Original missing-drive failure was not reproduced' }}
$valid=Join-Path $PSScriptRoot 'actual installation'
New-Item -ItemType Directory -Path ($valid+'\\runtime\\postgresql\\bin') -Force | Out-Null
foreach ($name in @('FishManager.exe','runtime\\postgresql\\bin\\postgres.exe','runtime\\postgresql\\bin\\pg_ctl.exe')) {{
    [IO.File]::WriteAllText([IO.Path]::Combine($valid,$name),'fixture')
}}
$script:entries=@(
    [pscustomobject]@{{DisplayName='Unrelated program';InstallLocation=$stale}},
    [pscustomobject]@{{DisplayName='FishManager';InstallLocation=$stale}},
    [pscustomobject]@{{DisplayName='FishManager';InstallLocation=$valid}}
)
function Get-ItemProperty {{ param($Path,$ErrorAction) return $script:entries }}
if ((Find-FishManagerRoot '' '') -ne $valid) {{ throw 'Valid installation not found after stale entries' }}
if (Test-FishManagerRoot $stale) {{ throw 'Missing installation accepted' }}
$second=Join-Path $PSScriptRoot 'second'
Copy-Item -LiteralPath $valid -Destination $second -Recurse
$script:entries += [pscustomobject]@{{DisplayName='FishManager';InstallLocation=$second}}
$failed=$false
try {{ Find-FishManagerRoot '' '' | Out-Null }} catch {{ $failed=$true }}
if (-not $failed) {{ throw 'Ambiguous installations selected silently' }}
if ((Find-FishManagerRoot '' $valid) -ne $valid) {{ throw 'Adjacent repair selection ignored' }}
if ((Find-FishManagerRoot $second '') -ne $second) {{ throw 'Explicit installation ignored' }}
$script:entries=@([pscustomobject]@{{DisplayName='Unrelated program';InstallLocation=$valid}})
$env:LOCALAPPDATA=Join-Path $PSScriptRoot 'empty-profile'
$failed=$false
try {{ Find-FishManagerRoot '' '' | Out-Null }} catch {{ $failed=$true }}
if (-not $failed) {{ throw 'Unrelated application selected' }}
""",
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows repair launcher")
def test_single_file_cmd_reports_missing_installation_without_touching_data(tmp_path):
    launcher = tmp_path / "repair v2.cmd"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_database_repair.py"), str(launcher)], check=True
    )
    import os

    report = tmp_path / "result.txt"
    data = tmp_path / "preserved-data"
    data.mkdir()
    sentinel = data / "sentinel.txt"
    sentinel.write_text("must remain unchanged", encoding="utf-8")
    result = subprocess.run(
        [str(launcher), str(tmp_path / "missing-app"), str(data), str(report)],
        env=dict(os.environ, FM_REPAIR_NO_PAUSE="1"),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    contents = report.read_text(encoding="utf-8-sig")
    assert "Phase=locate-installation" in contents
    assert "selected folder does not contain FishManager" in contents
    assert sentinel.read_text(encoding="utf-8") == "must remain unchanged"
    assert list(data.iterdir()) == [sentinel]
