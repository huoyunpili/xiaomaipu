from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("integrations", "0005_externalfactapplication_platformrefund_and_more")]

    operations = [
        migrations.AddField(
            model_name="syncrun",
            name="history_import_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="syncrun",
            name="history_imported_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
