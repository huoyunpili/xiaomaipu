"""Synthetic fixtures for the dedicated v131 release verification container only."""

import hashlib
import os
import struct
import sys
import zlib
from pathlib import Path

if os.environ.get("RELEASE_PROBE") != "v131-e5567ffa":
    raise RuntimeError("Explicit isolated release verification flag required")
sys.path.insert(0, "/srv/app")
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.accounts.models import User  # noqa: E402
from app.shops.models import Shop  # noqa: E402
from app.workbench.exports import private_path, save_image  # noqa: E402
from app.workbench.models import CostVersion, ExportImage, ProductCost, Trade  # noqa: E402
from app.workbench.services import create_batches  # noqa: E402

owner = User.objects.get(username="synthetic-release-owner", role="ADMIN")
shop = Shop.objects.get(is_active=True)
marker = Path(settings.PRIVATE_MEDIA_ROOT) / "synthetic-restore-marker.txt"
stage = sys.argv[1]
if stage == "seed":
    assert not Trade.objects.exists(), "Refusing to seed a nonempty business database"
    product = ProductCost.objects.create(
        shop=shop,
        key="synthetic-release-product",
        title="Synthetic release product",
        unit_fen=6000,
        supplier="Synthetic supplier",
        supplier_wechat="Synthetic contact",
        shipping_note="Original default note",
    )
    CostVersion.objects.create(
        product=product, version=1, unit_fen=6000, effective_at=timezone.now()
    )
    trade = Trade.objects.create(
        shop=shop,
        product=product,
        number="SYNTHETIC-RELEASE-ORDER",
        status="SHIPPING",
        title=product.title,
        spec="Synthetic specification",
        paid_fen=20000,
        quantity=2,
        unit_cost_fen=6000,
        cost_version=1,
        paid_at=timezone.now(),
        supplier=product.supplier,
        receiver="Synthetic recipient",
        phone="00000000000",
        address="Synthetic address",
        note="Original local note",
        default_shipping_note=product.shipping_note,
    )
    batch = create_batches([trade], "shipping", owner)[0]

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
    marker.write_text("Original private file", encoding="utf-8")
elif stage == "mutate":
    assert Trade.objects.count() == 1
    Trade.objects.filter(number="SYNTHETIC-RELEASE-ORDER").update(note="Changed after backup")
    marker.write_text("Changed after backup", encoding="utf-8")
elif stage == "verify":
    trade = Trade.objects.get(number="SYNTHETIC-RELEASE-ORDER")
    assert trade.note == "Original local note" and trade.unit_cost_fen == 6000
    assert trade.cost_version == 1 and trade.profit_fen == 7680
    assert trade.product.supplier_wechat == "Synthetic contact"
    assert marker.read_text(encoding="utf-8") == "Original private file"
    for saved in ExportImage.objects.all():
        assert (
            hashlib.sha256(private_path(saved.storage_name).read_bytes()).hexdigest()
            == saved.sha256
        )
else:
    raise ValueError("Unknown probe stage")
print(f"Synthetic release probe {stage}: passed")
