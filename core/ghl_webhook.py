"""GHL marketplace webhook signature verification (Ed25519) + event handling.

GHL signs marketplace webhooks with a GLOBAL Ed25519 key and sends the
base64 signature in the ``X-GHL-Signature`` header (the legacy RSA
``x-wh-signature`` scheme retired 2026-07-01). The signed message is the raw
request body. GHL publishes the public key; set it (PEM) in
``GHL_WEBHOOK_PUBLIC_KEY``.

Policy: when the key isn't configured we log and ACCEPT (so an unconfigured
deploy doesn't silently drop events); set the key to enforce rejection of
unsigned/forged calls. Verification failures with a configured key are rejected.
"""
from __future__ import annotations

import base64
import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from django.conf import settings

logger = logging.getLogger(__name__)


def signature_configured() -> bool:
    """True when a verification public key is configured."""
    return bool((getattr(settings, "GHL_WEBHOOK_PUBLIC_KEY", "") or "").strip())


def verify_signature(*, body: bytes, signature_b64: str) -> bool:
    """Verify the ``X-GHL-Signature`` (base64 Ed25519) over the raw ``body``.

    Returns False on a missing key/signature, a non-Ed25519 key, a bad base64
    signature, or a signature mismatch. Never raises.
    """
    # Accept the PEM with real newlines OR "\n"-escaped (single-line env var).
    key_pem = (getattr(settings, "GHL_WEBHOOK_PUBLIC_KEY", "") or "").strip().replace("\\n", "\n")
    if not key_pem or not signature_b64:
        return False
    if "BEGIN PUBLIC KEY" not in key_pem:
        # Env holds just the base64 SPKI (no PEM header) -> wrap it. Cleanest
        # way to carry the key in an env var without newline escaping.
        key_pem = f"-----BEGIN PUBLIC KEY-----\n{key_pem}\n-----END PUBLIC KEY-----"
    try:
        pub = load_pem_public_key(key_pem.encode())
    except (ValueError, TypeError) as exc:
        logger.error("GHL_WEBHOOK_PUBLIC_KEY is not a valid PEM public key: %s", exc)
        return False
    if not isinstance(pub, Ed25519PublicKey):
        logger.error("GHL_WEBHOOK_PUBLIC_KEY is not an Ed25519 public key.")
        return False
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    try:
        pub.verify(signature, body)
        return True
    except (InvalidSignature, ValueError):
        return False


def handle_event(payload: dict) -> None:
    """Act on a verified GHL webhook event.

    Currently handles uninstall: an ``*UNINSTALL*`` event marks the matching
    install(s) disconnected (by locationId, or all of an agency's installs by
    companyId). Unknown events are logged and ignored.
    """
    from core.models import GhlInstall

    etype = (payload.get("type") or payload.get("event") or "").upper()
    location_id = payload.get("locationId") or payload.get("location_id") or ""
    company_id = payload.get("companyId") or payload.get("company_id") or ""

    if "UNINSTALL" in etype:
        if location_id:
            GhlInstall.objects.filter(location_id=location_id).update(
                status=GhlInstall.STATUS_DISCONNECTED
            )
        if company_id:
            GhlInstall.objects.filter(agency__company_id=company_id).update(
                status=GhlInstall.STATUS_DISCONNECTED
            )
        logger.info(
            "GHL webhook UNINSTALL: location=%s company=%s", location_id, company_id
        )
    else:
        logger.info("GHL webhook event ignored: type=%s", etype or "unknown")
