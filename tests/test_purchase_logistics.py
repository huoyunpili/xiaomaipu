import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import close_old_connections
from django.utils import timezone

from app.common.business import BusinessError
from app.inventory.models import InventoryBalance, StockLot
from app.procurement.logistics import logistics_action
from app.procurement.models import PurchaseArrival, PurchaseDispatch, PurchaseInspection
from tests.test_procurement import operate, setup_purchase

pytestmark = pytest.mark.django_db


def step(actor, purchase, operation, **kwargs):
    purchase.refresh_from_db()
    result = logistics_action(
        actor=actor,
        submission_key=uuid.uuid4(),
        purchase_id=purchase.pk,
        version=purchase.version,
        operation=operation,
        reason="实际核对",
        **kwargs,
    )
    purchase.refresh_from_db()
    return result


def test_ten_dispatched_six_arrived_four_accepted_three(admin_user):
    p = setup_purchase(admin_user, quantity=10)
    operate(admin_user, p, "order")
    now = timezone.now()
    d = step(
        admin_user,
        p,
        "ship",
        quantity=6,
        occurred_at=now - timedelta(days=3),
        expected_arrival_at=now - timedelta(days=1),
    )
    a = step(
        admin_user,
        p,
        "arrive",
        quantity=4,
        dispatch_id=d["dispatch_id"],
        occurred_at=now - timedelta(hours=1),
    )
    assert InventoryBalance.objects.get().on_hand_qty == 0
    step(admin_user, p, "inspect", quantity=3, arrival_id=a["arrival_id"], occurred_at=now)
    assert (p.unshipped_qty, p.in_transit_qty, p.awaiting_inspection_qty, p.received_qty) == (
        4,
        2,
        1,
        3,
    )
    assert StockLot.objects.get().original_received_at == now
    operate(admin_user, p, "close")
    assert (p.cancelled_qty, p.in_transit_qty) == (4, 2)
    step(admin_user, p, "arrive", quantity=2, dispatch_id=d["dispatch_id"])
    assert p.in_transit_qty == 0 and p.awaiting_inspection_qty == 3


def test_unknown_dispatch_is_not_invented_and_rejection_is_not_cash(admin_user):
    p = setup_purchase(admin_user, quantity=2)
    operate(admin_user, p, "order")
    a = step(admin_user, p, "arrive", quantity=2)
    assert p.shipped_qty == 0 and not PurchaseDispatch.objects.exists()
    step(admin_user, p, "inspect", quantity=1, arrival_id=a["arrival_id"], result="REJECT")
    assert not StockLot.objects.exists()
    assert p.refunded_fen == 0
    step(admin_user, p, "reject_return", quantity=1, arrival_id=a["arrival_id"])
    assert p.total_fen == p.unit_cost_fen and p.refunded_fen == 0
    with pytest.raises(BusinessError):
        step(admin_user, p, "inspect", quantity=2, arrival_id=a["arrival_id"])


def test_parent_mismatch_and_repeated_inspection_do_not_duplicate_stock(admin_user):
    p = setup_purchase(admin_user)
    other = setup_purchase(admin_user)
    operate(admin_user, p, "order")
    operate(admin_user, other, "order")
    a = step(admin_user, p, "arrive", quantity=1)
    with pytest.raises(BusinessError):
        step(admin_user, other, "inspect", quantity=1, arrival_id=a["arrival_id"])
    data = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        purchase_id=p.pk,
        version=p.version,
        operation="inspect",
        quantity=1,
        arrival_id=a["arrival_id"],
        reason="检验",
    )
    assert logistics_action(**data) == logistics_action(**data)
    assert PurchaseInspection.objects.count() == StockLot.objects.count() == 1


def test_loss_preserves_cash_and_audit(admin_user):
    p = setup_purchase(admin_user, quantity=3)
    operate(admin_user, p, "order")
    operate(admin_user, p, "pay", amount_fen=p.total_fen)
    d = step(admin_user, p, "ship", quantity=2)
    step(admin_user, p, "loss", quantity=1, dispatch_id=d["dispatch_id"])
    assert p.in_transit_qty == 1 and p.refunded_fen == 0
    assert p.dispatches.get().dispositions.get().resolved is False
    operate(admin_user, p, "close")
    assert p.cancelled_qty == 1 and p.in_transit_qty == 1


def test_history_confirmation_preserves_unknown_times(admin_user):
    p = setup_purchase(admin_user)
    operate(admin_user, p, "order")
    a = step(admin_user, p, "arrive", quantity=1)
    p.legacy_logistics = True
    p.save()
    d = step(admin_user, p, "history_ship", quantity=2)
    step(
        admin_user, p, "match", quantity=1, arrival_id=a["arrival_id"], dispatch_id=d["dispatch_id"]
    )
    step(admin_user, p, "history_finish", quantity=1)
    assert p.cancelled_qty == 1 and p.in_transit_qty == 1 and not p.legacy_logistics
    assert PurchaseDispatch.objects.get().dispatched_at is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_acceptance_locks_arrival_parent(admin_user):
    p = setup_purchase(admin_user, quantity=1)
    operate(admin_user, p, "order")
    a = step(admin_user, p, "arrive", quantity=1)

    def run(_):
        close_old_connections()
        try:
            logistics_action(
                actor=admin_user,
                submission_key=uuid.uuid4(),
                purchase_id=p.pk,
                version=p.version,
                operation="inspect",
                quantity=1,
                arrival_id=a["arrival_id"],
                reason="并发验收",
            )
            return "ok"
        except BusinessError:
            return "stale"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ["ok", "stale"]
    assert InventoryBalance.objects.get().available_qty == 1
    assert PurchaseArrival.objects.get().inspected_qty == 1


def test_legacy_backfill_is_idempotent_and_never_replays_stock(admin_user):
    from importlib import import_module

    from django.apps import apps
    from django.db import connection

    from app.inventory.services import receive_stock
    from app.procurement.models import PurchaseReceipt

    p = setup_purchase(admin_user, quantity=2)
    stock = receive_stock(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        sku_id=p.sku_id,
        quantity=1,
        unit_cost_fen=p.unit_cost_fen,
    )
    PurchaseReceipt.objects.create(purchase=p, lot_id=stock["lot_id"], quantity=1)
    p.received_qty = p.shipped_qty = 1
    p.save()
    migration = import_module("app.procurement.migrations.0006_legacy_logistics")
    with connection.schema_editor(atomic=False) as editor:
        migration.backfill(apps, editor)
        migration.backfill(apps, editor)
    p.refresh_from_db()
    assert p.legacy_logistics
    assert p.arrivals.count() == 1 and p.arrivals.get().received_at is None
    assert p.arrivals.get().inspections.count() == 1
    assert not p.dispatches.exists()
    assert InventoryBalance.objects.get().available_qty == 1


def test_late_source_matching_does_not_add_stock_again(admin_user):
    p = setup_purchase(admin_user, quantity=2)
    operate(admin_user, p, "order")
    a = step(admin_user, p, "arrive", quantity=1)
    step(admin_user, p, "inspect", quantity=1, arrival_id=a["arrival_id"])
    d = step(
        admin_user, p, "source", quantity=1, arrival_id=a["arrival_id"], tracking_no="BACKFILL"
    )
    assert p.unmatched_qty == 0 and p.in_transit_qty == 0 and p.unshipped_qty == 1
    step(admin_user, p, "eta", dispatch_id=d["dispatch_id"], expected_arrival_at=timezone.now())
    assert InventoryBalance.objects.get().available_qty == 1
