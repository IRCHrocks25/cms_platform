"""Replay due Sites form deliveries after an mAIstro receiver outage."""

from django.core.management.base import BaseCommand, CommandError

from core.services.form_delivery import deliver_pending


class Command(BaseCommand):
    help = "Deliver due business form submissions from the durable outbox"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be positive")
        delivered = deliver_pending(limit=options["limit"])
        self.stdout.write(f"Delivered {delivered} business forms")
