import json

from django.core.management.base import BaseCommand
from django.db import connection

from app.common.backup import database_fingerprint


class Command(BaseCommand):
    help = "Read-only row counts and content fingerprints for backup verification."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            result = database_fingerprint(cursor)
        self.stdout.write(json.dumps(result, sort_keys=True))
