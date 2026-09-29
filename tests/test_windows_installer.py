import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_windows_release_uses_bundled_postgres_without_redis_or_docker(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "app.config.settings.windows_release",
            "DJANGO_SECRET_KEY": "a" * 64,
            "POSTGRES_PASSWORD": "private-test-password",
            "FISH_MANAGER_DATA": str(tmp_path),
            "PUBLIC_BASE_URL": "http://127.0.0.1:18765",
        }
    )
    code = (
        "import django; django.setup(); "
        "from django.conf import settings; "
        "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'; "
        "assert settings.CELERY_BROKER_URL == 'filesystem://'; "
        "assert settings.PRIVATE_MEDIA_ROOT.name == 'private-media'; "
        "from pathlib import Path; import os; "
        "assert settings.XGJ_CREDENTIALS_FILE == Path(os.environ['FISH_MANAGER_DATA']).resolve() / 'xgj-api.json'; "
        "assert settings.CELERY_BROKER_TRANSPORT_OPTIONS['control_folder'].endswith('control'); "
        "assert 'http://127.0.0.1:18765' in settings.CSRF_TRUSTED_ORIGINS; "
        "assert 'whitenoise.middleware.WhiteNoiseMiddleware' in settings.MIDDLEWARE"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_windows_installer_is_the_single_complete_user_path():
    runtime_script = (ROOT / "scripts/windows_release.ps1").read_text(encoding="utf-8")
    build_script = (ROOT / "scripts/build_windows_installer.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "installer/fish-manager.iss").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Docker" not in runtime_script
    assert "redis" not in runtime_script.lower()
    assert "postgresql/bin" in runtime_script
    assert "tunnel=@(" not in runtime_script
    assert "'cloudflared.exe'),@('tunnel'" not in runtime_script
    assert "trycloudflare.com" not in runtime_script
    assert "SUPPLIER_PUBLIC_URL" not in runtime_script
    assert "serve_supplier" not in runtime_script
    assert "supplier=@(" not in runtime_script
    assert "README.md') -Destination $stageRoot" not in build_script
    assert "Join-Path $projectRoot 'docs'" not in build_script
    assert "Join-Path $projectRoot 'LICENSE'" in build_script
    assert "Join-Path $projectRoot 'NOTICE'" in build_script
    assert "cloudflared" not in build_script.lower()
    assert "../site-packages/win32/lib" in build_script
    assert "pywin32>=306" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "Remove-ObsoleteTunnel" in runtime_script
    assert (
        "Refusing to remove an obsolete tunnel outside the application directory" in runtime_script
    )
    assert "'Install' {\n        Stop-All\n        Remove-ObsoleteTunnel" in runtime_script
    assert "LicenseFile={#SourceRoot}\\LICENSE" in installer
    assert "AppVersion=0.7.1" in installer
    assert "OutputBaseFilename=鱼管家-0.7.1-安装程序" in installer
    assert 'Filename: "{app}\\FishManager.exe"' in installer
    assert "{userstartup}" in installer
    assert "{userdesktop}\\鱼管家" in installer
    assert "ResultCode <> 0" in installer
    assert "PrepareToInstall" in installer
    assert "普通用户只保留一种方式" in readme


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell regression")
def test_stop_before_first_install_does_not_call_pg_ctl(tmp_path):
    """The first install must not stop an absent PostgreSQL cluster."""
    script = tmp_path / "stop-fresh.ps1"
    launcher = str(ROOT / "scripts/windows_release.ps1").replace("'", "''")
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) {{ throw 'Invalid launcher syntax' }}
foreach ($name in @('Test-PostgresRunning', 'Stop-All')) {{
    $node = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }}, $true)
    Invoke-Expression $node.Extent.Text
}}
$pgData = Join-Path $PSScriptRoot 'not-yet-created'
$pgBin = Join-Path $PSScriptRoot 'no-executables'
$script:stopped = $false
function Stop-ServiceProcesses {{ $script:stopped = $true }}
Stop-All
if (-not $script:stopped) {{ throw 'Application processes were not stopped' }}
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell regression")
@pytest.mark.parametrize("fail_at", ["none", "backup", "migrate", "bootstrap"])
def test_schema_initialization_only_marks_success_after_verified_backup(tmp_path, fail_at):
    script = tmp_path / "schema.ps1"
    launcher = str(ROOT / "scripts/windows_release.ps1").replace("'", "''")
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$parseErrors)
$node = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Ensure-Schema' }}, $true)
Invoke-Expression $node.Extent.Text
$dataRoot = $PSScriptRoot
$appRootPath = $PSScriptRoot
$releaseVersion = 'test'
$python = 'Invoke-FakePython'
$script:events = [Collections.Generic.List[string]]::new()
function Invoke-FakePython {{ $global:LASTEXITCODE = 1 }}
function Stop-ServiceProcesses {{ $script:events.Add('stop') }}
function Backup-Database {{
    $script:events.Add('backup')
    if ('{fail_at}' -eq 'backup') {{ throw 'Synthetic backup failure' }}
}}
function Invoke-Manage([string[]]$Arguments) {{
    $script:events.Add(($Arguments -join ' '))
    if ($Arguments[0] -eq '{fail_at}') {{ throw 'Synthetic initialization failure' }}
}}
$failed = $false
try {{ Ensure-Schema }} catch {{ $failed = $true }}
if ($failed -ne ('{fail_at}' -ne 'none')) {{ throw 'Unexpected failure state' }}
$marked = Test-Path (Join-Path $dataRoot 'initialized.txt')
if ($marked -ne ('{fail_at}' -eq 'none')) {{ throw 'Incorrect initialization marker' }}
if ($events[0] -ne 'stop' -or $events[1] -ne 'backup') {{ throw 'Backup must precede migration' }}
if ('{fail_at}' -eq 'backup' -and $events.Count -ne 2) {{ throw 'Migration ran without a backup' }}
if ('{fail_at}' -eq 'none' -and ($events -join ',') -ne 'stop,backup,migrate --noinput,bootstrap,rebuild_workspace,migrate --check') {{ throw 'Incorrect migration sequence' }}
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell regression")
def test_repeated_launch_recognizes_a_single_cim_watcher(tmp_path):
    """A scalar CIM result has no Count property in Windows PowerShell 5.1."""
    launcher = str(ROOT / "scripts/windows_release.ps1").replace("'", "''")
    script = tmp_path / "single-watcher.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$parseErrors)
