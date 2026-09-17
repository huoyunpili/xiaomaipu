from unittest.mock import patch

import pytest

from app.integrations.client import APIError
from app.integrations.models import SyncRun
from app.integrations.services import store_order
from app.integrations.tasks import enrich_synced_orders
from app.workbench.models import Trade
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def test_sync_enriches_shipping_time_image_and_refund_type(connection):
    data = payload(order_status=21, refund_status=3)
    data["goods"]["images"] = []
    row = store_order(connection, data)
    run = SyncRun.objects.create(
        connection=connection,
        status="DONE",
        window_start=data["update_time"] - 1,
        window_end=data["update_time"] + 1,
    )
    detail = {
        **data,
        "consign_time": data["pay_time"],
        "goods": {**data["goods"], "images": ["https://img.alicdn.com/a.png"]},
    }
    refund = {
        "order_no": data["order_no"],
        "refund_status": 3,
        "refund_type": 2,
        "apply_amount": data["pay_amount"],
        "apply_time": data["pay_time"],
    }
    with patch("app.integrations.services.XgjClient.call", side_effect=[detail, refund]) as api:
        enrich_synced_orders(str(run.pk))
    assert [c.args[0] for c in api.call_args_list] == ["detail", "refund_detail"]
    trade = Trade.objects.get(platform=row)
    assert trade.image and trade.shipped_at
    assert trade.refund_type == 2 and trade.status == "REFUNDING"


def test_detail_failure_does_not_undo_list_sync_and_has_bounded_retries(connection):
    data = payload()
    row = store_order(connection, data)
    run = SyncRun.objects.create(
        connection=connection,
        status="DONE",
        window_start=data["update_time"],
        window_end=data["update_time"],
    )
    with (
        patch("app.integrations.tasks.refresh_order", side_effect=APIError("offline")),
        patch("app.integrations.tasks.enrich_synced_orders.apply_async") as retry,
    ):
        enrich_synced_orders(str(run.pk))
        retry.assert_called_once_with(args=[str(run.pk), 1], countdown=60)
        retry.reset_mock()
        enrich_synced_orders(str(run.pk), attempt=2)
        retry.assert_not_called()
    run.refresh_from_db()
    assert run.status == "DONE"
    assert Trade.objects.get(platform=row).status == "SHIPPING"
