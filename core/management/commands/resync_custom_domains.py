"""Re-sync the legacy ``Tenant.custom_domain`` display hint for every tenant.

The hint must mirror each tenant's earliest *verified* ``CustomDomain`` row
(or be empty). Tenants verified through the MCP tool, or before the dashboard
learned to sync, drifted and were shown ``<sub>.sites.katek.app`` links
(CMS-63). The URL helpers no longer read the field, but the tenant detail
header still does, so repair it once after deploy.

Safe to run repeatedly: it only rewrites the column when the value differs,
never resolves DNS, and never touches the Traefik route file.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from core.models import Tenant
from core.services.custom_domains import sync_tenant_primary_domain


class Command(BaseCommand):
    help = (
        "Point Tenant.custom_domain at each tenant's earliest verified "
        "CustomDomain (or clear it). Idempotent; no DNS or Traefik side effects."
    )

    def handle(self, *args, **options):
        changed = 0
        for tenant in Tenant.objects.order_by("pk"):
            before = tenant.custom_domain
            if sync_tenant_primary_domain(tenant):
                changed += 1
                self.stdout.write(
                    f"{tenant.subdomain} (pk={tenant.pk}): "
                    f"{before or '(empty)'} -> {tenant.custom_domain or '(empty)'}"
                )
        self.stdout.write(
            self.style.SUCCESS(f"{changed} changed, {Tenant.objects.count()} checked")
        )
