"""Custom-domain add + verify logic shared by the agency dashboard and the
MCP tools (CMS-32). Keeping normalisation, validation, and the verification
threshold in one place means the two surfaces can never drift.
"""
from __future__ import annotations

import re
import socket

from django.conf import settings
from django.db import transaction

from core.models import CustomDomain, Tenant


DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)


def normalize_domain(raw_domain: str) -> str:
    return (raw_domain or "").strip().lower().rstrip(".")


def resolve_a_records(domain: str) -> list:
    """Best-effort A-record lookup for ``domain``. Returns the resolved IPv4
    addresses (empty list on any failure: NXDOMAIN, timeout, or no A record)."""
    try:
        infos = socket.getaddrinfo(domain, None, family=socket.AF_INET)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos})


def add_custom_domain(tenant: Tenant, raw_domain: str):
    """Normalise, validate, and create a ``CustomDomain`` row for ``tenant``.

    Returns ``(custom_domain, error)``; exactly one is set. No external
    registration step: the client just points an A record at our origin. The
    row starts unverified; ``verify_custom_domain`` confirms the DNS resolves
    to us before the route-syncer emits the Traefik router (which is what
    triggers Let's Encrypt issuance).
    """
    domain = normalize_domain(raw_domain)
    if not domain:
        return None, "Enter a domain to add."
    if not DOMAIN_RE.match(domain):
        return None, "That doesn't look like a valid domain (e.g. training.acme.com)."
    if CustomDomain.objects.filter(domain=domain).exists():
        return None, f"“{domain}” is already in use."

    custom_domain = CustomDomain.objects.create(
        tenant=tenant, domain=domain, is_verified=False
    )
    sync_tenant_primary_domain(tenant)
    return custom_domain, None


def verify_custom_domain(custom_domain: CustomDomain):
    """Resolve ``custom_domain``'s A records and flip ``is_verified`` when they
    include ``settings.CUSTOM_DOMAIN_TARGET_IP``. Returns ``(is_verified,
    resolved)``. ``resolved`` is always returned (even on success) so callers
    needing the raw addresses don't have to re-resolve.
    """
    target_ip = settings.CUSTOM_DOMAIN_TARGET_IP
    resolved = resolve_a_records(custom_domain.domain)

    if target_ip in resolved:
        if not custom_domain.is_verified:
            custom_domain.is_verified = True
            custom_domain.save(update_fields=["is_verified", "updated_at"])
        # Sync on every successful check, not only on the flip: re-verifying
        # an already-verified domain is how an operator repairs a drifted
        # Tenant.custom_domain from the dashboard (CMS-63 review fix).
        sync_tenant_primary_domain(custom_domain.tenant)
        return True, resolved

    return False, resolved


def delete_custom_domain(custom_domain: CustomDomain) -> None:
    """Delete ``custom_domain`` and re-sync the owning tenant's display hint.

    Dropping the row removes the host from the next route-syncer pass, so
    Traefik stops serving it; no external cleanup is needed here.
    """
    tenant = custom_domain.tenant
    custom_domain.delete()
    sync_tenant_primary_domain(tenant)


def sync_tenant_primary_domain(tenant: Tenant) -> bool:
    """Keep the legacy ``Tenant.custom_domain`` display hint in step with the
    CustomDomain table: the earliest verified domain, or ``""`` when none.

    Routing and the URL helpers (``core.urls_helpers``) key off the rows;
    this field is only a display hint, but it must not drift after any
    add/verify/delete, whichever surface performed it (CMS-63). Returns
    ``True`` when the stored value changed and refreshes ``tenant`` so the
    caller's instance matches the DB. Idempotent: an in-step tenant costs
    reads only, no write.
    """
    with transaction.atomic():
        # Compare against the persisted value under a row lock, never the
        # caller's in-memory copy: a second instance of the same tenant with
        # a stale custom_domain would otherwise skip a needed write.
        locked = (
            Tenant.objects.select_for_update()
            .only("pk", "custom_domain")
            .get(pk=tenant.pk)
        )
        primary = (
            locked.custom_domains.filter(is_verified=True)
            .order_by("created_at", "pk")
            .values_list("domain", flat=True)
            .first()
            or ""
        )
        changed = locked.custom_domain != primary
        if changed:
            locked.custom_domain = primary
            locked.save(update_fields=["custom_domain", "updated_at"])
    tenant.custom_domain = primary
    return changed