foreach ($name in @('Resolve-WebAddress', 'Start-Watcher')) {{
    $node = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }}, $true)
    Invoke-Expression $node.Extent.Text
}}
function Get-Watcher {{ [Microsoft.Management.Infrastructure.CimInstance]::new('Win32_Process') }}
$baseUrl = [Uri]'http://127.0.0.1:18765'
$dataRoot = Join-Path $PSScriptRoot 'must-not-write'
Resolve-WebAddress
Start-Watcher
if (Test-Path $dataRoot) {{ throw 'Repeated launch changed configuration' }}
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell regression")
def test_interrupted_database_initialization_is_retried_without_publishing_partial_data(tmp_path):
    launcher = str(ROOT / "scripts/windows_release.ps1").replace("'", "''")
    script = tmp_path / "interrupted-init.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$parseErrors)
$helper = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-DatabasePort' }}, $true)
Invoke-Expression $helper.Extent.Text
$node = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Initialize-Postgres' }}, $true)
Invoke-Expression $node.Extent.Text
$dataRoot = $PSScriptRoot
$pgData = Join-Path $dataRoot 'postgres'
$pgBin = Join-Path $dataRoot 'bin'
New-Item -ItemType Directory -Path $pgData,$pgBin | Out-Null
# Model interruption after PG_VERSION was already written.
function Join-Path($Path,$ChildPath) {{
    if ($ChildPath -eq 'initdb.exe') {{ return 'Invoke-TestInitdb' }}
    Microsoft.PowerShell.Management\\Join-Path $Path $ChildPath
}}
function New-RandomHex {{ 'synthetic-only' }}
$script:fail = $true
function Invoke-TestInitdb {{
    $pending = Join-Path $dataRoot 'postgres-initializing'
    New-Item -ItemType Directory -Path $pending | Out-Null
    Set-Content -LiteralPath (Join-Path $pending 'PG_VERSION') -Value '17'
    if ($script:fail) {{ $global:LASTEXITCODE = 1 }} else {{ $global:LASTEXITCODE = 0 }}
}}
$env:POSTGRES_PASSWORD = 'synthetic-only'
$env:POSTGRES_PORT = '55499'
$failed = $false
try {{ Initialize-Postgres }} catch {{ $failed = $true }}
if (-not $failed -or (Test-Path (Join-Path $pgData 'PG_VERSION'))) {{ throw 'Partial database was published' }}
$script:fail = $false
Initialize-Postgres
if (-not (Test-Path (Join-Path $pgData 'PG_VERSION'))) {{ throw 'Retry did not publish database' }}
if (Test-Path (Join-Path $dataRoot 'postgres-initializing')) {{ throw 'Temporary cluster was left behind' }}
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
