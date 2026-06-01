"""Regenerate the Traefik custom-domain dynamic file from the DB.

Run by the isolated ``route-syncer`` compose service on a loop; also runnable
by hand for an immediate sync after onboarding or to recover from drift:

    docker exec <route-syncer> python manage.py sync_traefik_routes

No-op unless ``TRAEFIK_DYNAMIC_DIR`` is set and points at a writable dir, so it's
safe to run anywhere (dev included).
"""
from django.core.management.base import BaseCommand

from core.services.traefik_routes import (
    ROUTES_FILENAME,
    _dynamic_dir,
    sync_custom_domain_routes,
)


class Command(BaseCommand):
    help = "Regenerate the Traefik custom-domain dynamic file from verified CustomDomains."

    def handle(self, *args, **options):
        target_dir = _dynamic_dir()
        if not target_dir:
            self.stdout.write(
                "TRAEFIK_DYNAMIC_DIR is not set — nothing to write (no-op)."
            )
            return
        # Runs in the syncer's `while true` loop. Never raise: on first deploy the
        # DB may not be migrated yet (web owns migrations), so the CustomDomain
        # query can fail transiently — log and exit 0 so the loop retries cleanly.
        try:
            ok = sync_custom_domain_routes()
        except Exception as exc:  # noqa: BLE001 — loop must survive any error
            self.stderr.write(f"Route sync errored (will retry): {exc!r}")
            return
        if ok:
            self.stdout.write(
                self.style.SUCCESS(f"Up to date: {target_dir}/{ROUTES_FILENAME}")
            )
        else:
            self.stderr.write(
                f"Route sync did not complete (dir missing/unwritable: {target_dir})."
            )
