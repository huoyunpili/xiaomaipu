import uuid
from datetime import timedelta
from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.catalog.models import SKU
from app.catalog.services import save_product
from app.common.business import BusinessError
from app.finance.services import record_customer_payment
from app.inventory.models import InventoryBalance, StockLot
from app.operations.models import CommitmentRevision, ListingMapping, SupplyAllocation
from app.operations.projections import (
    bottlenecks,
    order_progress,
    purchase_progress,
    supplemental_issues,
)
from app.operations.services import allocate_supply, save_listing_mapping, sync_followups
from app.procurement.allocation import reserve_purchase_receipt
from app.procurement.logistics import logistics_action
from app.procurement.models import Purchase, PurchaseReceipt
from app.procurement.services import create_purchase, save_supplier
from tests.test_business import action, draft, goods, money
from tests.test_procurement import operate, setup_purchase

pytestmark = pytest.mark.django_db


def logistics(actor, purchase, operation, **kwargs):
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


def platform_order(actor):
    sku, lot = goods(actor)
    order = draft(actor, sku, lot=lot, channel="XIANYU")
    action(actor, order, "confirm", reason="已核对买家付款")
    return order


def test_k1_k2_k3_k4_k5_k7_use_independent_facts(admin_user, shop):
    now = timezone.now()
    paid = setup_purchase(admin_user, quantity=2)
    operate(admin_user, paid, "order")
    operate(admin_user, paid, "pay", amount_fen=1000)

    late = setup_purchase(admin_user, quantity=2)
    operate(admin_user, late, "order")
    logistics(
        admin_user,
        late,
        "ship",
        quantity=2,
        occurred_at=now - timedelta(days=3),
        expected_arrival_at=now - timedelta(days=1),
    )

    received = setup_purchase(admin_user, quantity=1)
    operate(admin_user, received, "order")
    logistics(admin_user, received, "arrive", quantity=1, occurred_at=now - timedelta(days=1))

    old_sku, old_lot = goods(admin_user, quantity=2)
    StockLot.objects.filter(pk=old_lot.pk).update(original_received_at=now - timedelta(days=100))

    waiting = platform_order(admin_user)
    record_customer_payment(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=waiting.pk,
        version=waiting.version,
        amount_fen=waiting.amount_fen,
        source_ref="K4-PAY",
        evidence="平台付款记录",
        occurred_at=now - timedelta(hours=2),
    )

    delivered_sku, delivered_lot = goods(admin_user)
    delivered = draft(admin_user, delivered_sku, lot=delivered_lot)
    action(admin_user, delivered, "confirm")
    action(admin_user, delivered, "ship", delivery_method="HANDOVER")
    money(admin_user, delivered, "RECEIPT", 1000)

    cards = bottlenecks(as_of=now)
    kinds = {card["kind"] for card in cards}
    assert kinds == {"K1", "K2", "K3", "K4", "K5", "K7"}
    assert (
        len([card for card in cards if card["kind"] == "K5" and card["object_id"] == delivered.pk])
        == 1
    )
    assert next(card for card in cards if card["kind"] == "K1")["amount_fen"] == 1000
    assert next(card for card in cards if card["kind"] == "K2")["quantity"] == 2
    assert next(card for card in cards if card["kind"] == "K3")["object_id"] == old_lot.pk
    assert next(card for card in cards if card["kind"] == "K4")["object_id"] == waiting.pk
    assert next(card for card in cards if card["kind"] == "K7")["amount_fen"] == received.total_fen

    assert purchase_progress([late.pk])[0]["in_transit_qty"] == 2
    assert order_progress([waiting.pk])[0]["unshipped_qty"] == 1
    assert InventoryBalance.objects.get(sku=old_sku).available_qty == 2


def test_unknown_dates_are_supplemental_not_false_overdue(admin_user):
    purchase = setup_purchase(admin_user, quantity=1)
    operate(admin_user, purchase, "order")
    logistics(admin_user, purchase, "ship", quantity=1)
    sku, lot = goods(admin_user)
    issues = supplemental_issues()
    assert issues["dispatches_without_eta"] == 1
    assert issues["lots_without_original_time"] >= 1
    assert not [card for card in bottlenecks() if card["kind"] in {"K2", "K3"}]
    assert StockLot.objects.get(pk=lot.pk).original_received_at is None
    assert InventoryBalance.objects.get(sku=sku).available_qty == 2


def test_followup_resolves_from_facts_and_reopens_new_cycle(admin_user):
    _, lot = goods(admin_user)
    StockLot.objects.filter(pk=lot.pk).update(
        original_received_at=timezone.now() - timedelta(days=100)
    )
    cards = sync_followups(bottlenecks(kind="K3"))
    first = cards[0]["followup"]
    assert first.cycle == 1 and first.resolved_at is None
    StockLot.objects.filter(pk=lot.pk).update(on_hand_qty=0)
    InventoryBalance.objects.filter(sku=lot.sku).update(on_hand_qty=0)
    sync_followups(bottlenecks())
    first.refresh_from_db()
    assert first.resolved_at is not None
    StockLot.objects.filter(pk=lot.pk).update(on_hand_qty=1)
    InventoryBalance.objects.filter(sku=lot.sku).update(on_hand_qty=1)
    second = sync_followups(bottlenecks(kind="K3"))[0]["followup"]
    assert second.cycle == 2 and second.pk != first.pk


