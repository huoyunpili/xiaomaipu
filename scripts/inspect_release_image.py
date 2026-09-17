"""Inspect the built image without printing configuration secrets."""

import hashlib
import json
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "xianyu-seller-local:0.6.0-rc1"


def main():
    secrets = []
    for config in [ROOT / ".env.local", ROOT / ".local-release/verification-v131/data/config.env"]:
        if config.exists():
            for key, value in dotenv_values(config).items():
                if (
                    key
                    in {"DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "XGJ_APP_KEY", "XGJ_APP_SECRET"}
                    and value
                    and len(value) >= 8
                    and value != "seller-local-only"
                ):
                    secrets.append(value)
    code = """
import hashlib, json, sys
from pathlib import Path
secrets = [value.encode() for value in json.load(sys.stdin)]
root = Path('/srv/app')
files = {}
for path in root.rglob('*'):
    if not path.is_file() or '.venv' in path.parts or '__pycache__' in path.parts:
        continue
    name = path.relative_to(root).as_posix()
    if path.name.startswith('.env') or path.name in ('api_key.txt', 'config.env') or path.suffix in ('.dump', '.sqlite3', '.secret'):
        raise RuntimeError('Forbidden image file: ' + name)
    payload = path.read_bytes()
    if any(value in payload for value in secrets):
        raise RuntimeError('Private configuration found in image file: ' + name)
    files[name] = hashlib.sha256(payload).hexdigest()
print(json.dumps(files, sort_keys=True))
"""
    scanned = subprocess.run(
        ["docker", "run", "--rm", "-i", "--entrypoint", "python", IMAGE, "-c", code],
        input=json.dumps(secrets).encode(),
        capture_output=True,
        check=True,
    )
    files = json.loads(scanned.stdout)
    expected = [
        p for p in (ROOT / "app").rglob("*") if p.is_file() and "__pycache__" not in p.parts
    ]
    expected += [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "manage.py"]
    for path in expected:
        name = path.relative_to(ROOT).as_posix()
        if files.get(name) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError("Built image differs from source: " + name)
    metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", IMAGE]))[0]
    assert metadata["Config"]["Labels"]["org.opencontainers.image.version"] == "0.6.0-rc1"
    assert not any(value in json.dumps(metadata["Config"]) for value in secrets)
    report = {
        "passed": True,
        "image": IMAGE,
        "image_id": metadata["Id"],
        "source_files_matched": len(expected),
        "image_files_scanned": len(files),
        "private_configuration_matches": 0,
    }
    output = ROOT / "artifacts/verification-v131-image.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Image source and privacy checks passed: {output}")


if __name__ == "__main__":
    main()
