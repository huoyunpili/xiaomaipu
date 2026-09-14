import pytest

from app.common.reconciliation import reconcile_current_data
from app.inventory.models import InventoryBalance
from app.procurement.models import Purchase
from tests.test_procurement import operate, setup_purchase

pytestmark = pytest.mark.django_db


def test_reconciles_partial_purchase_without_writes(admin_user, django_assert_num_queries):
    purchase = setup_purchase(admin_user)
    operate(admin_user, purchase, "order")
    operate(admin_user, purchase, "pay", amount_fen=1000)
    operate(admin_user, purchase, "receive", quantity=1)
    before = list(Purchase.objects.values())
    with django_assert_num_queries(7):
        report = reconcile_current_data()
    assert report["differences"] == []
    assert report["totals"]["purchase_paid_fen"] == 1000
    assert report["migration_gaps"]["purchases_with_unverified_dispatch_history"] == 1
    assert list(Purchase.objects.values()) == before


def test_reports_cash_and_stock_drift_without_repair(admin_user):
    purchase = setup_purchase(admin_user)
    Purchase.objects.filter(pk=purchase.pk).update(paid_fen=17)
    InventoryBalance.objects.update(on_hand_qty=2)
    report = reconcile_current_data()
    assert {row["field"] for row in report["differences"]} == {"paid_fen", "on_hand_qty"}
    assert Purchase.objects.get(pk=purchase.pk).paid_fen == 17


@pytest.mark.django_db(transaction=True)
def test_command_uses_read_only_snapshot_and_reports_drift(admin_user):
    import json
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    purchase = setup_purchase(admin_user)
    Purchase.objects.filter(pk=purchase.pk).update(paid_fen=23)
    output = StringIO()
    with pytest.raises(CommandError):
        call_command("reconcile_business_data", check=True, stdout=output)
    report = json.loads(output.getvalue())
    assert report["differences"][0]["stored"] == 23
    assert Purchase.objects.get(pk=purchase.pk).paid_fen == 23
