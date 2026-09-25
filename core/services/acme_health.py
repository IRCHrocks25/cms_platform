"""Self-heal custom domains whose Let's Encrypt order failed (CMS-65).

Traefik opens an ACME order when a router with ``certResolver`` appears, and
retries only when its dynamic config changes. If the first HTTP-01 challenge
fails (typically DNS still propagating), the domain keeps Traefik's default
cert until something touches its router. That used to mean removing and
re-adding the domain by hand.

The route-syncer calls ``check_pending_certificates`` on each pass. For every
verified domain without a confirmed cert it probes the origin over TLS with
the domain as SNI, verifying against the public CA bundle. A valid cert is
recorded and never probed again (Traefik handles renewal itself). A failing
one gets its ``acme_generation`` bumped, which renames its routers in the
generated file (``traefik_routes``) and so forces a fresh order.
"""
from __future__ import annotations

import logging
import socket
import ssl
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import CustomDomain

logger = logging.getLogger(__name__)

# Time the first order gets before we judge it: it runs on the first HTTPS hit
# after the router loads, and the syncer loop alone adds up to 20s.
GRACE_PERIOD = timedelta(minutes=3)
# Let's Encrypt allows 5 failed validations per hostname per hour; 15 minutes
# stays under that even with a manual re-verify in between.
RETRY_INTERVAL = timedelta(minutes=15)
# A domain that still fails after this many automatic retries most likely no
# longer points at us. Stop spending LE attempts; a manual verify resets it.
MAX_AUTO_RETRIES = 5
# Manual re-verify floor, so repeated clicks don't queue several orders.
MANUAL_RETRY_FLOOR = timedelta(minutes=2)

_PROBE_TIMEOUT = 5.0


def _probe_address():
    configured = (getattr(settings, "CUSTOM_DOMAIN_TLS_PROBE_HOST", "") or "").strip()
    host, _, port = (configured or settings.CUSTOM_DOMAIN_TARGET_IP).partition(":")
    return host, int(port or 443)


def probe_certificate(domain: str) -> bool:
    """True when the origin serves a publicly trusted cert valid for ``domain``."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection(_probe_address(), timeout=_PROBE_TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=domain):
                return True
    except (OSError, ssl.SSLError, ValueError):
        return False


def _bump(custom_domain: CustomDomain, now, *, reset_budget: bool) -> None:
    custom_domain.acme_generation += 1
    custom_domain.acme_retry_count = 0 if reset_budget else custom_domain.acme_retry_count + 1
    custom_domain.acme_retried_at = now
    custom_domain.save(
        update_fields=[
            "acme_generation",
            "acme_retry_count",
            "acme_retried_at",
            "updated_at",
        ]
    )


def _confirm(custom_domain: CustomDomain, now) -> None:
    custom_domain.cert_confirmed_at = now
    custom_domain.save(update_fields=["cert_confirmed_at", "updated_at"])


def check_pending_certificates(now=None, probe=None) -> int:
    """Probe verified, unconfirmed domains and bump the ones still failing.

    Returns the number of domains bumped. ``probe`` defaults to
    ``probe_certificate``; tests pass a fake.
    """
    now = now or timezone.now()
    probe = probe or probe_certificate
    bumped = 0
    pending = CustomDomain.objects.filter(
        is_verified=True,
        cert_confirmed_at__isnull=True,
        verified_at__lte=now - GRACE_PERIOD,
    ).order_by("pk")
    for cd in pending:
        if cd.acme_retried_at and now - cd.acme_retried_at < RETRY_INTERVAL:
            continue
        if cd.acme_retry_count >= MAX_AUTO_RETRIES:
            continue
        if probe(cd.domain):
            _confirm(cd, now)
            continue
        _bump(cd, now, reset_budget=False)
        bumped += 1
        if cd.acme_retry_count >= MAX_AUTO_RETRIES:
            logger.warning(
                "Custom domain %s still has no valid certificate after %d "
                "automatic ACME retries; giving up until it is re-verified.",
                cd.domain,
                cd.acme_retry_count,
            )
        else:
            logger.info(
                "Custom domain %s has no valid certificate; forcing ACME retry "
                "(generation %d).",
                cd.domain,
                cd.acme_generation,
            )
    return bumped


def reverify_certificate(custom_domain: CustomDomain, now=None) -> None:
    """Manual re-verify of an already-verified domain: confirm a good cert, or
    force a new order and restore the automatic retry budget."""
    if custom_domain.cert_confirmed_at is not None:
        return
    now = now or timezone.now()
    if custom_domain.acme_retried_at and now - custom_domain.acme_retried_at < MANUAL_RETRY_FLOOR:
        return
    if probe_certificate(custom_domain.domain):
        _confirm(custom_domain, now)
    else:
        _bump(custom_domain, now, reset_budget=True)
