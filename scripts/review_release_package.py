"""Build and inspect a committed copy of the current source without changing the working Git index."""

import hashlib
import json
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    original_status = subprocess.check_output(["git", "status", "--porcelain", "-z"], cwd=ROOT)
    review_id = uuid.uuid4().hex[:10]
    source = ROOT / ".local-release" / ("source-review-" + review_id)
    output = ROOT / "artifacts" / "releases" / review_id
    source.mkdir(parents=True)
    output.mkdir(parents=True)
    paths = (
        subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT)
        .decode("utf-8")
        .split("\0")
    )
    forbidden = re.compile(
        r"(^|/)(api_key\.txt|config\.env|private-media|backups|\.local|\.local-release|artifacts)(/|$)|(^|/)\.env($|\.(?!example$))|\.(dump|log|secret|sqlite3|pem|key)$",
        re.I,
    )
    for name in sorted(set(paths) - {""}):
        original = ROOT / name
        if not original.exists():
            continue
        if (
            forbidden.search(name)
            or original.is_symlink()
            or not original.resolve().is_relative_to(ROOT)
        ):
            raise RuntimeError(f"Unsafe source path: {name}")
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "--all"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Local release verification",
            "-c",
            "user.email=release-check@localhost",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"core.hooksPath={source / '.git/empty-hooks'}",
            "commit",
            "-qm",
            "Review current PRD V1.31 implementation",
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    built = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(source / "scripts/build_release.ps1"),
            "-OutputDir",
            str(output),
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    (output / "build-output.txt").write_bytes(built.stdout + built.stderr)
    package = next(output.glob("*.zip"))
    secrets = []
    for path in [
        ROOT / ".env.local",
        ROOT / ".env",
        ROOT / ".local-release/verification-v131/data/config.env",
    ]:
        if path.exists():
            for key, value in dotenv_values(path).items():
                if (
                    value
                    and len(value) >= 8
                    and key
                    in {"DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "XGJ_APP_KEY", "XGJ_APP_SECRET"}
                    and value != "seller-local-only"
                ):
                    secrets.append(value.encode())
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        for name in names:
            relative = "/".join(name.split("/")[1:])
            if (
                forbidden.search(relative)
                or ".." in Path(relative).parts
                or relative.startswith("/")
            ):
                raise RuntimeError(f"Unsafe archive path: {name}")
            payload = archive.read(name)
            if any(value in payload for value in secrets):
                raise RuntimeError(f"Private configuration value detected in {name}")
        for required in [
            "app/workbench/models.py",
            "app/workbench/migrations/0007_product_shop.py",
            "app/workbench/migrations/0008_status_change_time.py",
            "scripts/local_release.ps1",
            "README.md",
        ]:
            if not any(name.endswith("/" + required) for name in names):
                raise RuntimeError(f"Required release file missing: {required}")
    report = {
        "passed": True,
        "package": str(package),
        "file_entries": len(names),
        "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "private_configuration_matches": 0,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source)
        .decode()
        .strip(),
        "original_worktree_unchanged": True,
    }
    if subprocess.check_output(["git", "status", "--porcelain", "-z"], cwd=ROOT) != original_status:
        raise RuntimeError("Original worktree status changed during packaging")
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"passed": True, "report": str(output / "report.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