def test_listing_mapping_conflict_and_shared_sku(admin_user, shop):
    sku1, _ = goods(admin_user)
    sku2 = SKU.objects.get(
        pk=save_product(actor=admin_user, submission_key=uuid.uuid4(), name="另一商品")["sku_id"]
    )
    channel = shop.channels.get(code="XIANYU") if hasattr(shop, "channels") else None
    if channel is None:
        from app.shops.models import SalesChannel

        channel = SalesChannel.objects.get(code="XIANYU")
    args = dict(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        sku_id=sku1.pk,
        channel_id=channel.pk,
        external_listing_id="GOODS-1",
        external_spec_id="RED",
        evidence="人工核对链接",
    )
    first = save_listing_mapping(**args)
    args["submission_key"] = uuid.uuid4()
    assert save_listing_mapping(**args)["mapping_id"] == first["mapping_id"]
    args.update(submission_key=uuid.uuid4(), sku_id=sku2.pk)
    with pytest.raises(BusinessError):
        save_listing_mapping(**args)
    assert ListingMapping.objects.get().sku_id == sku1.pk


def test_one_purchase_can_plan_supply_for_two_orders(admin_user, shop):
    sku_id = save_product(actor=admin_user, submission_key=uuid.uuid4(), name="共享供货商品")[
        "sku_id"
    ]
    sku = SKU.objects.get(pk=sku_id)
    orders = [draft(admin_user, sku) for _ in range(2)]
    for order in orders:
        action(admin_user, order, "confirm")
    supplier = save_supplier(actor=admin_user, submission_key=uuid.uuid4(), name="供货人")
    created = create_purchase(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        supplier_id=supplier["supplier_id"],
        sku_id=sku.pk,
        quantity=2,
        unit_cost_fen=100,
    )
    purchase = Purchase.objects.get(pk=created["purchase_id"])
    for order in orders:
        allocate_supply(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            purchase_id=purchase.pk,
            order_item_id=order.items.get().pk,
            quantity=1,
        )
    assert SupplyAllocation.objects.filter(purchase=purchase).count() == 2
    with pytest.raises(BusinessError):
        allocate_supply(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            purchase_id=purchase.pk,
            order_item_id=orders[0].items.get().pk,
            quantity=2,
        )
    operate(admin_user, purchase, "order")
    operate(
        admin_user,
        purchase,
        "receive",
        quantity=2,
        condition_description="两件实际货况已核对",
    )
    receipt = PurchaseReceipt.objects.get(purchase=purchase)
    for order in orders:
        order.refresh_from_db()
        receipt.lot.refresh_from_db()
        reserve_purchase_receipt(
            actor=admin_user,
            submission_key=uuid.uuid4(),
            receipt_id=receipt.pk,
            order_item_id=order.items.get().pk,
            version=order.version,
            lot_version=receipt.lot.version,
            quantity=1,
            acknowledged=True,
        )
    assert [order.items.get().reserved_qty for order in orders] == [1, 1]


def test_deadline_revisions_and_k6_unknown_page(client, admin_user):
    purchase = setup_purchase(admin_user, quantity=1)
    operate(admin_user, purchase, "order")
    first = timezone.now() + timedelta(days=2)
    logistics(admin_user, purchase, "promise", expected_arrival_at=first)
    second = first + timedelta(days=1)
    logistics(admin_user, purchase, "promise", expected_arrival_at=second)
    revisions = list(CommitmentRevision.objects.filter(object_id=purchase.pk))
    assert len(revisions) == 2
    assert revisions[1].old_due_at == first and revisions[1].new_due_at == second
    client.force_login(admin_user)
    response = client.get(reverse("bottleneck-list"))
    assert response.status_code == 200
    assert "K6 售后钱货不同步：暂不可计算" in response.content.decode()


def test_t3_backfill_is_idempotent(admin_user, shop):
    sku_id = save_product(actor=admin_user, submission_key=uuid.uuid4(), name="历史供货商品")[
        "sku_id"
    ]
    sku = SKU.objects.get(pk=sku_id)
    order = draft(admin_user, sku)
    action(admin_user, order, "confirm")
    purchase = setup_purchase(admin_user, quantity=1)
    Purchase.objects.filter(pk=purchase.pk).update(sku=sku, order_item=order.items.get())
    migration = import_module("app.operations.migrations.0001_bottleneck_workbench")
    with connection.schema_editor(atomic=False) as editor:
        migration.backfill_t3(apps, editor)
        migration.backfill_t3(apps, editor)
    allocation = SupplyAllocation.objects.get(purchase=purchase)
    assert allocation.quantity == 1 and allocation.source == "LEGACY"


def test_projection_query_count_does_not_grow_with_child_rows(admin_user, shop):
    order = platform_order(admin_user)
    record_customer_payment(
        actor=admin_user,
        submission_key=uuid.uuid4(),
        order_id=order.pk,
        version=order.version,
        amount_fen=order.amount_fen,
        source_ref="QUERY-K4",
        evidence="查询测试",
    )
    with CaptureQueriesContext(connection) as captured:
        cards = bottlenecks()
    assert len(captured) <= 14
    assert len([card for card in cards if card["kind"] == "K4"]) == 1


def test_operator_cannot_change_thresholds(client, operator):
    client.force_login(operator)
    assert client.get(reverse("bottleneck-thresholds")).status_code == 403
