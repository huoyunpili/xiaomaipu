from django.core.management.base import BaseCommand

from app.workbench.exports import cleanup_images


class Command(BaseCommand):
    help = "Clean expired PNGs using each shop's configured retention; snapshots are kept."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        count = cleanup_images(dry_run=options["dry_run"])
        self.stdout.write(f"Expired images: {count}; dry_run={options['dry_run']}")
