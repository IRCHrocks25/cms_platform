"""CMS-constrained Dynamic Client Registration (RFC 7591) on top of DOT 3.4.0.

django-oauth-toolkit already ships ``DynamicClientRegistrationView``. This
module wires it at ``/oauth/register`` (IBC-compatible path), opens it for
unauthenticated MCP clients, and tightens what metadata we accept so a
registrant cannot opt into grants/auth methods CMS-20 disabled.
"""

from __future__ import annotations

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
_DCR_RATE_LIMIT = 10
_DCR_RATE_WINDOW = 60 * 60


def _dcr_rate_limited(request) -> bool:
    ip = request.META.get("REMOTE_ADDR", "") or "unknown"
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
