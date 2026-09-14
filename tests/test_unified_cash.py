import uuid
from datetime import timedelta
from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from app.common.business import BusinessError
from app.common.reconciliation import reconcile_current_data
from app.finance.models import CustomerPaymentFact, MoneyEntry
from app.finance.services import record_customer_payment, record_money
from app.inventory.models import InventoryBalance
from app.procurement.models import PurchaseEvent
from app.procurement.services import purchase_action
from tests.test_business import action, draft, goods
from tests.test_procurement import operate, setup_purchase

pytestmark = pytest.mark.django_db


def test_purchase_cash_single_source_and_retry(admin_user):
    purchase = setup_purchase(admin_user, quantity=2)
    operate(admin_user, purchase, "order")
    purchase.refresh_from_db()
    payload = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        purchase_id=purchase.pk,
        version=purchase.version,
        operation="pay",
        amount_fen=6000,
        reason="银行已付款",
    )
    assert purchase_action(**payload) == purchase_action(**payload)
    purchase.refresh_from_db()
    assert purchase.paid_fen == 6000
    cash = purchase.money_entries.get()
    assert (cash.kind, cash.direction, cash.amount_fen) == ("PAYMENT", "OUT", 6000)
    assert cash.purchase_event_id and cash.actor_id == admin_user.pk
    assert cash.occurred_at is None and cash.time_quality == "UNKNOWN"
    operate(admin_user, purchase, "refund", amount_fen=1000)
    assert purchase.money_entries.get(kind="REFUND").direction == "IN"
    assert reconcile_current_data()["differences"] == []
    # Audit events are not another source of cash totals.
    PurchaseEvent.objects.filter(pk=cash.purchase_event_id).update(amount_fen=1)
    assert reconcile_current_data()["differences"] == []
    assert InventoryBalance.objects.get().on_hand_qty == 0


def platform_order(actor):
    sku, lot = goods(actor)
    order = draft(actor, sku, lot=lot, channel="XIANYU")
    action(actor, order, "confirm", reason="已核对买家付款")
    return order


def test_customer_payment_is_not_cash_and_external_key_deduplicates(admin_user, shop):
    order = platform_order(admin_user)
    payload = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        version=order.version,
        amount_fen=20000,
        source_ref="PAY-1",
        evidence="核对付款截图",
    )
    first = record_customer_payment(**payload)
    assert record_customer_payment(**payload) == first
    payload["submission_key"] = uuid.uuid4()
    assert record_customer_payment(**payload) == first
    assert CustomerPaymentFact.objects.count() == 1
    assert not MoneyEntry.objects.exists()
    order.refresh_from_db()
    assert order.received_fen == 0 and order.realized_profit_fen is None
    payload.update(submission_key=uuid.uuid4(), amount_fen=20001)
    with pytest.raises(BusinessError):
        record_customer_payment(**payload)


def test_cash_metadata_rejects_escrow_and_future(admin_user, shop):
    order = platform_order(admin_user)
    payload = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        version=order.version,
        amount_fen=1000,
        kind="RECEIPT",
        reason="到账核对",
    )
    with pytest.raises(BusinessError):
        record_money(**payload, account_type="ESCROW")
    with pytest.raises(BusinessError):
        record_money(**payload, occurred_at=timezone.now() + timedelta(days=1))
    when = timezone.now() - timedelta(days=1)
    record_money(**payload, occurred_at=when, account_type="ALIPAY")
    entry = MoneyEntry.objects.get()
    assert entry.direction == "IN" and entry.occurred_at == when and entry.time_quality == "EXACT"


def test_customer_payment_form_records_fact_only(client, admin_user, shop):
    order = platform_order(admin_user)
    client.force_login(admin_user)
    url = reverse("customer-payment-record", args=[order.pk])
    assert client.get(url).status_code == 200
    result = client.post(
        url,
        dict(
            submission_key=uuid.uuid4(),
            version=order.version,
            amount="200",
            source_ref="FORM-1",
            evidence="核对平台付款记录",
        ),
    )
    assert result.status_code == 302
    assert order.customer_payments.count() == 1 and not order.money_entries.exists()


@pytest.mark.django_db(transaction=True)
def test_upgrade_preserves_old_cash_and_does_not_replay_on_backfill(admin_user, shop):
    order = platform_order(admin_user)
    purchase = setup_purchase(admin_user, quantity=2)
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("finance", "0002_initial")])
        old_apps = executor.loader.project_state(
            [("finance", "0002_initial"), ("procurement", "0006_legacy_logistics")]
        ).apps
        old_money = old_apps.get_model("finance", "MoneyEntry")
        original = old_money.objects.create(
            order_id=order.pk, kind="RECEIPT", amount_fen=1234, reason="旧到账"
        )
        old_event = old_apps.get_model("procurement", "PurchaseEvent")
        event = old_event.objects.create(
            purchase_id=purchase.pk, kind="pay", amount_fen=6000, reason="旧采购付款"
        )
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        cash = MoneyEntry.objects.get(pk=original.pk)
        assert cash.direction == "IN" and cash.source == "LEGACY"
        assert cash.occurred_at is None and cash.created_at == original.created_at
        migrated = MoneyEntry.objects.get(purchase_event_id=event.pk)
        assert migrated.amount_fen == 6000 and migrated.direction == "OUT"
        assert migrated.created_at == event.created_at and migrated.occurred_at is None
        migration = import_module("app.finance.migrations.0003_unified_cash")
        with connection.schema_editor(atomic=False) as editor:
            migration.backfill_cash(apps, editor)
        assert MoneyEntry.objects.count() == 2
    finally:
        MigrationExecutor(connection).migrate(latest)
