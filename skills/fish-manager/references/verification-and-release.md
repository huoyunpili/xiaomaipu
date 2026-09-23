# Verification, diagnosis, and release safety

Read this reference for installation, synchronization, release, credential, or security work.

## Local verification

Use the project-managed environment and locked dependencies. Run focused tests first, for example:

```powershell
uv run pytest -q tests/test_integrations.py tests/test_saved_history_import.py
```

The repository quality gate is:

```powershell
uv run python scripts/check.py
```

It runs Ruff checks and formatting verification, mypy, Django checks, migration drift detection, and pytest. It expects the isolated PostgreSQL test service described by the repository setup. Do not claim this gate passed if a service was unavailable; report the exact completed checks and blocker.

## Installation diagnosis

Start with observable state: installed version, Windows version, process/service state, local data path, listening port, PostgreSQL state and the most recent redacted error. Never ask the user to paste the entire configuration or database.

The supported end-user path is the single signed Windows installer. Docker, Python, PostgreSQL, Redis and PowerShell setup are development concerns, not prerequisites for ordinary users.

Preserve `%LOCALAPPDATA%\XianyuSeller` across upgrades and normal uninstall. Before repair or migration, take a verified backup when data is material.

## 闲管家 synchronization diagnosis

Use read-only checks first. Confirm credentials are present without displaying them, validate the authorized shop, inspect the last synchronization run, window/cursor and redacted diagnostic, then reproduce with mocked or synthetic requests where possible.

Do not repeatedly call a potentially billable or mutating API while guessing. Stop before any live shipment, refund or message and ask for explicit authorization with the exact intended operation.

For historical import, verify the upstream documented lookback and item limits, page traversal, deduplication key, retry behavior and projection rebuild. Reconcile source order counts against local platform facts and workbench orders; explain any excluded statuses.

## Secret and privacy checks

Real secrets belong only in ignored local configuration or the operating-system credential facility. Before public release:

- confirm `.env.local`, `config.env`, databases, logs, backups, media and `.local-release` are untracked;
- scan the working tree and Git history for keys, tokens, cookies, customer PII and absolute personal paths;
- rotate any credential that may have appeared in terminal output, archives or shared diagnostics;
- review third-party code, binary downloads and fixture licenses;
- ensure internal `docs/` records are ignored and excluded from the package.

Never paste a discovered secret into a report. Identify it by variable/file and remediation status only.

## Windows release

Build with `scripts/build_windows_installer.ps1`. The script pins and verifies external runtime downloads, runs the quality gate unless explicitly skipped, stages only required application files, and compiles `installer/fish-manager.iss`.

After building:

1. run the existing package review checks;
2. verify the installer filename, version and SHA-256;
3. inspect that no secret, internal document, database, log, backup or private media is present;
4. test fresh install, launch, first-owner creation, restart, upgrade and uninstall/data-retention behavior;
5. publish known limitations and rollback/backup guidance.

Do not sign or publish a release unless the user explicitly requested those external actions and the signing identity is available.
