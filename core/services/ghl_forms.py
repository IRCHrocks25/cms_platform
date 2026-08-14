"""Tenant-scoped access to existing GoHighLevel forms."""

from __future__ import annotations

from dataclasses import dataclass

from core import ghl_oauth
from core.models import GhlInstall, Tenant
from core.services.ghl_connect import ensure_fresh_location_token


@dataclass
class GhlFormsUnavailable(Exception):
    code: str
    public_message: str

    def __str__(self) -> str:
        return self.public_message


def _unavailable(code: str, message: str) -> GhlFormsUnavailable:
    return GhlFormsUnavailable(code=code, public_message=message)


def _connected_install(tenant: Tenant) -> GhlInstall:
    location_id = (tenant.ghl_location_id or "").strip()
    if not location_id:
        raise _unavailable(
            "not_connected",
            "Connect this site to its GoHighLevel location to list forms.",
        )

    install = GhlInstall.objects.filter(
        tenant=tenant,
        location_id=location_id,
    ).first()
    if install is None:
        raise _unavailable(
            "not_connected",
            "Connect this site to its GoHighLevel location to list forms.",
        )
    if install.status != GhlInstall.STATUS_CONNECTED:
        raise _unavailable(
            "reconnect_required",
            "Reconnect this site's GoHighLevel integration to list forms.",
        )
    if "forms.readonly" not in (install.scopes or []):
        raise _unavailable(
            "reconsent_required",
            "Reconnect GoHighLevel and approve the Forms permission to list forms.",
        )
    return install


def _expire(install: GhlInstall) -> None:
    if install.status == GhlInstall.STATUS_EXPIRED:
        return
    install.status = GhlInstall.STATUS_EXPIRED
    install.save(update_fields=["status", "updated_at"])


def list_forms_for_tenant(tenant: Tenant) -> list[dict[str, str]]:
    """Return forms only from ``tenant``'s bound, connected GHL location."""
    install = _connected_install(tenant)
    try:
        access_token = ensure_fresh_location_token(install)
        return ghl_oauth.list_forms(
            access_token=access_token,
            location_id=install.location_id,
        )
    except (
        ghl_oauth.GhlFormsAuthorizationFailed,
        ghl_oauth.TokenExchangeFailed,
    ) as exc:
        _expire(install)
        raise _unavailable(
            "reconnect_required",
            "Reconnect this site's GoHighLevel integration to list forms.",
        ) from exc
    except ghl_oauth.GhlFormsRequestFailed as exc:
        raise _unavailable(
            "temporarily_unavailable",
            "GoHighLevel forms could not be loaded. Try again in a moment.",
        ) from exc
