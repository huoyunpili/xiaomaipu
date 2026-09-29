from django.db import migrations
from django.db.models import Q


def enable_existing_connections(apps, schema_editor):
    # Keep credential replacement and unknown historical boundaries blocked.
    Connection = apps.get_model("integrations", "Connection")
    Connection.objects.using(schema_editor.connection.alias).filter(
        enabled=False,
        sync_start_at__isnull=False,
        actor__is_active=True,
    ).filter(Q(actor__role="ADMIN") | Q(actor__is_superuser=True)).exclude(
        error__startswith="API 配置已更新"
    ).update(enabled=True)


class Migration(migrations.Migration):
    dependencies = [("integrations", "0006_syncrun_history_import")]
    operations = [migrations.RunPython(enable_existing_connections, migrations.RunPython.noop)]
