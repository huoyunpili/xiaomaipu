"""Build a self-contained Windows repair launcher without machine-specific paths."""

import argparse
from pathlib import Path


def build(output: Path) -> None:
    source = Path(__file__).with_name("repair_database_port.ps1").read_text(encoding="utf-8-sig")
    wrapper = r"""@echo off
setlocal
set "FM_REPAIR_SELF=%~f0"
set "FM_REPAIR_APP=%~1"
set "FM_REPAIR_DATA=%~2"
set "FM_REPAIR_REPORT=%~3"
echo FishManager repair v2
echo Close FishManager completely before running this repair.
echo This repairs only the known database port configuration block.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content -LiteralPath $env:FM_REPAIR_SELF -Raw -Encoding UTF8; & ([scriptblock]::Create(($s -split '(?m)^:POWERSHELL\r?$')[1])) -AppRoot $env:FM_REPAIR_APP -DataRoot $env:FM_REPAIR_DATA -ReportPath $env:FM_REPAIR_REPORT"
set "FM_REPAIR_EXIT=%ERRORLEVEL%"
echo.
echo Keep the report shown above if the application still cannot start.
if not "%FM_REPAIR_NO_PAUSE%"=="1" pause
exit /b %FM_REPAIR_EXIT%
:POWERSHELL
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        (wrapper + source).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
