"""Read-only checks for the existing ledger; never infer missing business facts."""

from collections import Counter
from typing import Any
from uuid import UUID

from app.finance.models import MoneyEntry
from app.inventory.models import InventoryBalance, StockLot
from app.orders.models import SalesOrder
from app.procurement.models import Purchase, PurchaseEvent, PurchaseReceipt


def reconcile_current_data():
    row: Any
    report: dict[str, Any] = {
        "schema": 1,
        "counts": {},
        "totals": {},
        "differences": [],
        "migration_gaps": {},
    }

    def compare(kind, obj_id, field, stored, ledger):
        if stored != ledger:
            report["differences"].append(
                {
                    "kind": kind,
                    "id": str(obj_id),
                    "field": field,
                    "stored": stored,
                    "ledger": ledger,
                }
            )

    money: Counter[tuple[UUID, str]] = Counter()
    for row in MoneyEntry.objects.values("order_id", "kind", "amount_fen"):
        money[row["order_id"], row["kind"]] += row["amount_fen"]
    orders = list(SalesOrder.objects.values("id", "received_fen", "refunded_fen"))
    for row in orders:
        compare(
            "order", row["id"], "received_fen", row["received_fen"], money[row["id"], "RECEIPT"]
        )
        compare("order", row["id"], "refunded_fen", row["refunded_fen"], money[row["id"], "REFUND"])

    payments: Counter[tuple[UUID, str]] = Counter()
    for row in PurchaseEvent.objects.values("purchase_id", "kind", "amount_fen"):
        payments[row["purchase_id"], row["kind"]] += row["amount_fen"]
    receipts: Counter[tuple[UUID, str]] = Counter()
    for row in PurchaseReceipt.objects.values("purchase_id", "quantity", "returned_qty"):
        receipts[row["purchase_id"], "received_qty"] += row["quantity"]
        receipts[row["purchase_id"], "returned_qty"] += row["returned_qty"]
    purchases = list(
        Purchase.objects.values(
            "id",
            "paid_fen",
            "refunded_fen",
            "received_qty",
            "returned_qty",
            "direct_qty",
            "closed",
            "shipped_qty",
        )
    )
    for row in purchases:
        for field, kind in (("paid_fen", "pay"), ("refunded_fen", "refund")):
            compare("purchase", row["id"], field, row[field], payments[row["id"], kind])
        for field in ("received_qty", "returned_qty"):
            compare("purchase", row["id"], field, row[field], receipts[row["id"], field])

    stock: Counter[tuple[UUID, str]] = Counter()
    lots = list(StockLot.objects.values("sku_id", "on_hand_qty", "reserved_qty"))
    for row in lots:
        for field in ("on_hand_qty", "reserved_qty"):
            stock[row["sku_id"], field] += row[field]
    balances = list(InventoryBalance.objects.values("sku_id", "on_hand_qty", "reserved_qty"))
    for row in balances:
        for field in ("on_hand_qty", "reserved_qty"):
            compare("inventory", row["sku_id"], field, row[field], stock[row["sku_id"], field])
    balance_ids = {row["sku_id"] for row in balances}
    for sku_id in {row["sku_id"] for row in lots} - balance_ids:
        compare("inventory", sku_id, "balance_present", False, True)

    report["counts"] = {"orders": len(orders), "purchases": len(purchases), "lots": len(lots)}
    report["totals"] = {
        "sales_received_fen": sum(row["received_fen"] for row in orders),
        "sales_refunded_fen": sum(row["refunded_fen"] for row in orders),
        "purchase_paid_fen": sum(row["paid_fen"] for row in purchases),
        "purchase_refunded_fen": sum(row["refunded_fen"] for row in purchases),
        "on_hand_qty": sum(row["on_hand_qty"] for row in balances),
        "reserved_qty": sum(row["reserved_qty"] for row in balances),
    }
    report["migration_gaps"] = {
        "purchases_with_unverified_dispatch_history": sum(
            row["shipped_qty"] > 0 for row in purchases
        ),
        "direct_purchases_without_independent_arrival": sum(
            row["direct_qty"] > 0 for row in purchases
        ),
        "closed_purchases_with_unresolved_dispatch_balance": sum(
            row["closed"] and row["shipped_qty"] > row["received_qty"] + row["direct_qty"]
            for row in purchases
        ),
        "lots_without_original_business_time": len(lots),
    }
    return report
