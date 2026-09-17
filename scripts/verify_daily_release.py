"""Run inside the release image against a disposable regression_* PostgreSQL database."""

import json
import os
from datetime import timedelta
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.config.settings.local_release")
django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.accounts.models import User  # noqa: E402
from app.integrations.models import Connection  # noqa: E402
from app.integrations.services import store_order  # noqa: E402
from app.shops.models import Shop  # noqa: E402
from app.workbench.models import ProductCost, Trade  # noqa: E402
from app.workbench.services import confirm_refund_success  # noqa: E402


def main():
    assert settings.DATABASES["default"]["NAME"].startswith("regression_")
    assert not settings.DEBUG
    setup_test_environment()
    call_command("migrate", verbosity=0)
    call_command("bootstrap", verbosity=0)
    owner = User.objects.create_user("release-probe", role="ADMIN")
    shop = Shop.objects.get(is_active=True)
    old_product = ProductCost.objects.create(
        shop=shop, key="upgrade-probe", title="Upgrade probe", unit_fen=32000
    )
    # Exercise an upgrade from before today's new metadata, with existing cost data.
    call_command("migrate", "workbench", "0008", verbosity=0)
    call_command("migrate", verbosity=0)
    old_product.refresh_from_db()
    assert old_product.unit_fen == 32000 and old_product.condition == ""
    now = timezone.now()
    connection = Connection.objects.create(
        shop=shop,
        actor=owner,
        seller_id="synthetic",
        enabled=False,
        sync_start_at=now - timedelta(days=10),
    )

    def order(number, **changes):
        data = {
            "order_no": number,
            "order_status": 12,
            "refund_status": 0,
            "pay_amount": 20000,
            "update_time": int(now.timestamp()),
            "order_time": int((now - timedelta(days=12)).timestamp()),
            "pay_time": int((now - timedelta(days=12)).timestamp()),
            "goods": {
                "product_id": "synthetic",
                "sku_id": "one",
                "title": "Release probe",
                "sku_text": "款式:测试",
                "quantity": 1,
            },
            **changes,
        }
        return store_order(connection, data)

    shipping = order("10001")
    tomorrow_end = timezone.localtime(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=3)
    order(
        "10002", order_status=21, consign_time=int((tomorrow_end - timedelta(days=10)).timestamp())
    )
    order("10003", order_status=22, confirm_time=int(now.timestamp()))
    refunded = order(
        "10004",
        order_status=23,
        refund_time=int(now.timestamp()),
        refund_amount=0,
        order_time=int(now.timestamp()),
    )
    trade = Trade.objects.get(platform=refunded)
    confirm_refund_success(trade, owner)
    Trade.objects.filter(pk=trade.pk).update(refund_type=2, unit_cost_fen=32000, recovered_at=now)
    for _ in range(2):
        call_command("rebuild_workspace", verbosity=0)
    trade.refresh_from_db()
    assert trade.status == "REFUNDED" and trade.refund_success_confirmed_at
    assert trade.recovered_at == now and trade.unit_cost_fen == 32000
    assert Trade.objects.get(platform=shipping).status == "SHIPPING"
    client = Client()
    client.force_login(owner)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    assert response.context["today_repayment_amount"] == 20000
    assert response.context["soon_due"] == 20000
    assert response.context["recovery"] == 0
    assert "平台未提供" not in client.get(reverse("wb-costs")).content.decode()
    response = client.post(reverse("wb-recovery", args=[trade.pk]), {"recovered": "no"})
    assert response.status_code == 200 and not response.json()["recovered"]
    assert client.get(reverse("dashboard")).context["recovery"] == 32000
    for name in (
        "workbench.js",
        "workbench-costs.js",
        "workbench.css",
        "theme.css",
        "supplier.js",
        "supplier.css",
        "shipping-text.js",
    ):
        assert (Path(settings.STATIC_ROOT) / name).is_file()
    print(
        json.dumps(
            {
                "release_mode": True,
                "migration_upgrade": "passed",
                "rebuild_preserves_confirmation": "passed",
                "dashboard_and_inline_recovery": "passed",
                "static_assets": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
