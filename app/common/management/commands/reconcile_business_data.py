import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from app.common.reconciliation import reconcile_current_data


class Command(BaseCommand):
    help = "Read-only current-ledger consistency check; missing historical facts are not repaired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check", action="store_true", help="Exit nonzero on ledger differences."
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            report = reconcile_current_data()
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        if options["check"] and report["differences"]:
            raise CommandError("发现账款或库存差异；只读检查没有修改数据。")
