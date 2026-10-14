"""Default-off, internal suite credential handoff for a verified Sites binding.

No HTTP route calls these functions. The future privileged provisioner must
authenticate its caller and pass an active superuser operator. The returned
plaintext must be delivered only to mAIstro's encrypted per-business vault.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from api.models import ServiceCredential, SuiteCredentialHandoff
from api.service_auth import DEFAULT_LIFETIME, _checked_scopes, issue_service_credential
from core.models import SuiteTenantBinding, Tenant


class SuiteCredentialHandoffError(ValueError):
    """A handoff is unavailable or conflicts with its immutable operation."""


@dataclass(frozen=True)
class HandoffReceipt:
    credential_id: int
    expires_at: object
    handoff_expires_at: object
    token: str = field(repr=False)


def _operator(operator):
    if (
        operator is None
        or getattr(operator, "pk", None) is None
        or not get_user_model().objects.filter(
            pk=operator.pk, is_active=True, is_superuser=True
        ).exists()
    ):
        raise SuiteCredentialHandoffError("verified operator required")


def _operation_key(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SuiteCredentialHandoffError("valid operation key required") from exc


def _binding_id(value):
    if isinstance(value, bool):
        raise SuiteCredentialHandoffError("verified suite binding required")
    try:
        key = int(value)
    except (TypeError, ValueError) as exc:
        raise SuiteCredentialHandoffError("verified suite binding required") from exc
    if key <= 0:
        raise SuiteCredentialHandoffError("verified suite binding required")
    return key


def _fernet():
    key = getattr(settings, "SUITE_CREDENTIAL_HANDOFF_FERNET_KEY", "") or ""
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError) as exc:
        raise SuiteCredentialHandoffError("handoff encryption unavailable") from exc


def _ttl():
    seconds = getattr(settings, "SUITE_CREDENTIAL_HANDOFF_TTL_SECONDS", 900)
    if not isinstance(seconds, int) or not 0 < seconds <= 3600:
        raise SuiteCredentialHandoffError("handoff lifetime must be at most one hour")
    return seconds


def _binding(binding_id):
    try:
        binding = SuiteTenantBinding.objects.select_for_update().select_related(
            "tenant"
        ).get(pk=binding_id)
    except (SuiteTenantBinding.DoesNotExist, TypeError, ValueError) as exc:
        raise SuiteCredentialHandoffError("verified suite binding required") from exc
    if binding.tenant.owner_id != binding.owner_id:
        raise SuiteCredentialHandoffError("bound tenant owner has changed")
    Tenant.objects.select_for_update().get(pk=binding.tenant_id)
    return binding


@transaction.atomic
def purge_expired_suite_handoffs(*, now=None, binding_id=None):
    """Erase expired ciphertext and revoke unacknowledged pending tokens.

    A privileged maintenance caller should schedule this when handoff is wired.
    Reads and retries also call it, so expired secrets never become readable.
    """
    now = now or timezone.now()
    query = SuiteCredentialHandoff.objects.select_for_update().filter(
        acknowledged_at__isnull=True, expires_at__lte=now
    ).exclude(ciphertext="")
    if binding_id is not None:
        query = query.filter(binding_id=binding_id)
    handoffs = list(query.order_by("pk"))
    for handoff in handoffs:
        ServiceCredential.objects.filter(
            pk=handoff.credential_id, is_pending=True, revoked_at__isnull=True
        ).update(revoked_at=now)
        handoff.ciphertext = ""
        handoff.save(update_fields=["ciphertext"])
    return len(handoffs)


def begin_suite_credential_handoff(
    *, binding_id, operation_key, scopes, operator, rotate=False
) -> HandoffReceipt:
    """Issue/retry a pending 30-day token under an immutable operation UUID."""
    _operator(operator)
    binding_id = _binding_id(binding_id)
    key = _operation_key(operation_key)
    try:
        selected = _checked_scopes(scopes)
    except (TypeError, ValueError) as exc:
        raise SuiteCredentialHandoffError("invalid service credential scopes") from exc
    if not isinstance(rotate, bool):
        raise SuiteCredentialHandoffError("invalid rotation flag")
    crypto = _fernet()  # Fail closed before any credential or cleanup write.
    ttl = _ttl()
    purge_expired_suite_handoffs(binding_id=binding_id)
    with transaction.atomic():
        binding = _binding(binding_id)
        existing = SuiteCredentialHandoff.objects.select_for_update().filter(
            operation_key=key
        ).first()
        if existing is not None:
            if (
                existing.binding_id != binding.pk
                or existing.scopes != selected
                or existing.rotate != rotate
            ):
                raise SuiteCredentialHandoffError("operation conflicts with suite binding")
            if existing.acknowledged_at is not None or not existing.ciphertext:
                raise SuiteCredentialHandoffError("handoff secret is no longer available")
            if existing.expires_at <= timezone.now():
                raise SuiteCredentialHandoffError("handoff secret has expired")
            credential = existing.credential
            if credential.revoked_at is not None or credential.expires_at <= timezone.now():
                raise SuiteCredentialHandoffError("pending credential is inactive")
            try:
                token = crypto.decrypt(existing.ciphertext.encode(), ttl=ttl).decode()
            except InvalidToken as exc:
                raise SuiteCredentialHandoffError("handoff secret is unavailable") from exc
            return HandoffReceipt(credential.pk, credential.expires_at, existing.expires_at, token)

        if SuiteCredentialHandoff.objects.filter(
            binding=binding, acknowledged_at__isnull=True
        ).exclude(ciphertext="").exists():
            raise SuiteCredentialHandoffError("another handoff is pending")
        now = timezone.now()
        active = ServiceCredential.objects.filter(
            tenant_id=binding.tenant_id, is_pending=False,
            revoked_at__isnull=True, expires_at__gt=now,
        ).first()
        if rotate and active is None:
            raise SuiteCredentialHandoffError("rotation requires an unexpired active credential")
        if rotate and sorted(active.scopes) != selected:
            raise SuiteCredentialHandoffError("rotation must preserve credential scopes")
        if not rotate and active is not None:
            raise SuiteCredentialHandoffError("active credential already exists; rotate it")
        try:
            credential, token = issue_service_credential(
                binding.tenant, scopes=selected,
                expires_at=now + DEFAULT_LIFETIME, is_pending=True,
            )
        except ValueError as exc:
            raise SuiteCredentialHandoffError(str(exc)) from exc
        handoff = SuiteCredentialHandoff.objects.create(
            operation_key=key, binding=binding, credential=credential,
            previous_credential=active, scopes=selected, rotate=rotate,
            ciphertext=crypto.encrypt(token.encode()).decode(),
            expires_at=now + timedelta(seconds=ttl),
        )
        return HandoffReceipt(credential.pk, credential.expires_at, handoff.expires_at, token)


@transaction.atomic
def acknowledge_suite_credential_handoff(*, binding_id, operation_key, operator):
    """Activate receipt, revoke old key, and erase plaintext ciphertext atomically."""
    _operator(operator)
    binding_id = _binding_id(binding_id)
    key = _operation_key(operation_key)
    binding = _binding(binding_id)
    handoff = SuiteCredentialHandoff.objects.select_for_update().filter(
        operation_key=key, binding=binding
    ).first()
    if handoff is None:
        raise SuiteCredentialHandoffError("handoff not found for binding")
    credential = ServiceCredential.objects.select_for_update().get(pk=handoff.credential_id)
    if handoff.acknowledged_at is not None:
        return credential
    now = timezone.now()
    if not handoff.ciphertext or handoff.expires_at <= now:
        raise SuiteCredentialHandoffError("handoff secret has expired")
    if credential.revoked_at is not None or credential.expires_at <= now or not credential.is_pending:
        raise SuiteCredentialHandoffError("pending credential is inactive")
    if handoff.previous_credential_id is not None:
        ServiceCredential.objects.filter(
            pk=handoff.previous_credential_id, revoked_at__isnull=True
        ).update(revoked_at=now)
    credential.is_pending = False
    credential.save(update_fields=["is_pending"])
    handoff.ciphertext = ""
    handoff.acknowledged_at = now
    handoff.save(update_fields=["ciphertext", "acknowledged_at"])
    return credential
