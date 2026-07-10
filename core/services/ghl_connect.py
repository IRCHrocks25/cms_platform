"""Model-touching GHL orchestration: keep agency tokens fresh, mint and
persist per-location installs, and re-bind on demand. HTTP lives in
core.ghl_oauth; crypto in core.ghl_crypto."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core import ghl_oauth
from core.ghl_crypto import decrypt_token, encrypt_token
from core.models import GhlAgencyInstall, GhlInstall, Tenant

REFRESH_MARGIN = timedelta(seconds=60)


def _expires_at(expires_in):
    if not expires_in:
        return None
    try:
        return timezone.now() + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def ensure_fresh_agency_token(agency: GhlAgencyInstall) -> str:
    """Return a valid agency access token, refreshing if within the margin."""
    if agency.expires_at and agency.expires_at - timezone.now() > REFRESH_MARGIN:
        return decrypt_token(agency.access_token)
    data = ghl_oauth.refresh_access_token(
        refresh_token=decrypt_token(agency.refresh_token), user_type="Company"
    )
    agency.access_token = encrypt_token(data.get("access_token", ""))
    if data.get("refresh_token"):
        agency.refresh_token = encrypt_token(data["refresh_token"])
    agency.expires_at = _expires_at(data.get("expires_in"))
    agency.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return data.get("access_token", "")


def bind_location(*, agency: GhlAgencyInstall, location_id: str, tenant: Tenant) -> GhlInstall:
    """Mint a location token from the agency token and persist a connected
    GhlInstall linked to ``tenant``. Also sets Tenant.ghl_location_id so the
    embed/SSO path lights up."""
    access = ensure_fresh_agency_token(agency)
    data = ghl_oauth.mint_location_token(
        agency_access_token=access, company_id=agency.company_id, location_id=location_id
    )
    name = next(
        (l.get("name", "") for l in agency.available_locations if l.get("id") == location_id),
        "",
    )
    scope = data.get("scope", "")
    install, _ = GhlInstall.objects.update_or_create(
        location_id=location_id,
        defaults={
            "agency": agency,
            "company_id": agency.company_id,
            "user_type": GhlInstall.USER_TYPE_LOCATION,
            "access_token": encrypt_token(data.get("access_token", "")),
            "refresh_token": encrypt_token(data.get("refresh_token", "")),
            "expires_at": _expires_at(data.get("expires_in")),
            "scopes": scope.split() if scope else [],
            "location_name": name,
            "status": GhlInstall.STATUS_CONNECTED,
            "tenant": tenant,
        },
    )
    if tenant.ghl_location_id != location_id:
        tenant.ghl_location_id = location_id
        tenant.save(update_fields=["ghl_location_id", "updated_at"])
    return install


def reconnect_install(install: GhlInstall) -> GhlInstall:
    """Re-mint a location token for an existing install from its agency."""
    if install.agency is None or install.tenant is None:
        raise ValueError("Install has no agency/tenant to reconnect.")
    return bind_location(
        agency=install.agency, location_id=install.location_id, tenant=install.tenant
    )
