from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.wsgi import get_wsgi_application
from waitress import serve  # type: ignore[import-untyped]


class Command(BaseCommand):
    help = "Serve the isolated public supplier gateway on loopback only."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=18766)

    def handle(self, *args, **options):
        if settings.ROOT_URLCONF != "app.config.supplier_urls" or settings.DEBUG:
            raise CommandError(
                "Use --settings=app.config.settings.supplier; never expose the admin server."
            )
        serve(
            get_wsgi_application(),
            host="127.0.0.1",
            port=options["port"],
            threads=6,
            max_request_body_size=62 * 1024 * 1024,
            connection_limit=30,
            channel_timeout=120,
            trusted_proxy="127.0.0.1",
            trusted_proxy_headers={"x-forwarded-proto"},
            expose_tracebacks=False,
        )
