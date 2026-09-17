"""Read-only aggregate inspection; never prints identities, amounts or credentials."""

import os
import sys
from collections import Counter
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.config.settings.test")

django.setup()
from app.integrations.models import PlatformOrder  # noqa: E402

counts = Counter()
for row in PlatformOrder.objects.only("snapshot").iterator():
    data = row.snapshot
    counts[
        (
            data.get("order_status"),
            data.get("refund_status"),
            bool(data.get("pay_time")),
            bool(data.get("consign_time")),
            bool(data.get("confirm_time")),
            bool(data.get("refund_time")),
            data.get("pay_amount", -1) == data.get("refund_amount", -2),
        )
    ] += 1
for fields, count in sorted(counts.items(), key=lambda item: str(item[0])):
    print(fields, count)
