"""CMS-constrained Dynamic Client Registration (RFC 7591) on top of DOT 3.4.0.

django-oauth-toolkit already ships ``DynamicClientRegistrationView``. This
module wires it at ``/oauth/register`` (IBC-compatible path), opens it for
unauthenticated MCP clients, and tightens what metadata we accept so a
registrant cannot opt into grants/auth methods CMS-20 disabled.
"""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.compat import login_not_required
from oauth2_provider.models import get_application_model
from oauth2_provider.settings import oauth2_settings
from oauth2_provider.views.dynamic_client_registration import (
    DynamicClientRegistrationManagementView,
    DynamicClientRegistrationView,
    _application_to_response,
    _build_application_kwargs,
    _check_permissions,
    _error_response,
    _issue_registration_token,
    _parse_metadata,
    _validation_error_description,
)


# Unauthenticated writes → cheap flood target. Cache-backed, best-effort.
# Per real client IP (not the Traefik hop): 10/hour is generous for MCP
# connectors (one registration) and still blocks floods once the key is correct.
_DCR_RATE_LIMIT = 10
_DCR_RATE_WINDOW = 60 * 60

# Same posture as SECURE_PROXY_SSL_HEADER / USE_X_FORWARDED_HOST: the app is
# only reachable via Traefik on the docker network (see deploy/DOKPLOY.md), so
# private/loopback REMOTE_ADDR means the peer is our edge proxy and we may
# read CF-Connecting-IP / X-Forwarded-For. A public REMOTE_ADDR is not that
# peer — treat client-IP headers as spoofable and ignore them.
_DEFAULT_TRUSTED_PROXY_NETWORKS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)


def _trusted_proxy_networks():
    raw = getattr(settings, "DCR_TRUSTED_PROXY_IPS", None)
    if raw is None:
        raw = _DEFAULT_TRUSTED_PROXY_NETWORKS
    return tuple(ipaddress.ip_network(n, strict=False) for n in raw)


