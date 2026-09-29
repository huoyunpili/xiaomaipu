from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workbench", "0012_alter_trade_status")]
    operations = [
        migrations.AddField(
            model_name="trade",
            name="review_resolution",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
