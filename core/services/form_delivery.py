"""Signed, retryable S11 delivery contract for the S12 mAIstro receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta

import httpx
from cryptography.fernet import InvalidToken
from django.db import transaction
from django.utils import timezone

from core.models import BusinessFormBinding, FormDelivery
from core.services.business_forms import reveal_signing_secret


def delivery_body(submission) -> bytes:
    """Stable event body: replay uses the original event ID and business claim."""
    return json.dumps(
        {
            "version": 1,
            "event_id": str(submission.event_id),
            "tenant_id": str(submission.tenant_id),
            "business_id": str(submission.business_id),
            "form_id": submission.form_id,
            "page_id": submission.page_id,
            "name": submission.name,
            "email": submission.email,
            "phone": submission.phone,
            "message": submission.message,
            "consent": submission.consent,
            "source": submission.source,
            "occurred_at": submission.created_at.isoformat(),
        },
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def delivery_headers(body: bytes, secret: str, tenant_id: str, event_id: str) -> dict:
    stamp = str(int(time.time()))
    signed = b".".join((stamp.encode(), tenant_id.encode(), body))
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Katek-Event-Id": event_id,
        "X-Katek-Tenant-Id": tenant_id,
        "X-Katek-Timestamp": stamp,
        "X-Katek-Signature": f"sha256={digest}",
    }


def verify_delivery_signature(body: bytes, headers: dict, secret: str, tenant_id: str, *, max_age=300) -> bool:
    """Receiver-side verifier shared with S12's contract tests."""
    try:
        stamp = str(headers["X-Katek-Timestamp"])
        if abs(time.time() - int(stamp)) > max_age:
            return False
        if str(headers["X-Katek-Tenant-Id"]) != tenant_id:
            return False
        signed = b".".join((stamp.encode(), tenant_id.encode(), body))
        expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers["X-Katek-Signature"]):
            return False
        payload = json.loads(body)
        return str(payload["tenant_id"]) == tenant_id and str(payload["event_id"]) == str(headers["X-Katek-Event-Id"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        return False


def deliver_pending(*, limit=100, force=False) -> int:
    """Send due outbox rows; 2xx acknowledges, all failures stay retryable."""
    now = timezone.now()
    qs = FormDelivery.objects.filter(status=FormDelivery.PENDING).select_related("submission")
    if not force:
        qs = qs.filter(next_attempt_at__lte=now)
    delivered = 0
    for candidate in qs.order_by("next_attempt_at", "pk")[:limit]:
        with transaction.atomic():
            delivery = FormDelivery.objects.select_for_update().select_related("submission").get(pk=candidate.pk)
            if delivery.status != FormDelivery.PENDING:
                continue
            submission = delivery.submission
            try:
                binding = BusinessFormBinding.objects.get(tenant_id=submission.tenant_id, business_id=submission.business_id)
                body = delivery_body(submission)
                headers = delivery_headers(body, reveal_signing_secret(binding), str(submission.tenant_id), str(submission.event_id))
                response = httpx.post(binding.receiver_url, content=body, headers=headers, timeout=5)
                if not 200 <= response.status_code < 300:
                    raise ValueError(f"Receiver returned {response.status_code}")
            except (httpx.HTTPError, OSError, ValueError, InvalidToken, BusinessFormBinding.DoesNotExist) as exc:
                delivery.attempts += 1
                delivery.last_error = str(exc)[:300]
                delivery.next_attempt_at = now + timedelta(seconds=min(3600, 2 ** min(delivery.attempts, 12)))
                delivery.save(update_fields=["attempts", "last_error", "next_attempt_at"])
                continue
            delivery.status = FormDelivery.DELIVERED
            delivery.delivered_at = timezone.now()
            delivery.attempts += 1
            delivery.last_error = ""
            delivery.save(update_fields=["status", "delivered_at", "attempts", "last_error"])
            delivered += 1
    return delivered
