from django.core.management.base import BaseCommand

from app.common.business import BusinessError
from app.integrations.client import APIError
from app.integrations.models import PlatformOrder
from app.integrations.services import refresh_order
from app.workbench.models import Trade
from app.workbench.services import project


class Command(BaseCommand):
    help = "Project in-scope orders and historical carryover obligations without changing legacy accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--refresh-missing-images",
            action="store_true",
            help="Read platform details to fill missing images; requires configured API access.",
        )

    def handle(self, *args, **options):
        count = 0
        for row in PlatformOrder.objects.select_related("connection__shop").iterator():
            if project(row):
                count += 1
        self.stdout.write(f"Projected {count} orders. Missing private fields refresh on next sync.")
        if options["refresh_missing_images"]:
            filled = failed = unavailable = 0
            missing = Trade.objects.filter(image="", platform__isnull=False).select_related(
                "platform__connection"
            )
            for trade in missing.iterator():
                try:
                    refresh_order(trade.platform)
                except (APIError, BusinessError):
                    failed += 1
                    continue
                trade.refresh_from_db(fields=["image"])
                if trade.image:
                    filled += 1
                else:
                    unavailable += 1
            self.stdout.write(
                f"Images filled: {filled}; unavailable: {unavailable}; refresh failed: {failed}."
            )
