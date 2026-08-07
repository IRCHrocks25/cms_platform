from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from ninja.security import HttpBearer
from oauth2_provider.models import AccessToken

from core.models import Tenant, TenantMembership


User = get_user_model()


@dataclass(frozen=True)
class TenantScope:
    tenant: Tenant
    role: str


@dataclass(frozen=True)
class ResolvedAuth:
    """Concrete principal derived from an OAuth access token.

    Superusers are platform-wide. Everyone else (including staff) is scoped
    strictly to their ``TenantMembership`` rows. A missing membership never
    falls back to a default tenant. Tokens must be issued by the configured
    Claude OAuth application.
    """

    user: object
    platform_role: Optional[str]
    scopes: tuple[TenantScope, ...]

    def for_tenant(self, tenant: Tenant) -> Optional[TenantScope]:
        if self.platform_role:
            return TenantScope(tenant=tenant, role=self.platform_role)
        for scope in self.scopes:
            if scope.tenant.pk == tenant.pk:
                return scope
        return None


def build_consent_contexts(user) -> list[dict]:
    """Tenant/role rows shown on the OAuth consent screen."""
    contexts: list[dict] = []
    if user.is_superuser:
        contexts.append(
            {
                "tenant_name": None,
                "tenant_subdomain": None,
                "role": "superadmin",
                "platform": True,
            }
        )

    memberships = (
        TenantMembership.objects.select_related("tenant")
        .filter(user=user)
        .order_by("tenant__name")
    )
    for membership in memberships:
        contexts.append(
            {
                "tenant_name": membership.tenant.name,
                "tenant_subdomain": membership.tenant.subdomain,
                "role": membership.role,
                "platform": False,
            }
        )
    return contexts


def _issued_by_claude_client(access: AccessToken) -> bool:
    expected = (settings.CLAUDE_OAUTH_CLIENT_ID or "").strip()
    if not expected:
        return False
    application = access.application
    if application is None:
        return False
    return application.client_id == expected


def resolve_access_token(token: str) -> Optional[ResolvedAuth]:
    """Resolve a bearer token to ``(user, role, tenant)`` scopes, or ``None``."""
    if not token:
        return None

    access = (
        AccessToken.objects.select_related("user", "application")
        .filter(token=token)
        .first()
    )
    if access is None or not access.is_valid():
        return None

    if not _issued_by_claude_client(access):
        return None

    user = access.user
    if user is None or not user.is_active:
        return None

    if user.is_superuser:
        return ResolvedAuth(user=user, platform_role="superadmin", scopes=())

    memberships = list(
        TenantMembership.objects.select_related("tenant")
        .filter(user=user)
        .order_by("tenant__id")
    )
    if not memberships:
        return None

    scopes = tuple(
        TenantScope(tenant=m.tenant, role=m.role) for m in memberships
    )
    return ResolvedAuth(user=user, platform_role=None, scopes=scopes)


class CmsBearerAuth(HttpBearer):
    """django-ninja bearer auth that returns a ``ResolvedAuth`` principal."""

    def authenticate(self, request, token: str) -> Optional[ResolvedAuth]:
        return resolve_access_token(token)
