import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer checks")


def test_prerequisite_install_outcomes(tmp_path):
    compiler = Path(os.environ["LOCALAPPDATA"]) / "Programs/Inno Setup 6/ISCC.exe"
    if not compiler.exists():
        pytest.skip("Inno Setup compiler is not installed")
    helper = (ROOT / "installer/vc-runtime.iss").read_text(encoding="utf-8")
    # Compile the actual prerequisite control flow, substituting only OS calls.
    # No real registry, UAC prompt, or system-runtime installation is changed.
    helper = helper.replace("function VCRuntimeReady:", "function ActualVCRuntimeReady:")
    helper = helper.replace("ExtractTemporaryFile(", "TestExtract(")
    helper = helper.replace("ShellExec(", "TestShellExec(")
    header = r"""
var Ready, AfterReady, LaunchOK: Boolean;
    ExitCode, Calls: Integer;
function VCRuntimeReady: Boolean;
begin Result := Ready; end;
procedure TestExtract(Name: String);
begin if Name <> 'vc_redist.x64.exe' then RaiseException('Wrong prerequisite'); end;
function TestShellExec(Verb, Filename, Params, WorkDir: String; Show: Integer;
  Wait: TExecWait; var Code: Integer): Boolean;
begin
  if Verb <> 'runas' then RaiseException('Missing elevation');
  if Pos('/install /quiet /norestart /log ', Params) <> 1 then RaiseException('Wrong arguments');
  Calls := Calls + 1; Code := ExitCode; Ready := AfterReady; Result := LaunchOK;
end;
"""
    checks = r"""
procedure Scenario(Before, After, Started: Boolean; Code: Integer; Success, Reboot: Boolean);
var Message: String;
begin
  Ready := Before; AfterReady := After; LaunchOK := Started;
  ExitCode := Code; Calls := 0; RuntimeRestartRequired := False;
  Message := EnsureVCRuntime;
  if ((Message = '') <> Success) then RaiseException('Incorrect success result');
  if (NeedRestart <> Reboot) or (CanLaunchDesktop = Reboot) then RaiseException('Unsafe launch/reboot');
  if Before and (Calls <> 0) then RaiseException('Reinstalled existing runtime');
  if (not Before) and (Calls <> 1) then RaiseException('Missing runtime was skipped');
end;
procedure InitializeWizard;
begin
  Scenario(True, True, False, 0, True, False);
  Scenario(False, True, True, 0, True, False);
  Scenario(False, False, False, 1223, False, False);
  Scenario(False, False, True, 1603, False, False);
  Scenario(False, False, True, 0, False, False);
  Scenario(False, True, True, 1638, True, False);
  Scenario(False, False, True, 1638, False, False);
  Scenario(False, False, True, 3010, True, True);
  Scenario(False, False, True, 1641, True, True);
  if not SaveStringToFile(ExpandConstant('{param:result}'), 'PASS', False) then RaiseException('No result');
end;
function NextButtonClick(CurPageID: Integer): Boolean;
begin Result := False; end;
"""
    # Keep test logs in the test directory, not the real user's data directory.
    helper = helper.replace("{localappdata}\\XianyuSeller\\logs", str(tmp_path))
    script = tmp_path / "prerequisite-test.iss"
    script.write_text(
        "[Setup]\nAppName=Prerequisite Test\nAppVersion=1\n"
        "CreateAppDir=no\nUninstallable=no\nPrivilegesRequired=lowest\n"
        "ArchitecturesAllowed=x64compatible\nArchitecturesInstallIn64BitMode=x64compatible\n"
        f"OutputDir={tmp_path}\nOutputBaseFilename=test\n[Code]\n" + header + helper + checks,
        encoding="utf-8-sig",
    )
    compiled = subprocess.run([str(compiler), str(script)], capture_output=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout.decode(errors="replace")
    report = tmp_path / "result.txt"
    subprocess.run(
        [str(tmp_path / "test.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", f"/result={report}"],
        capture_output=True,
        timeout=60,
    )
    assert report.read_text() == "PASS"


@pytest.mark.parametrize("exit_code", [0, 1, -1073741515])
def test_database_init_records_native_exit_code(tmp_path, exit_code):
    launcher = str(ROOT / "scripts/windows_release.ps1").replace("'", "''")
    script = tmp_path / "native-test.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{launcher}', [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw 'Invalid syntax' }}
$node = $ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-DatabaseInit'}}, $true)
Invoke-Expression $node.Extent.Text
$pgBin = $PSScriptRoot; $logRoot = $PSScriptRoot
$env:POSTGRES_USER = 'test-only'
Add-Type -TypeDefinition 'public class Probe {{ public static int Main(string[] args) {{ System.Console.WriteLine("test stdout"); System.Console.Error.WriteLine("test stderr"); return {exit_code}; }} }}' -OutputAssembly (Join-Path $pgBin 'initdb.exe') -OutputType ConsoleApplication
$failed = $false
try {{ Invoke-DatabaseInit (Join-Path $pgBin 'data path') (Join-Path $pgBin 'password path') }} catch {{ $failed = $true }}
if ($failed -ne ({exit_code} -ne 0)) {{ throw 'Incorrect initialization outcome' }}
""",
        encoding="utf-8-sig",
    )
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr.decode(errors="replace")
    assert "test stdout" in (tmp_path / "initdb-out.log").read_text(encoding="utf-8-sig")
    assert "test stderr" in (tmp_path / "initdb-error.log").read_text(encoding="utf-8-sig")
    result = (tmp_path / "initdb-result.log").read_text(encoding="utf-8-sig")
    assert f"0x{exit_code & 0xFFFFFFFF:08X}" in result
