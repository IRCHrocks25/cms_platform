from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


_REQUIRED = (
    "KATEK_OIDC_ISSUER",
    "KATEK_OIDC_CLIENT_ID",
    "KATEK_OIDC_CLIENT_SECRET",
    "KATEK_OIDC_AUTHORIZATION_ENDPOINT",
    "KATEK_OIDC_TOKEN_ENDPOINT",
    "KATEK_OIDC_USERINFO_ENDPOINT",
    "KATEK_OIDC_JWKS_ENDPOINT",
    "KATEK_TENANT_REGISTRY_URL",
    "KATEK_TENANT_REGISTRY_TOKEN",
)

_HTTPS_URLS = (
    "KATEK_OIDC_ISSUER",
    "KATEK_OIDC_AUTHORIZATION_ENDPOINT",
    "KATEK_OIDC_TOKEN_ENDPOINT",
    "KATEK_OIDC_USERINFO_ENDPOINT",
    "KATEK_OIDC_JWKS_ENDPOINT",
    "KATEK_TENANT_REGISTRY_URL",
)


@dataclass(frozen=True)
class CentralIdentityConfig:
    enabled: bool
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    jwks_endpoint: str = ""
    registry_url: str = ""
    registry_token: str = ""
    signing_algorithm: str = "RS256"
    use_nonce: bool = True
    pkce_method: str = "S256"
    entitlement_cache_seconds: int = 300
    request_timeout_seconds: float = 5.0


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _value(values: Mapping[str, str], name: str) -> str:
    return (values.get(name) or "").strip()


def load_central_identity_config(values: Mapping[str, str]) -> CentralIdentityConfig:
    if not _enabled(values.get("KATEK_OIDC_ENABLED")):
        return CentralIdentityConfig(enabled=False)

    missing = [name for name in _REQUIRED if not _value(values, name)]
    if missing:
        raise ImproperlyConfigured(
            "Katek OIDC is enabled but required settings are missing: "
            + ", ".join(missing)
        )

    for name in _HTTPS_URLS:
        parsed = urlparse(_value(values, name))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ImproperlyConfigured(f"{name} must be an absolute HTTPS URL")

    issuer = _value(values, "KATEK_OIDC_ISSUER")
    return CentralIdentityConfig(
        enabled=True,
        issuer=issuer,
        client_id=_value(values, "KATEK_OIDC_CLIENT_ID"),
        client_secret=_value(values, "KATEK_OIDC_CLIENT_SECRET"),
        authorization_endpoint=_value(
            values, "KATEK_OIDC_AUTHORIZATION_ENDPOINT"
        ),
        token_endpoint=_value(values, "KATEK_OIDC_TOKEN_ENDPOINT"),
        userinfo_endpoint=_value(values, "KATEK_OIDC_USERINFO_ENDPOINT"),
        jwks_endpoint=_value(values, "KATEK_OIDC_JWKS_ENDPOINT"),
        registry_url=_value(values, "KATEK_TENANT_REGISTRY_URL").rstrip("/"),
        registry_token=_value(values, "KATEK_TENANT_REGISTRY_TOKEN"),
        request_timeout_seconds=float(
            values.get("KATEK_OIDC_REQUEST_TIMEOUT_SECONDS", "5") or "5"
        ),
    )
