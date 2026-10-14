"""Tenant-scoped runtime credentials for Sites MCP.

Only the privileged suite provisioner may call the issuer. Runtime tokens can
never provision a tenant or inherit a Django user's platform role.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from api.auth import TenantScope
from api.models import ServiceCredential, SuiteCredentialHandoff
from core.models import Tenant

SERVICE_SCOPES = frozenset({"read", "draft", "publish", "domain"})
DEFAULT_LIFETIME = timedelta(days=30)
_TOKEN = re.compile(r"^sites_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True)
class ServicePrincipal:
    """A single-tenant runtime identity, separate from user OAuth authority."""

    credential_id: int
    tenant: Tenant
    scopes: frozenset[str]
    user = None
    platform_role = None
    is_service = True

    @property
    def tenant_scopes(self) -> tuple[TenantScope, ...]:
        return (TenantScope(tenant=self.tenant, role="service"),)

    def for_tenant(self, tenant: Tenant) -> TenantScope | None:
        if tenant.pk == self.tenant.pk:
            return TenantScope(tenant=self.tenant, role="service")
        return None


def _checked_scopes(scopes) -> list[str]:
    selected = set(scopes)
    if not selected or not selected <= SERVICE_SCOPES:
        raise ValueError("invalid service credential scopes")
    return sorted(selected)


def issue_service_credential(
    tenant: Tenant, *, scopes, expires_at=None, is_pending=False
) -> tuple[ServiceCredential, str]:
    """Mint once for a verified tenant; plaintext is returned once, never saved."""
    selected = _checked_scopes(scopes)
    if tenant.pk is None:
        raise ValueError("tenant must be saved")
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        now = timezone.now()
        ServiceCredential.objects.filter(
            tenant_id=tenant.pk, revoked_at__isnull=True, expires_at__lte=now
        ).update(revoked_at=now)
        outstanding = ServiceCredential.objects.filter(
            tenant_id=tenant.pk, revoked_at__isnull=True
        )
        if outstanding.filter(is_pending=True).exists():
            raise ValueError("tenant already has a pending service credential")
        if not is_pending and outstanding.filter(is_pending=False).exists():
            raise ValueError("tenant already has an active service credential; rotate it")
        expires_at = expires_at or now + DEFAULT_LIFETIME
        key_id = uuid.uuid4()
        token = f"sites_{key_id.hex}_{secrets.token_urlsafe(32)}"
        credential = ServiceCredential.objects.create(
            tenant=tenant,
            key_id=key_id,
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            scopes=selected,
            is_pending=is_pending,
            expires_at=expires_at,
        )
        return credential, token


def resolve_service_token(token: str) -> ServicePrincipal | None:
    match = _TOKEN.fullmatch(token or "")
    if match is None:
        return None
    credential = (
        ServiceCredential.objects.select_related("tenant")
        .filter(key_id=uuid.UUID(hex=match.group(1)))
        .first()
    )
    if credential is None:
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    if not hmac.compare_digest(credential.token_hash, digest):
        return None
    if credential.is_pending or credential.revoked_at is not None or credential.expires_at <= timezone.now():
        return None
    scopes = frozenset(credential.scopes)
    if not scopes or not scopes <= SERVICE_SCOPES:
        return None
    return ServicePrincipal(credential.pk, credential.tenant, scopes)


def revoke_service_credential(credential: ServiceCredential) -> None:
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=credential.tenant_id)
        ServiceCredential.objects.filter(
            tenant_id=credential.tenant_id, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        # A tenant-wide revoke also destroys any still-retryable handoff secret.
        SuiteCredentialHandoff.objects.filter(
            binding__tenant_id=credential.tenant_id, acknowledged_at__isnull=True
        ).exclude(ciphertext="").update(ciphertext="")


@transaction.atomic
def rotate_service_credential(
    credential: ServiceCredential, *, expires_at=None
) -> tuple[ServiceCredential, str]:
    Tenant.objects.select_for_update().get(pk=credential.tenant_id)
    current = ServiceCredential.objects.select_for_update().get(pk=credential.pk)
    if current.revoked_at is not None or current.expires_at <= timezone.now():
        raise ValueError("cannot rotate inactive credential")
    if ServiceCredential.objects.filter(
        tenant_id=current.tenant_id, is_pending=True, revoked_at__isnull=True
    ).exists():
        raise ValueError("cannot rotate while a suite handoff is pending")
    current.revoked_at = timezone.now()
    current.save(update_fields=["revoked_at"])
    replacement, token = issue_service_credential(
        current.tenant, scopes=current.scopes, expires_at=expires_at
    )
    return replacement, token
