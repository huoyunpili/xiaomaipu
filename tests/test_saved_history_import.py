from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from app.integrations.models import Connection
from app.integrations.services import execute_sync, queue_sync, store_order
from app.shops.models import Shop
from app.workbench.models import Trade
from app.workbench.services import import_saved_history
from tests.test_workspace import connection as workspace_connection
from tests.test_workspace import make_trade, payload

connection = workspace_connection
pytestmark = pytest.mark.django_db


def old_order(connection, number, **changes):
    old = int((connection.sync_start_at - timedelta(days=40)).timestamp())
    return store_order(
        connection,
        payload(
            number=number,
            order_time=old,
            pay_time=old + 10,
            update_time=int(timezone.now().timestamp()),
            **changes,
        ),
    )


def test_import_saved_history_keeps_existing_and_uncertain_facts(connection, admin_user):
    existing = make_trade(connection)
    existing.note = "保留原备注"
    existing.unit_cost_fen = 1234
    existing.save()
    before = Trade.objects.filter(pk=existing.pk).values().get()
    ended = int((connection.sync_start_at - timedelta(days=39)).timestamp())
    old_order(connection, "111001", order_status=22, confirm_time=ended)
    old_order(
        connection,
        "111002",
        order_status=23,
        refund_status=5,
        refund_amount=20000,
        refund_time=ended,
    )
    old_order(
        connection, "111003", order_status=23, refund_status=0, refund_amount=0, refund_time=ended
    )
    assert Trade.objects.count() == 1
    imported = import_saved_history(connection, admin_user)
    assert len(imported) == 3
    assert {t.status for t in imported} == {"COMPLETED", "REFUNDED", "REVIEW"}
    assert all(t.source == "API_HISTORY" for t in imported)
    assert all(t.platform.scope_status == "HISTORICAL" for t in imported)
    review = next(t for t in imported if t.status == "REVIEW")
    assert review.profit_fen is None and review.guarantee_fen == 0
    assert review.issue
    assert import_saved_history(connection, admin_user) == []
    assert Trade.objects.filter(pk=existing.pk).values().get() == before


def test_saved_history_import_is_scoped_and_requires_admin(connection, admin_user, operator):
    old_order(connection, "222001", order_status=24)
    other_shop = Shop.objects.create(name="其他店铺", is_active=False)
    other = Connection.objects.create(
        shop=other_shop, actor=admin_user, seller_id="other", sync_start_at=connection.sync_start_at
    )
    old_order(other, "222002", order_status=24)
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        import_saved_history(connection, operator)
    assert Trade.objects.count() == 0
    assert len(import_saved_history(connection, admin_user)) == 1
    assert not Trade.objects.filter(shop=other_shop).exists()


def test_history_sync_scans_provider_window_and_imports_in_one_run(connection, admin_user):
    run = queue_sync(connection, full=True, history_import=True)
    assert run.history_import_requested
    assert 178 * 86400 <= run.window_end - run.window_start <= 179 * 86400
    old = int((connection.sync_start_at - timedelta(days=40)).timestamp())
    client = MagicMock()
    client.call.return_value = {
        "list": [
            payload(
                number="333001",
                order_time=old,
                pay_time=old + 10,
                confirm_time=old + 3600,
                update_time=run.window_start + 10,
                order_status=22,
            )
        ]
    }

    execute_sync(run.pk, client=client, worker_id="history-test")

    run.refresh_from_db()
    trade = Trade.objects.get(number="333001")
    assert run.status == "DONE"
    assert run.historical_count == 1
    assert run.history_imported_count == 1
    assert trade.source == "API_HISTORY"


def test_old_history_upload_page_is_removed(client, admin_user):
    client.force_login(admin_user)
    response = client.post("/workspace/history/", {"file": "ignored"})
    assert response.status_code == 404
