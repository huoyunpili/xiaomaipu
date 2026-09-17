import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from app.common.backup import database_fingerprint


class Command(BaseCommand):
    help = "Back up local Docker PostgreSQL and private media; optionally restore to an isolated verification database."

    def add_arguments(self, parser):
        parser.add_argument("--verify", action="store_true")
        parser.add_argument("--output-dir", type=Path, help="Optional backup parent directory.")

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        backup = (options.get("output_dir") or root / ".local" / "backups") / (
            timezone.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        )
        backup.mkdir(parents=True)
        database = settings.DATABASES["default"]
        username, dbname = database["USER"], database["NAME"]
        prefix = [
            "docker",
            "compose",
            "-f",
            str(root / "deployment/compose.yaml"),
            "exec",
            "-T",
            "postgres",
        ]

        def run(args, **kwargs):
            result = subprocess.run(prefix + args, stderr=subprocess.PIPE, **kwargs)
            if result.returncode:
                raise CommandError("本地数据库备份/恢复命令失败，请检查 Docker PostgreSQL 状态。")
            return result

        dump = backup / "database.dump"
        with dump.open("wb") as output:
            run(["pg_dump", "-U", username, "-d", dbname, "-Fc"], stdout=output)
        files = {}
        media = Path(settings.PRIVATE_MEDIA_ROOT)
        if media.exists():
            for path in media.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    relative = path.relative_to(media)
                    target = backup / "private-media" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    with target.open("rb") as copied, path.open("rb") as original:
                        digest = hashlib.file_digest(copied, "sha256").hexdigest()
                        if digest != hashlib.file_digest(original, "sha256").hexdigest():
                            raise CommandError("私有文件备份校验失败。")
                        files[str(relative)] = digest
        manifest = {
            "created_at": timezone.now().isoformat(),
            "database": dbname,
            "media_hashes": files,
            "restored": False,
        }
        with dump.open("rb") as handle:
            manifest["dump_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
        if options["verify"]:
            scratch = "seller_restore_check_" + uuid.uuid4().hex[:12]
            if not scratch.startswith("seller_restore_check_") or scratch == dbname:
                raise CommandError("恢复校验目标不安全。")
            run(["createdb", "-U", username, scratch], stdout=subprocess.DEVNULL)
            try:
                with dump.open("rb") as source:
                    run(
                        [
                            "pg_restore",
                            "-U",
                            username,
                            "-d",
                            scratch,
                            "--exit-on-error",
                            "--no-owner",
                        ],
                        stdin=source,
                        stdout=subprocess.DEVNULL,
                    )

                def scalar(db, sql):
                    return (
                        run(
                            ["psql", "-U", username, "-d", db, "-At", "-c", sql],
                            stdout=subprocess.PIPE,
                        )
                        .stdout.decode()
                        .strip()
                    )

                tables = scalar(
                    scratch,
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
                ).splitlines()
                queries = []
                for table in tables:
                    quoted = '"' + table.replace('"', '""') + '"'
                    literal = "'" + table.replace("'", "''") + "'"
                    queries.append("SELECT " + literal + " AS name, count(*) FROM " + quoted)
                sql = " UNION ALL ".join(queries) + " ORDER BY name"
                if scalar(scratch, sql) != scalar(dbname, sql):
                    raise CommandError("恢复后的行数与源库不同，请暂停写入后重新校验。")
                manifest["verified_tables"] = len(tables)
                fingerprints = []
                for name in (dbname, scratch):
                    with psycopg.connect(
                        dbname=str(name),
                        user=str(username),
                        password=str(database["PASSWORD"]),
                        host=str(database["HOST"]),
                        port=str(database["PORT"]),
                    ) as verified_connection:
                        with verified_connection.cursor() as cursor:
                            fingerprints.append(database_fingerprint(cursor))
                if fingerprints[0] != fingerprints[1]:
                    raise CommandError("恢复后的字段内容与源库不同，请暂停写入后重新校验。")
                manifest["content_verified"] = True
                manifest["content_fingerprints"] = fingerprints[0]
                manifest["restored"] = True
            finally:
                run(["dropdb", "-U", username, scratch], stdout=subprocess.DEVNULL)
        (backup / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.stdout.write(f"Backup saved: {backup}\nRestore verified: {manifest['restored']}")
