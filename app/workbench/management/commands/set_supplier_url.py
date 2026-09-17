from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError

from app.shops.models import Shop
from app.workbench.models import WorkspaceSettings


class Command(BaseCommand):
    help = "Set the public supplier portal base URL; no tokens or order data are printed."

    def add_arguments(self, parser):
        parser.add_argument("url")

    def handle(self, *args, **options):
        url = options["url"].rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise CommandError("A plain HTTPS origin is required.")
        shop = Shop.objects.filter(is_active=True).first()
        if not shop:
            raise CommandError("No active shop.")
        config, _ = WorkspaceSettings.objects.get_or_create(shop=shop)
        config.supplier_base_url = url
        config.save(update_fields=["supplier_base_url", "updated_at"])
        self.stdout.write("Supplier public URL saved. Copy new links from the shipping batch page.")
