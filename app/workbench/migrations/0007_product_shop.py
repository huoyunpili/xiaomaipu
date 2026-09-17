import django.db.models.deletion
from django.db import migrations, models


def split_products(apps, schema_editor):
    Product = apps.get_model("workbench", "ProductCost")
    Trade = apps.get_model("workbench", "Trade")
    Revision = apps.get_model("workbench", "CostVersion")
    alias = schema_editor.connection.alias
    for product in Product.objects.using(alias).all().iterator():
        shops = list(
            Trade.objects.using(alias)
            .filter(product_id=product.pk)
            .order_by("shop_id")
            .values_list("shop_id", flat=True)
            .distinct()
        )
        if not shops:
            # Unreferenced legacy configurations have no provable owner; preserve, but hide.
            continue
        product.shop_id = shops[0]
        product.save(using=alias, update_fields=["shop"])
        fields = {
            name: getattr(product, name)
            for name in (
                "key",
                "title",
                "spec",
                "unit_fen",
                "version",
                "supplier",
                "supplier_wechat",
                "shipping_note",
            )
        }
        revisions = list(Revision.objects.using(alias).filter(product_id=product.pk))
        for shop_id in shops[1:]:
            clone = Product.objects.using(alias).create(shop_id=shop_id, **fields)
            Product.objects.using(alias).filter(pk=clone.pk).update(
                created_at=product.created_at, updated_at=product.updated_at
            )
            for revision in revisions:
                Revision.objects.using(alias).create(
                    product_id=clone.pk,
                    version=revision.version,
                    unit_fen=revision.unit_fen,
                    effective_at=revision.effective_at,
                )
            Trade.objects.using(alias).filter(product_id=product.pk, shop_id=shop_id).update(
                product_id=clone.pk
            )


class Migration(migrations.Migration):
    dependencies = [("workbench", "0006_product_notes")]
    operations = [
        migrations.AddField(
            model_name="productcost",
            name="shop",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, to="shops.shop"
            ),
        ),
        migrations.AlterField(
            model_name="productcost", name="key", field=models.CharField(max_length=64)
        ),
        migrations.RunPython(split_products),
        migrations.AddConstraint(
            model_name="productcost",
            constraint=models.UniqueConstraint(fields=("shop", "key"), name="wb_product_shop_key"),
        ),
    ]
