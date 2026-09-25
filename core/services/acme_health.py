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
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
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
# Wall-clock cap on probing per syncer pass. Routes are written before the
# probes run, so this only bounds how late the next pass starts; domains not
# reached are probed on a later pass.
PROBE_BUDGET_SECONDS = 10.0

_PROBE_TIMEOUT = 3.0


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


def _bump(custom_domain: CustomDomain, now, *, reset_budget: bool) -> bool:
    """Bump the router generation from the current row, under a row lock.

    The syncer and a manual re-verify in the web container can race on the
    same domain, so every decision is re-made against the locked row rather
    than the caller's copy. Both paths share one ``RETRY_INTERVAL`` window:
    Let's Encrypt counts failed validations per host regardless of who asked.
    Returns False when the locked row says not to bump; ``custom_domain`` is
    refreshed either way.
    """
    with transaction.atomic():
        row = CustomDomain.objects.select_for_update().get(pk=custom_domain.pk)
        allowed = not (row.acme_retried_at and now - row.acme_retried_at < RETRY_INTERVAL)
        if not reset_budget and row.acme_retry_count >= MAX_AUTO_RETRIES:
            allowed = False
        if allowed:
            row.acme_generation += 1
            row.acme_retry_count = 0 if reset_budget else row.acme_retry_count + 1
            row.acme_retried_at = now
            row.save(
                update_fields=[
                    "acme_generation",
                    "acme_retry_count",
                    "acme_retried_at",
                    "updated_at",
                ]
            )
    for field in ("acme_generation", "acme_retry_count", "acme_retried_at"):
        setattr(custom_domain, field, getattr(row, field))
    return allowed


def _confirm(custom_domain: CustomDomain, now) -> None:
    custom_domain.cert_confirmed_at = now
    custom_domain.save(update_fields=["cert_confirmed_at", "updated_at"])


def check_pending_certificates(now=None, probe=None) -> int:
    """Probe verified, unconfirmed domains and bump the ones still failing.

    Returns the number of domains bumped. ``probe`` defaults to
    ``probe_certificate``; tests pass a fake. Stops probing once
    ``PROBE_BUDGET_SECONDS`` of wall-clock time is spent.
    """
    now = now or timezone.now()
    probe = probe or probe_certificate
    started = time.monotonic()
    bumped = 0
    pending = CustomDomain.objects.filter(
        is_verified=True,
        cert_confirmed_at__isnull=True,
        verified_at__lte=now - GRACE_PERIOD,
    ).order_by("acme_retried_at", "pk")
    for cd in pending:
        if time.monotonic() - started >= PROBE_BUDGET_SECONDS:
            break
        if cd.acme_retried_at and now - cd.acme_retried_at < RETRY_INTERVAL:
            continue
        if cd.acme_retry_count >= MAX_AUTO_RETRIES:
            continue
        if probe(cd.domain):
            _confirm(cd, now)
            continue
        if not _bump(cd, now, reset_budget=False):
            continue
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
    force a new order and restore the automatic retry budget. Still bound by
    ``RETRY_INTERVAL`` since the last attempt, automatic or manual."""
    if custom_domain.cert_confirmed_at is not None:
        return
    now = now or timezone.now()
    if custom_domain.acme_retried_at and now - custom_domain.acme_retried_at < RETRY_INTERVAL:
        return
    if probe_certificate(custom_domain.domain):
        _confirm(custom_domain, now)
    else:
        _bump(custom_domain, now, reset_budget=True)
