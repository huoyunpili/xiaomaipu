import django.utils.timezone
from django.db import migrations, models
from django.db.models.functions import Coalesce


def backfill(apps, schema_editor):
    Trade = apps.get_model("workbench", "Trade")
    Trade.objects.using(schema_editor.connection.alias).update(
        status_changed_at=models.Case(
            models.When(status="COMPLETED", then=Coalesce("completed_at", "updated_at")),
            models.When(status="REFUNDED", then=Coalesce("refunded_at", "updated_at")),
            models.When(status="REFUNDING", then=Coalesce("refund_applied_at", "updated_at")),
            default=models.F("updated_at"),
            output_field=models.DateTimeField(),
        )
    )


class Migration(migrations.Migration):
    dependencies = [("workbench", "0007_product_shop")]
    operations = [
        migrations.AddField(
            model_name="trade",
            name="status_changed_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
