"""One command for the M3 quality gate; requires the isolated PostgreSQL service."""

import os
import subprocess
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.config.settings.test")
sources = [
    "app",
    "tests",
    "scripts/check.py",
    "scripts/verify_daily_release.py",
    "scripts/xgj_probe/probe.py",
    "manage.py",
]
commands = [
    ["-m", "ruff", "check", *sources],
    ["-m", "ruff", "format", "--check", *sources],
    ["-m", "mypy", "app"],
    ["manage.py", "check"],
    ["manage.py", "makemigrations", "--check", "--dry-run"],
    ["-m", "pytest", "-q"],
]
for command in commands:
    print("Running:", " ".join(command), flush=True)
    result = subprocess.run([sys.executable, *command], check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
