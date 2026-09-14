from django.db import migrations


def backfill(apps, schema_editor):
    Purchase = apps.get_model("procurement", "Purchase")
    Receipt = apps.get_model("procurement", "PurchaseReceipt")
    Arrival = apps.get_model("procurement", "PurchaseArrival")
    Inspection = apps.get_model("procurement", "PurchaseInspection")
    Dispatch = apps.get_model("procurement", "PurchaseDispatch")
    Shipment = apps.get_model("orders", "Shipment")
    alias = schema_editor.connection.alias
    for purchase in Purchase.objects.using(alias).all().iterator():
        if purchase.shipped_qty or purchase.received_qty or purchase.cancelled_qty:
            Purchase.objects.using(alias).filter(pk=purchase.pk).update(legacy_logistics=True)
        for receipt in Receipt.objects.using(alias).filter(purchase_id=purchase.pk):
            if Inspection.objects.using(alias).filter(receipt_id=receipt.pk).exists():
                continue
            arrival = Arrival.objects.using(alias).create(
                purchase_id=purchase.pk,
                quantity=receipt.quantity,
                inspected_qty=receipt.quantity,
                source="LEGACY",
            )
            Inspection.objects.using(alias).create(
                arrival_id=arrival.pk,
                quantity=receipt.quantity,
                result="ACCEPT",
                receipt_id=receipt.pk,
                source="LEGACY",
                reason="历史入库来源；业务时间未知",
            )
        if purchase.direct:
            for shipment in Shipment.objects.using(alias).filter(purchase_id=purchase.pk):
                Dispatch.objects.using(alias).get_or_create(
                    shipment_id=shipment.pk,
                    defaults=dict(
                        purchase_id=purchase.pk,
                        quantity=shipment.quantity,
                        customer=True,
                        carrier=shipment.carrier,
                        tracking_no=shipment.tracking_no,
                        source="LEGACY",
                    ),
                )


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0005_purchase_logistics"),
        ("inventory", "0002_purchase_logistics"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
