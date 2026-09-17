from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workbench", "0004_private_export_images")]

    operations = [
        migrations.AddField(
            model_name="workspacesettings",
            name="reference_days",
            field=models.PositiveIntegerField(default=10),
        ),
    ]
