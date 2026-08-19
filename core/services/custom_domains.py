"""Custom-domain add + verify logic shared by the agency dashboard and the
MCP tools (CMS-32). Keeping normalisation, validation, and the verification
threshold in one place means the two surfaces can never drift.
"""
from __future__ import annotations

import re
import socket

from django.conf import settings

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
        return True, resolved

    return False, resolved
