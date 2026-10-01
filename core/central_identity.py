import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
import jwt
from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.db import IntegrityError, transaction
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.models import CentralIdentityLink, Tenant, VerifiedUserEmail


logger = logging.getLogger(__name__)


class RegistryDenied(Exception):
    """The central registry did not prove an active CMS binding."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EntitlementDecision:
    global_tenant_id: UUID
    local_tenant_id: str
    revision: str
    updated_at: str


class TenantRegistryClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def require_active(
        self, global_tenant_id: UUID, local_tenant_id: str
    ) -> EntitlementDecision:
        path = (
            f"/v1/businesses/{global_tenant_id}/"
            "product-entitlements/cms"
        )
        started = time.monotonic()
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.get(
                    path,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            logger.error(
                "tenant_registry_denied product=cms business=%s code=unavailable latency_ms=%d",
                global_tenant_id,
                int((time.monotonic() - started) * 1000),
            )
            raise RegistryDenied("registry_unavailable") from exc

        if response.status_code != 200:
            code = "not_entitled" if response.status_code == 404 else "registry_rejected"
            logger.warning(
                "tenant_registry_denied product=cms business=%s status=%d code=%s latency_ms=%d",
                global_tenant_id,
                response.status_code,
                code,
                int((time.monotonic() - started) * 1000),
            )
            raise RegistryDenied(code)

        try:
            body = response.json()
            response_global_id = UUID(str(body["globalTenantId"]))
            product = body["product"]
            status = body["status"]
            response_local_id = str(body["localTenantId"])
            revision = str(body["revision"])
            updated_at = str(body["updatedAt"])
            datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryDenied("invalid_registry_response") from exc

        if (
            response_global_id != global_tenant_id
            or product != "cms"
            or status != "active"
            or response_local_id != str(local_tenant_id)
            or not revision
        ):
            raise RegistryDenied("binding_mismatch_or_inactive")

        return EntitlementDecision(
            global_tenant_id=response_global_id,
            local_tenant_id=response_local_id,
            revision=revision,
            updated_at=updated_at,
        )


def tenant_registry_client() -> TenantRegistryClient:
    return TenantRegistryClient(
        base_url=settings.KATEK_TENANT_REGISTRY_URL,
        token=settings.KATEK_TENANT_REGISTRY_TOKEN,
        timeout_seconds=settings.KATEK_OIDC_REQUEST_TIMEOUT_SECONDS,
    )


def _normalized_email(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _eligible_tenants(request, user) -> list[Tenant]:
    requested_tenant = getattr(request, "tenant", None)
    if requested_tenant is not None:
        if not requested_tenant.memberships.filter(user=user).exists():
            raise RegistryDenied("local_membership_denied")
        return [requested_tenant]

    # Customer identity is never a staff bypass. An agency-host login is used
    # by External MCP consent, so every customer site exposed to that session
    # must have a current registry binding.
    tenants = list(
        Tenant.objects.filter(memberships__user=user)
        .distinct()
        .order_by("pk")
    )
    if not tenants:
        raise RegistryDenied("local_membership_denied")
    return tenants


def admit_central_user(request, user) -> list[dict[str, str]]:
    registry = tenant_registry_client()
    bindings: list[dict[str, str]] = []
    for tenant in _eligible_tenants(request, user):
        if tenant.global_tenant_id is None:
            raise RegistryDenied("missing_global_tenant_binding")
        decision = registry.require_active(tenant.global_tenant_id, str(tenant.pk))
        bindings.append(
            {
                "tenant_id": str(tenant.pk),
                "global_tenant_id": str(tenant.global_tenant_id),
                "revision": decision.revision,
            }
        )
    return bindings


class KatekOIDCBackend(OIDCAuthenticationBackend):
    """OIDC protocol adapter with CMS-owned mapping and linking policy."""

    def verify_token(self, token, **kwargs):
        try:
            payload = super().verify_token(token, **kwargs)
        except jwt.PyJWTError as exc:
            raise SuspiciousOperation("OIDC token validation failed") from exc
        expected_issuer = settings.KATEK_OIDC_ISSUER
        if payload.get("iss") != expected_issuer:
            raise SuspiciousOperation("OIDC issuer verification failed")

        audience = payload.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if settings.OIDC_RP_CLIENT_ID not in audiences:
            raise SuspiciousOperation("OIDC audience verification failed")
        authorized_party = payload.get("azp")
        if (len(audiences) > 1 or authorized_party is not None) and (
            authorized_party != settings.OIDC_RP_CLIENT_ID
        ):
            raise SuspiciousOperation("OIDC authorized-party verification failed")
        if not payload.get("sub"):
            raise SuspiciousOperation("OIDC subject is required")
        if not isinstance(payload.get("exp"), (int, float)):
            raise SuspiciousOperation("OIDC expiry is required")
        return payload

    def verify_claims(self, claims):
        return bool(
            claims.get("sub")
            and _normalized_email(claims.get("email"))
            and claims.get("email_verified") is True
        )

    def get_or_create_user(self, access_token, id_token, payload):
        userinfo = self.get_userinfo(access_token, id_token, payload)
        if userinfo.get("sub") != payload.get("sub"):
            raise SuspiciousOperation("OIDC userinfo subject mismatch")
        claims = {**userinfo, **payload}
        if not self.verify_claims(claims):
            raise SuspiciousOperation("OIDC verified email and subject are required")

        state = self.request.GET.get("state", "")
        transactions = self.request.session.get("katek_oidc_transactions", {})
        pending = transactions.pop(state, None)
        self.request.session["katek_oidc_transactions"] = transactions
        self.request.session.modified = True
        if not pending:
            raise SuspiciousOperation("Central identity transaction is missing")
        if pending.get("host") != self.request.get_host().lower():
            raise SuspiciousOperation("Central identity transaction host mismatch")
        if time.time() - float(pending.get("created_at", 0)) > 10 * 60:
            raise SuspiciousOperation("Central identity transaction expired")

        issuer = str(claims["iss"])
        subject = str(claims["sub"])
        if pending.get("mode") == "link":
            user = self._link_user(pending, claims, issuer, subject)
        else:
            try:
                user = CentralIdentityLink.objects.select_related("user").get(
                    issuer=issuer, subject=subject
                ).user
            except CentralIdentityLink.DoesNotExist as exc:
                # Email is never a sign-in lookup. Unknown subjects must use a
                # recent authenticated link flow.
                raise SuspiciousOperation("Central identity is not linked") from exc

        if not user.is_active:
            raise SuspiciousOperation("Linked local account is inactive")
        try:
            bindings = admit_central_user(self.request, user)
        except RegistryDenied as exc:
            raise SuspiciousOperation("Central identity entitlement denied") from exc

        self.request.session["katek_central_identity"] = {
            "user_id": str(user.pk),
            "issuer": issuer,
            "subject": subject,
            "bindings": bindings,
            "checked_at": time.time(),
        }
        return user

    def _link_user(self, pending, claims, issuer: str, subject: str):
        request_user = getattr(self.request, "user", None)
        if not request_user or not request_user.is_authenticated:
            raise SuspiciousOperation("A live legacy session is required for linking")
        if str(request_user.pk) != str(pending.get("user_id")):
            raise SuspiciousOperation("Link transaction user mismatch")

        legacy_at = self.request.session.get("legacy_authenticated_at")
        legacy_user_id = self.request.session.get("legacy_authenticated_user_id")
        now = time.time()
        if (
            str(legacy_user_id) != str(request_user.pk)
            or not isinstance(legacy_at, (int, float))
            or now - legacy_at > 15 * 60
        ):
            raise SuspiciousOperation("A fresh legacy login is required for linking")

        auth_time = claims.get("auth_time")
        if (
            not isinstance(auth_time, (int, float))
            or auth_time > now + 60
            or now - auth_time > 5 * 60
        ):
            raise SuspiciousOperation("A fresh identity-provider login is required")

        email = _normalized_email(claims.get("email"))
        try:
            proof = VerifiedUserEmail.objects.get(user=request_user)
        except VerifiedUserEmail.DoesNotExist as exc:
            raise SuspiciousOperation("Local email is not verified") from exc
        if (
            proof.normalized_email != email
            or _normalized_email(request_user.email) != email
            or pending.get("email") != email
        ):
            raise SuspiciousOperation("Verified email does not match")

        try:
            with transaction.atomic():
                link, created = CentralIdentityLink.objects.get_or_create(
                    issuer=issuer,
                    subject=subject,
                    defaults={"user": request_user, "email_at_link": email},
                )
                if not created and link.user_id != request_user.pk:
                    raise SuspiciousOperation("Central identity belongs to another user")
        except IntegrityError as exc:
            raise SuspiciousOperation("Central identity link conflicts") from exc
        return request_user
