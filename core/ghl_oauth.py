"""GHL marketplace OAuth — install URL building, signed state tokens,
and authorization-code → access-token exchange.

The endpoints below moved to ``leadconnectorhq.com`` in 2025; the legacy
``marketplace.gohighlevel.com/oauth/...`` host returns "no integration found"
for newer client IDs. (Pattern from IRCHrocks25/Intelligent_Busines_Center.)

Why the state token is signed by US, not GHL:
    GHL's own ``state`` parameter has a short TTL (a few minutes) and lives
    in marketplace.leadconnectorhq.com cookies. If a user starts an install,
    uninstalls, then reinstalls, the marketplace cookies carry the original
    state and GHL rejects it as "Invalid state: Signature has expired".
    By passing OUR signed state and verifying it on the callback, we control
    the TTL and avoid that whole class of failure.
"""
import logging
import time
from typing import Any

import httpx
from django.conf import settings
from django.core import signing

logger = logging.getLogger(__name__)

# --- GHL endpoints --------------------------------------------------------- #
AUTH_BASE = "https://marketplace.leadconnectorhq.com/oauth/chooselocation"
TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
LOCATION_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/locationToken"
INSTALLED_LOCATIONS_URL = (
    "https://services.leadconnectorhq.com/oauth/installedLocations"
)
GHL_API_VERSION = "2021-07-28"

# State TTL — 30 minutes. Long enough that a user choosing a location at a
# leisurely pace doesn't time out; short enough that a leaked state link
# can't be replayed indefinitely.
STATE_TTL_SECONDS = 30 * 60

# Default OAuth scopes. Override per-call if a flow needs more.
# Keep this minimal — every scope you request must be valid on GHL's side AND
# enabled on your marketplace app. `users.readonly` is NOT a valid GHL scope
# name (the install fails with "Invalid scope(s)"). For a Custom Page that
# uses signed-blob SSO, no scope is strictly required; we keep
# `locations.readonly` so the integration can look up location metadata.
DEFAULT_SCOPES = [
    "locations.readonly",
]


class StateInvalid(Exception):
    """Signed state token failed verification or expired."""


class TokenExchangeFailed(Exception):
    """GHL's /oauth/token endpoint returned a non-2xx."""


def sign_state(payload: dict[str, Any]) -> str:
    """Produce an opaque, tamper-evident state token. Includes an issued-at
    timestamp so verify_state can enforce TTL independently of Django's
    own signer max_age (which is consulted but ours is the source of truth
    on the wire)."""
    body = {**payload, "iat": int(time.time())}
    return signing.dumps(body, salt="ghl-oauth-state")


def verify_state(token: str, max_age: int = STATE_TTL_SECONDS) -> dict[str, Any]:
    try:
        body = signing.loads(token, salt="ghl-oauth-state", max_age=max_age)
    except signing.BadSignature as exc:
        raise StateInvalid("signature invalid") from exc
    if not isinstance(body, dict):
        raise StateInvalid("payload not a dict")
    return body


def build_install_url(*, state: str, redirect_uri: str, scopes: list[str] | None = None) -> str:
    """Construct the URL to redirect a user to in order to install the app.

    GHL marketplace client IDs are formatted ``<app_id>-<version_suffix>``
    (e.g. ``6a26a22d7ea963384e3e358a-mq5jclqk``). The chooselocation URL
    requires ``version_id`` — omitting it fails with noAppVersionIdFound.
    Default to the prefix; override with GHL_APP_VERSION_ID if needed.
    """
    client_id = settings.GHL_CLIENT_ID
    if not client_id:
        raise RuntimeError("GHL_CLIENT_ID is not set.")
    app_version_id = getattr(settings, "GHL_APP_VERSION_ID", "") or client_id.split("-")[0]
    use_scopes = scopes or DEFAULT_SCOPES
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(use_scopes),
        "state": state,
        "version_id": app_version_id,
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code(*, code: str, redirect_uri: str, user_type: str = "Location") -> dict[str, Any]:
    """POST the auth code to GHL and get back an access token.

    user_type='Location' is a hint — GHL still returns userType='Company'
    when the installer is an agency owner OR the app's scope set includes
    agency-level scopes. Caller normalizes downstream."""
    client_id = settings.GHL_CLIENT_ID
    client_secret = settings.GHL_CLIENT_SECRET
    if not client_id or not client_secret:
        raise RuntimeError("GHL_CLIENT_ID and GHL_CLIENT_SECRET must be set.")
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "user_type": user_type,
    }
    try:
        resp = httpx.post(
            TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise TokenExchangeFailed(f"network error: {exc}") from exc
    if resp.status_code >= 400:
        raise TokenExchangeFailed(
            f"GHL /oauth/token {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


def mint_location_token(
    *,
    agency_access_token: str,
    company_id: str,
    location_id: str,
) -> dict[str, Any]:
    """When the install path returned a Company token (agency owner case),
    convert it into a Location-scoped token via /oauth/locationToken."""
    resp = httpx.post(
        LOCATION_TOKEN_URL,
        data={"companyId": company_id, "locationId": location_id},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {agency_access_token}",
            "Version": GHL_API_VERSION,
            "Accept": "application/json",
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise TokenExchangeFailed(
            f"GHL /oauth/locationToken {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


def list_installed_locations(
    *, agency_access_token: str, company_id: str, app_id: str
) -> list[dict]:
    """Return sub-accounts the app is installed on for this agency.

    Uses the agency (Company) access token. ``app_id`` is the client-id
    prefix (before the ``-``). Returns ``[{"id", "name"}, ...]``.
    """
    try:
        resp = httpx.get(
            INSTALLED_LOCATIONS_URL,
            params={"companyId": company_id, "appId": app_id},
            headers={
                "Authorization": f"Bearer {agency_access_token}",
                "Version": GHL_API_VERSION,
                "Accept": "application/json",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise TokenExchangeFailed(f"network error: {exc}") from exc
    if resp.status_code >= 400:
        raise TokenExchangeFailed(
            f"GHL /oauth/installedLocations {resp.status_code}: {resp.text[:200]}"
        )
    out: list[dict] = []
    for loc in (resp.json().get("locations") or []):
        loc_id = loc.get("_id") or loc.get("id")
        if not loc_id:
            continue
        out.append({"id": loc_id, "name": (loc.get("name") or "").strip()})
    return out


def refresh_access_token(*, refresh_token: str, user_type: str = "Location") -> dict[str, Any]:
    """Exchange a refresh token for a new access token. ``user_type`` is
    'Company' for agency tokens, 'Location' for sub-account tokens."""
    client_id = settings.GHL_CLIENT_ID
    client_secret = settings.GHL_CLIENT_SECRET
    if not client_id or not client_secret:
        raise RuntimeError("GHL_CLIENT_ID and GHL_CLIENT_SECRET must be set.")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "user_type": user_type,
    }
    try:
        resp = httpx.post(
            TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise TokenExchangeFailed(f"network error: {exc}") from exc
    if resp.status_code >= 400:
        raise TokenExchangeFailed(
            f"GHL /oauth/token refresh {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()
