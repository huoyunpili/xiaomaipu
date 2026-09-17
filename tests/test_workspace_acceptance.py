from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from app.common.business import BusinessError
from app.integrations.client import APIError
from app.integrations.services import store_order
from app.workbench.models import ExportBatch, Trade
from app.workbench.services import create_batches, update_product
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade, payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_dashboard_groups_changed_shipping_batches(connection, admin_user, client):
    batches = [
        ExportBatch.objects.create(
            shop=connection.shop,
            actor=admin_user,
            supplier=supplier,
            kind="shipping",
            stale=True,
        )
        for supplier in ["供应商甲", "供应商甲", "供应商甲", "供应商乙"]
    ]
    client.force_login(admin_user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count("供应商甲 的清单有订单变化") == 1
    assert html.count("供应商乙 的清单有订单变化") == 1
    assert "查看相关清单（3 份）" in html
    for batch in batches:
        assert reverse("wb-batch", args=[batch.pk]) in html


def test_shipped_batch_does_not_warn_to_stop_shipping(connection, admin_user, client):
    trade = make_trade(connection)
    update_product(trade.product, 6000, "供应商甲", admin_user)
    trade.refresh_from_db()
    batch = create_batches([trade], "shipping", admin_user)[0]
    now = int(timezone.now().timestamp())
    store_order(connection, payload(order_status=21, consign_time=now, update_time=now + 1))
    client.force_login(admin_user)
    response = client.get(reverse("wb-batch", args=[batch.pk]))
    assert response.context["changed"]
    assert not response.context["stop_shipping"]
    assert "联系供应商停止发货" not in response.content.decode()


def test_rebuild_refreshes_missing_images_and_preserves_existing(connection):
    trade = make_trade(connection)
    trade.platform.snapshot["goods"].pop("images", None)
    trade.platform.save(update_fields=["snapshot"])
    Trade.objects.filter(pk=trade.pk).update(image="", note="本单备注")
    output = StringIO()
    with patch("app.integrations.services.XgjClient.call", return_value=payload()) as api:
        call_command("rebuild_workspace", refresh_missing_images=True, stdout=output)
        assert api.call_count == 1
    trade.refresh_from_db()
    assert trade.image == "https://example.com/a.jpg"
    assert trade.note == "本单备注"
    assert "Images filled: 1" in output.getvalue()
    with patch("app.integrations.services.XgjClient.call") as api:
        call_command("rebuild_workspace", refresh_missing_images=True, stdout=StringIO())
        api.assert_not_called()


def test_rebuild_missing_images_api_failure_keeps_order(connection):
    trade = make_trade(connection)
    trade.platform.snapshot["goods"].pop("images", None)
    trade.platform.save(update_fields=["snapshot"])
    Trade.objects.filter(pk=trade.pk).update(image="")
    output = StringIO()
    with patch("app.integrations.services.XgjClient.call", side_effect=APIError("offline")):
        call_command("rebuild_workspace", refresh_missing_images=True, stdout=output)
    trade.refresh_from_db()
    assert trade.status == "SHIPPING" and trade.paid_fen == 20000
    assert "refresh failed: 1" in output.getvalue()


def test_rebuild_saved_snapshots_is_idempotent_and_preserves_private_data(connection, admin_user):
    trade = make_trade(connection)
    row = trade.platform
    trade.delete()
    call_command("rebuild_workspace")
    rebuilt = Trade.objects.get(platform=row)
    assert rebuilt.status == "SHIPPING" and rebuilt.paid_fen == 20000
    assert not rebuilt.address and not rebuilt.phone
    with pytest.raises(BusinessError, match="收件|地址|信息"):
        create_batches([rebuilt], "shipping", admin_user)
    store_order(connection, payload())
    rebuilt.refresh_from_db()
    assert rebuilt.address and rebuilt.phone
    rebuilt.note = "保留本单备注"
    rebuilt.save()
    before = (rebuilt.address, rebuilt.phone, rebuilt.status_changed_at)
    call_command("rebuild_workspace")
    rebuilt.refresh_from_db()
    assert Trade.objects.filter(platform=row).count() == 1
    assert (rebuilt.address, rebuilt.phone, rebuilt.status_changed_at) == before
    assert rebuilt.note == "保留本单备注"


def test_export_refresh_excludes_refund_and_keeps_history(connection, admin_user, client):
    refund = make_trade(connection)
    shipping = make_trade(connection, number="123456790")
    update_product(refund.product, 6000, "供应商甲", admin_user)
    old = create_batches([refund, shipping], "shipping", admin_user)[0]
    original = old.snapshot
    now = int(timezone.now().timestamp())

    def refreshed(row):
        return store_order(
            connection,
            payload(
                number=row.external_order_no,
                refund_status=1 if row.pk == refund.platform_id else 0,
                update_time=now + 1,
            ),
        )

    client.force_login(admin_user)
    with patch("app.workbench.views.refresh_order", side_effect=refreshed) as refresh:
        response = client.post(
            reverse("wb-export", args=["shipping"]),
            {
                "selected": [str(refund.pk), str(shipping.pk)],
            },
        )
    assert refresh.call_count == 2 and response.status_code == 200
    fresh = response.context["batches"][0]
    assert [row["id"] for row in fresh.snapshot] == [str(shipping.pk)]
    refund.refresh_from_db()
    old.refresh_from_db()
    assert refund.status == "REFUNDING" and old.stale and old.snapshot == original
    history = client.get(reverse("wb-batch", args=[old.pk]))
    assert "联系供应商停止发货" in history.content.decode()
    count = ExportBatch.objects.count()
    with patch(
        "app.workbench.views.refresh_order", side_effect=APIError("同步暂不可用", retryable=True)
    ):
        failed = client.post(
            reverse("wb-export", args=["shipping"]), {"selected": [str(shipping.pk)]}
        )
        assert failed.status_code == 302 and ExportBatch.objects.count() == count
        assert client.get(reverse("wb-batch", args=[old.pk])).status_code == 200


def test_a6_guarantee_sample_and_completion(connection, admin_user, client):
    now = int(timezone.now().timestamp())
    make_trade(connection, pay_amount=10000, order_status=22, confirm_time=now)
    make_trade(
        connection,
        number="123456790",
        order_status=23,
        refund_status=5,
        refund_amount=20000,
        refund_time=now,
    )
    pending = make_trade(
        connection,
        number="123456791",
        pay_amount=30000,
        order_status=21,
        consign_time=now - 86400,
        waybill_no="SYNTHETIC-TRACKING",
    )
    client.force_login(admin_user)
    response = client.get(reverse("dashboard"))
    assert response.context["guarantee"] == 30000
    rows = list(client.get(reverse("wb-orders"), {"guarantee": "1"}).context["rows"])
    assert [t.pk for t in rows] == [pending.pk]
    assert sum(t.guarantee_fen for t in rows) == 30000
    make_trade(
        connection,
        number=pending.number,
        pay_amount=30000,
        order_status=22,
        consign_time=now - 86400,
        confirm_time=now,
        update_time=now + 1,
    )
    assert client.get(reverse("dashboard")).context["guarantee"] == 0


def test_status_sort_is_not_changed_by_sync_or_notes(connection, admin_user, client):
    now = int(timezone.now().timestamp())
    common = {"pay_time": now - 400, "order_time": now - 500, "order_status": 22}
    first = make_trade(connection, **common, confirm_time=now - 300, update_time=now - 300)
    second = make_trade(
        connection, number="123456790", **common, confirm_time=now - 100, update_time=now - 100
    )
    recorded = first.status_changed_at
    first = make_trade(
        connection,
        **common,
        confirm_time=now - 300,
        update_time=now + 1,
        seller_remark="新的平台备注",
    )
    first.note = "新的本单备注"
    first.save()
    assert first.status_changed_at == recorded
    client.force_login(admin_user)
    rows = list(client.get(reverse("wb-orders")).context["rows"])
    assert [t.pk for t in rows] == [second.pk, first.pk]
    first = make_trade(
        connection, **common, confirm_time=now - 300, refund_status=1, update_time=now + 2
    )
    assert first.status == "REFUNDING" and first.status_changed_at > recorded
    detail = client.get(reverse("wb-detail", args=[first.pk]))
    assert "平台订单记录" in detail.content.decode() and "付款时间" in detail.content.decode()


def test_a8_missing_cost_recovery_is_retained_and_filled_once(connection, admin_user, client):
    now = int(timezone.now().timestamp())
    for number in ["123456789", "123456790"]:
        make_trade(
            connection,
            number=number,
            order_status=23,
            refund_status=5,
            refund_time=now,
            refund_amount=20000,
        )
    Trade.objects.all().update(refund_type=2)
    client.force_login(admin_user)
    response = client.get(reverse("wb-refunds"), {"refund_view": "recovery"})
    assert len(response.context["rows"]) == 2 and "待补成本" in response.content.decode()
    trade = Trade.objects.first()
    update_product(trade.product, 6000, "供应商甲", admin_user)
    assert [t.recovery_fen for t in Trade.objects.all()] == [12000, 12000]
    update_product(trade.product, 8000, "供应商甲", admin_user)
    assert [t.recovery_fen for t in Trade.objects.all()] == [12000, 12000]
