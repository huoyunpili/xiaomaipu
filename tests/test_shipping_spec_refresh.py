from unittest.mock import patch

import pytest
from django.urls import reverse

from app.integrations.services import store_order
from app.workbench.models import Trade
from tests.test_workspace import connection, payload  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("upstream_spec", [None, "", "   "])
def test_saved_spec_survives_export_refresh(connection, admin_user, client, upstream_spec):  # noqa: F811
    data = payload()
    data["goods"]["sku_text"] = ""
    row = store_order(connection, data)
    trade = Trade.objects.get(platform=row)
    client.force_login(admin_user)
    response = client.post(
        reverse("wb-detail", args=[trade.pk]),
        {
            "spec": "测试充电站 250W",
            "receiver": trade.receiver,
            "phone": trade.phone,
            "address": trade.address,
        },
    )
    assert response.status_code == 302
    data["update_time"] += 1
    if upstream_spec is None:
        data["goods"].pop("sku_text")
    else:
        data["goods"]["sku_text"] = upstream_spec

    def refresh(_row):
        return store_order(connection, data)

    with patch("app.workbench.views.refresh_order", side_effect=refresh) as refreshed:
        response = client.post(
            reverse("wb-export", args=["shipping"]), {"selected": [str(trade.pk)]}
        )
    refreshed.assert_called_once()
    assert response.status_code == 200
    batch = response.context["batches"][0]
    assert batch.snapshot[0]["spec"] == "测试充电站 250W"
    download = client.get(reverse("wb-batch-text", args=[batch.pk]))
    assert "测试充电站 250W" in download.content.decode()
    trade.refresh_from_db()
    assert trade.spec == "测试充电站 250W"
    # Real upstream changes and status validation must still be honored.
    data["update_time"] += 1
    data["goods"]["sku_text"] = "平台更正规格"
    store_order(connection, data)
    trade.refresh_from_db()
    assert trade.spec == "平台更正规格"


def test_complete_platform_order_exports_without_optional_spec(connection, admin_user, client):  # noqa: F811
    data = payload()
    data["goods"]["sku_text"] = ""
    trade = Trade.objects.get(platform=store_order(connection, data))
    client.force_login(admin_user)
    data["update_time"] += 1
    with patch(
        "app.workbench.views.refresh_order", side_effect=lambda _: store_order(connection, data)
    ):
        response = client.post(
            reverse("wb-export", args=["shipping"]),
            {"selected": [str(trade.pk)]},
        )
    assert response.status_code == 200
    batch = response.context["batches"][0]
    assert batch.snapshot[0]["spec"] == ""
    assert batch.snapshot[0]["title"] == trade.title
    downloaded = client.get(reverse("wb-batch-text", args=[batch.pk]))
    text = downloaded.content.decode()
    assert trade.title in text and trade.phone in text and trade.address in text
    assert "规格：" not in text


def test_export_error_identifies_missing_phone(connection, admin_user, client):  # noqa: F811
    data = payload(receiver_mobile="")
    trade = Trade.objects.get(platform=store_order(connection, data))
    client.force_login(admin_user)
    with patch("app.workbench.views.refresh_order"):
        response = client.post(
            reverse("wb-export", args=["shipping"]),
            {"selected": [str(trade.pk)]},
            follow=True,
        )
    assert f"订单 {trade.number} 需补充：电话。" in response.content.decode()
