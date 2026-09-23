"""Exercise upgrades and pg_dump/restore using only generated databases and synthetic records."""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(stage, database, media, output):
    if (
        not database.startswith("seller_wb_probe_")
        or not database.removeprefix("seller_wb_probe_").isalnum()
    ):
        raise ValueError("Only generated verification databases are allowed")
    os.environ["POSTGRES_DB"] = database
    os.environ["DJANGO_SETTINGS_MODULE"] = "app.config.settings.test"
    os.environ["PRIVATE_MEDIA_ROOT"] = str(media)
    import django

    django.setup()
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.utils import timezone

    from app.common.backup import database_fingerprint
    from app.workbench.exports import private_path, save_image
    from app.workbench.models import ExportBatch, ExportImage, SupplierAccess, SupplierVideo, Trade
    from app.workbench.supplier_views import legacy_video_path

    if stage == "upgrade":
        executor = MigrationExecutor(connection)
        targets = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "workbench"]
        executor.migrate(targets)
        legacy = executor.loader.project_state(targets).apps
        catalog_product = legacy.get_model("catalog", "Product").objects.create(
            name="Legacy synthetic product"
        )
        sku = legacy.get_model("catalog", "SKU").objects.create(
            product=catalog_product, specification="Legacy specification"
        )
        channel = legacy.get_model("shops", "SalesChannel").objects.create(
            code="PROBE", name="Synthetic channel"
        )
        legacy_order = legacy.get_model("orders", "SalesOrder").objects.create(
            channel=channel,
            number="LEGACY-PROBE",
            customer_name="Legacy synthetic customer",
            amount_fen=12800,
        )
        legacy.get_model("orders", "OrderItem").objects.create(
            order=legacy_order,
            sku=sku,
            quantity=2,
            unit_price_fen=6400,
            title_snapshot="Legacy synthetic product",
            condition_snapshot={"condition_description": "Preserve historical description"},
        )
        executor = MigrationExecutor(connection)
        targets.append(("workbench", "0006_product_notes"))
        executor.migrate(targets)
        old = executor.loader.project_state(targets).apps
        owner = old.get_model("accounts", "User").objects.create(
            username="synthetic-restore-owner", role="ADMIN"
        )
        first = old.get_model("shops", "Shop").objects.create(
            name="Synthetic active shop", is_active=True
        )
        second = old.get_model("shops", "Shop").objects.create(
            name="Synthetic archived shop", is_active=False
        )
        product = old.get_model("workbench", "ProductCost").objects.create(
            key="shared-upgrade-probe",
            title="Synthetic product",
            supplier="Synthetic supplier",
            unit_fen=6000,
            version=2,
            supplier_wechat="Synthetic contact",
            shipping_note="Package carefully",
        )
        for version, cost in [(1, 5000), (2, 6000)]:
            old.get_model("workbench", "CostVersion").objects.create(
                product=product, version=version, unit_fen=cost, effective_at=timezone.now()
            )
        identifiers = []
        for shop, cost in [(first, 6000), (second, 5000)]:
            trade = old.get_model("workbench", "Trade").objects.create(
                shop=shop,
                product=product,
                number="synthetic-order",
                status="SHIPPING",
                paid_fen=20000,
                quantity=2,
                unit_cost_fen=cost,
                cost_version=2 if cost == 6000 else 1,
                note="Keep local note",
                platform_note="Platform note",
                default_shipping_note="Package carefully",
                paid_at=timezone.now(),
                receiver="Synthetic recipient",
                phone="00000000000",
                address="Synthetic address",
            )
            identifiers.append(str(trade.pk))
        batch = old.get_model("workbench", "ExportBatch").objects.create(
            shop=first,
            actor=owner,
            kind="shipping",
            supplier="Synthetic supplier",
            snapshot=[
                {"id": identifiers[0], "note": "Immutable legacy note", "signature": "legacy-probe"}
            ],
        )
        expected_snapshot = batch.snapshot
        call_command("migrate", verbosity=0)
        rows = [Trade.objects.get(pk=pk) for pk in identifiers]
        assert rows[0].product_id != rows[1].product_id
        for row, cost in zip(rows, [6000, 5000], strict=True):
            assert row.unit_cost_fen == cost and row.product.shop_id == row.shop_id
            assert row.note == "Keep local note" and row.platform_note == "Platform note"
            assert list(row.product.revisions.values_list("version", "unit_fen")) == [
                (1, 5000),
                (2, 6000),
            ]
        batch = ExportBatch.objects.get(pk=batch.pk)
        assert batch.snapshot == expected_snapshot

        def chunk(kind, value):
            return (
                struct.pack(">I", len(value))
                + kind
                + value
                + struct.pack(">I", zlib.crc32(kind + value))
            )

        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 920, 1, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(b"\0" + b"\xff" * 3680)) + chunk(b"IEND", b"")
        save_image(batch, 1, png)
        # Include shipment evidence in backup/restore, not only old export PNGs.
        evidence = b"\x00\x00\x00\x18ftypisom" + b"synthetic-backup-evidence" * 100
        evidence_name = f"supplier-evidence/{rows[0].pk}/restore-probe.mp4"
        evidence_path = legacy_video_path(evidence_name)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(evidence)
        access = SupplierAccess.objects.create(
            batch=batch,
            expires_at=timezone.now(),
            revoked_at=timezone.now(),
        )
        SupplierVideo.objects.create(
            trade=rows[0],
            access=access,
            storage_name=evidence_name,
            original_name="restore-probe.mp4",
            size=len(evidence),
            sha256=hashlib.sha256(evidence).hexdigest(),
            mime_type="video/mp4",
        )
        call_command("migrate", verbosity=0)  # Repeated startup must be idempotent.
        backup_parent = output.parent / "command-backups"
        call_command("backup_local", verify=True, output_dir=backup_parent)
        manifest_path = next(backup_parent.glob("*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["content_verified"] is True and manifest["restored"] is True
    else:
        call_command("migrate", verbosity=0)
        assert Trade.objects.count() == 2 and ExportImage.objects.count() == 1
    for saved in ExportImage.objects.all():
        assert (
            hashlib.sha256(private_path(saved.storage_name).read_bytes()).hexdigest()
            == saved.sha256
        )
    assert SupplierVideo.objects.count() == 1
    for saved_video in SupplierVideo.objects.all():
        assert (
            hashlib.sha256(legacy_video_path(saved_video.storage_name).read_bytes()).hexdigest()
            == saved_video.sha256
        )
    from app.orders.models import SalesOrder

    legacy_order = SalesOrder.objects.get(number="LEGACY-PROBE")
    assert (
        legacy_order.amount_fen == 12800
        and legacy_order.customer_name == "Legacy synthetic customer"
    )
    assert legacy_order.items.get().condition_snapshot == {
        "condition_description": "Preserve historical description"
    }
    with connection.cursor() as cursor:
        fingerprint = database_fingerprint(cursor)
    media_hashes = {
        p.relative_to(media).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in media.rglob("*")
        if p.is_file()
    }
    output.write_text(
        json.dumps({"tables": fingerprint, "media": media_hashes}, sort_keys=True), encoding="utf-8"
    )
    connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["upgrade", "restored"])
    parser.add_argument("--database")
    parser.add_argument("--media", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.stage:
        worker(args.stage, args.database, args.media, args.output)
        return
    run_id = uuid.uuid4().hex[:12]
    folder = ROOT / "artifacts" / "restore" / run_id
    folder.mkdir(parents=True)
    source, restored = f"seller_wb_probe_{run_id}a", f"seller_wb_probe_{run_id}b"
    # Use the configured local PostgreSQL username; do not log environment values.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env.local", override=False)
    username = os.environ.get("POSTGRES_USER", "seller")
    prefix = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "deployment/compose.yaml"),
        "exec",
        "-T",
        "postgres",
    ]
    created = []

    def db_run(command, **kwargs):
        subprocess.run(prefix + command, check=True, stderr=subprocess.PIPE, **kwargs)

    try:
        for name in [source, restored]:
            db_run(["createdb", "-U", username, name], stdout=subprocess.DEVNULL)
            created.append(name)
        for stage, name in [("upgrade", source), ("restored", restored)]:
            media = folder / (stage + "-media")
            if stage == "restored":
                shutil.copytree(folder / "upgrade-media", media)
                with (folder / "database.dump").open("rb") as stream:
                    db_run(
                        [
                            "pg_restore",
                            "-U",
                            username,
                            "-d",
                            restored,
                            "--exit-on-error",
                            "--no-owner",
                        ],
                        stdin=stream,
                        stdout=subprocess.DEVNULL,
                    )
            subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--stage",
                    stage,
                    "--database",
                    name,
                    "--media",
                    str(media),
                    "--output",
                    str(folder / (stage + ".json")),
                ],
                check=True,
                cwd=ROOT,
            )
            if stage == "upgrade":
                with (folder / "database.dump").open("wb") as stream:
                    db_run(["pg_dump", "-U", username, "-d", source, "-Fc"], stdout=stream)
        before = json.loads((folder / "upgrade.json").read_text())
        after = json.loads((folder / "restored.json").read_text())
        assert before == after, "Restored row contents or media differ"
        report = {
            "passed": True,
            "upgrade_from": "legacy schema without workbench, through workbench 0006 and latest",
            "verified_tables": len(before["tables"]),
            "verified_images": len(before["media"]),
            "synthetic_only": True,
        }
        (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Upgrade and content restore verified: {folder / 'report.json'}")
    finally:
        for name in reversed(created):
            if name not in (source, restored) or not name.startswith("seller_wb_probe_"):
                raise RuntimeError("Unsafe cleanup target")
            db_run(["dropdb", "-U", username, "--if-exists", name], stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