def _parse_ip(value: str):
    value = (value or "").strip()
    if not value:
        return None
    # XFF / REMOTE_ADDR may be "ip:port" (IPv4) — strip a trailing :port.
    if value.count(":") == 1 and "." in value:
        value = value.rsplit(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _remote_addr_is_trusted_proxy(remote: str) -> bool:
    addr = _parse_ip(remote)
    if addr is None:
        return False
    return any(addr in net for net in _trusted_proxy_networks())


def _client_ip(request) -> str:
    """Real client IP for DCR rate limiting.

    Prefer ``CF-Connecting-IP``, else the left-most ``X-Forwarded-For`` entry,
    else ``REMOTE_ADDR`` — but only when ``REMOTE_ADDR`` is a known proxy
    (private/loopback by default). Matches the project's existing proxy-header
    trust (``SECURE_PROXY_SSL_HEADER``, ``USE_X_FORWARDED_HOST``): those headers
    are meaningful only because Traefik is the sole public ingress.
    """
    remote = (request.META.get("REMOTE_ADDR") or "").strip() or "unknown"
    if not _remote_addr_is_trusted_proxy(remote):
        return remote

    cf = (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip()
    if cf:
        parsed = _parse_ip(cf)
        if parsed is not None:
            return str(parsed)

    xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if xff:
        leftmost = xff.split(",", 1)[0].strip()
        parsed = _parse_ip(leftmost)
        if parsed is not None:
            return str(parsed)

    return remote


def _dcr_rate_limited(request) -> bool:
    ip = _client_ip(request)
    key = f"oauth-dcr:ip:{ip}"
    try:
        count = cache.get(key, 0)
        if count >= _DCR_RATE_LIMIT:
            return True
        cache.set(key, count + 1, _DCR_RATE_WINDOW)
    except Exception:
        # Never let a cache hiccup block a legitimate registration.
        return False
    return False


def _cms_validate_metadata(data: dict):
    """Reject grants/response types/auth methods outside the CMS-20 surface."""
    grant_types = data.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or not all(isinstance(g, str) for g in grant_types):
        return _error_response("invalid_client_metadata", "grant_types must be an array of strings")

    allowed_grants = set(oauth2_settings.OAUTH2_GRANT_TYPES_SUPPORTED)
    # refresh_token is advertised alongside authorization_code but is not a
    # standalone DOT Application grant.
    for grant in grant_types:
        if grant == "refresh_token":
            continue
        if grant not in allowed_grants:
            return _error_response(
                "invalid_client_metadata",
                f"Unsupported grant_type: {grant!r}. "
                f"Supported values: {', '.join(sorted(allowed_grants))}",
            )

    meaningful = [g for g in grant_types if g != "refresh_token"]
    if not meaningful:
        return _error_response(
            "invalid_client_metadata",
            "grant_types must contain authorization_code",
        )
    if meaningful != ["authorization_code"]:
        return _error_response(
            "invalid_client_metadata",
            "Only authorization_code (+ optional refresh_token) may be registered",
        )

    if "response_types" in data:
        response_types = data["response_types"]
        if not isinstance(response_types, list) or not all(
            isinstance(r, str) for r in response_types
        ):
            return _error_response(
                "invalid_client_metadata",
                "response_types must be an array of strings",
            )
        allowed_responses = list(oauth2_settings.OAUTH2_RESPONSE_TYPES_SUPPORTED)
        if response_types != allowed_responses:
            return _error_response(
                "invalid_client_metadata",
                f"Unsupported response_types: {response_types!r}. "
                f"Supported values: {allowed_responses!r}",
            )

    auth_method = data.get("token_endpoint_auth_method", "none")
    if auth_method != "none":
        return _error_response(
            "invalid_client_metadata",
            "Only token_endpoint_auth_method='none' (public clients) is allowed "
            "for dynamic registration",
        )

    return None


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_not_required, name="dispatch")
class CmsDynamicClientRegistrationView(DynamicClientRegistrationView):
    """DOT's RFC 7591 POST, with CMS grant/auth constraints and rate limiting."""

    def post(self, request, *args, **kwargs):
        if _dcr_rate_limited(request):
            return _error_response(
                "temporarily_unavailable",
                "Registration rate limit exceeded; try again later",
                status=429,
            )

        if not _check_permissions(request):
            return _error_response(
                "access_denied",
                "Authentication required to register a client",
                status=401,
            )

        data, err = _parse_metadata(request.body)
        if err:
            return err

        # Public-client default (DOT's own default is confidential).
        data.setdefault("token_endpoint_auth_method", "none")

        err = _cms_validate_metadata(data)
        if err:
            return err

        app_kwargs, err = _build_application_kwargs(data)
        if err:
            return err

        Application = get_application_model()
        user = request.user if request.user.is_authenticated else None
        application = Application(
            user=user,
            registration_source=Application.RegistrationSource.DCR,
            **app_kwargs,
        )

        try:
            application.full_clean()
        except ValidationError as exc:
            return _error_response(
                "invalid_client_metadata",
                _validation_error_description(exc),
            )

        with transaction.atomic():
            application.save()
            registration_token = _issue_registration_token(application, user)

        response_data = _application_to_response(application, registration_token, request)
        response_data["response_types"] = list(
            oauth2_settings.OAUTH2_RESPONSE_TYPES_SUPPORTED
        )
        # Never return a client_secret for DCR — public clients only.
        response_data.pop("client_secret", None)
        return JsonResponse(response_data, status=201)


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(login_not_required, name="dispatch")
class CmsDynamicClientRegistrationManagementView(DynamicClientRegistrationManagementView):
    """RFC 7592 management endpoint; same CMS constraints on PUT."""

    def put(self, request, client_id, *args, **kwargs):
        application, result = self._get_application_from_registration_token(
            request, client_id
        )
        if application is None:
            return result

        registration_token = result
        data, err = _parse_metadata(request.body)
        if err:
            return err

        data.setdefault("token_endpoint_auth_method", "none")
        err = _cms_validate_metadata(data)
        if err:
            return err

        app_kwargs, err = _build_application_kwargs(data)
        if err:
            return err

        for field, value in app_kwargs.items():
            setattr(application, field, value)

        try:
            application.full_clean()
        except ValidationError as exc:
            return _error_response(
                "invalid_client_metadata",
                _validation_error_description(exc),
            )

        with transaction.atomic():
            application.save()
            if oauth2_settings.DCR_ROTATE_REGISTRATION_TOKEN_ON_UPDATE:
                new_token = _issue_registration_token(application, application.user)
                registration_token.delete()
                registration_token = new_token

        response_data = _application_to_response(
            application, registration_token, request
        )
        response_data["response_types"] = list(
            oauth2_settings.OAUTH2_RESPONSE_TYPES_SUPPORTED
        )
        response_data.pop("client_secret", None)
        return JsonResponse(response_data)
