import logging
from http import cookies as http_cookies

from django.conf import settings

from .models import CustomDomain, Tenant

logger = logging.getLogger(__name__)


# Teach Python's stdlib SimpleCookie about the CHIPS "Partitioned" attribute
# so Django's response.cookies can serialize it. Without this, setting
# morsel["partitioned"] silently strips it from the Set-Cookie header.
http_cookies.Morsel._reserved.setdefault("partitioned", "Partitioned")
if hasattr(http_cookies.Morsel, "_flags"):
    http_cookies.Morsel._flags.add("partitioned")


class DiagnosticHeaderMiddleware:
    """Emits the runtime values of DEBUG / IFRAME_EMBED / cookie flags so
    a curl from outside can verify the deployed config without log access."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["X-Diag-Debug"] = str(getattr(settings, "DEBUG", "?"))
        response["X-Diag-Iframe-Embed"] = str(getattr(settings, "IFRAME_EMBED", "?"))
        response["X-Diag-Csrf-Samesite"] = str(getattr(settings, "CSRF_COOKIE_SAMESITE", "?"))
        response["X-Diag-Csrf-Secure"] = str(getattr(settings, "CSRF_COOKIE_SECURE", "?"))
        response["X-Diag-Ghl-Auto-Login"] = str(getattr(settings, "GHL_AUTO_LOGIN", "?"))
        return response


class PartitionedCookieMiddleware:
    """Adds the ``Partitioned`` attribute (CHIPS) to the session and CSRF
    cookies when iframe embedding is enabled. Without this, modern Chrome
    blocks even SameSite=None; Secure cookies inside cross-site iframes as
    part of its tracking-protection rollout, which kills login flows in a
    GHL Custom Page.

    With Partitioned, the cookie is stored keyed on the *top-level* site
    (e.g. app.daltoleadsystem.com), allowed in that iframe context, and
    isolated from other contexts — privacy-preserving and unblocking.

    Only applied when IFRAME_EMBED is on so dev cookies stay un-partitioned.

    ORDERING IS LOAD-BEARING. This middleware MUST be the outermost entry
    in MIDDLEWARE (first in the list) so its response phase runs LAST,
    after Session/Csrf have written their cookies in their own
    ``process_response``. If you put it below those two, its response phase
    sees an empty ``response.cookies`` for sessionid/csrftoken and the
    Partitioned attribute silently never gets attached — there is no error,
    just a Set-Cookie header that lacks Partitioned. We belt-and-brace this
    with a startup warning below.
    """

    _COOKIE_NAMES = ("sessionid", "csrftoken")

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = bool(getattr(settings, "IFRAME_EMBED", False))
        self._warn_if_misordered()

    def _warn_if_misordered(self) -> None:
        if not self.enabled:
            return
        mw = list(getattr(settings, "MIDDLEWARE", []))
        self_name = f"{__name__}.{type(self).__name__}"
        try:
            self_idx = mw.index(self_name)
        except ValueError:
            return
        risky_below = (
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
        )
        for offender in risky_below:
            if offender in mw and mw.index(offender) < self_idx:
                logger.warning(
                    "PartitionedCookieMiddleware is positioned BELOW %s in "
                    "MIDDLEWARE — its response phase will run before %s "
                    "writes the cookie, so Partitioned will never appear "
                    "on Set-Cookie. Move PartitionedCookieMiddleware to "
                    "the top of MIDDLEWARE.", offender, offender,
                )

    def __call__(self, request):
        response = self.get_response(request)
        if not self.enabled:
            return response
        for name in self._COOKIE_NAMES:
            morsel = response.cookies.get(name)
            if morsel is not None:
                morsel["partitioned"] = True
        return response


class FrameAncestorsCspMiddleware:
    """Emits ``Content-Security-Policy: frame-ancestors ...`` so this app can
    be embedded inside GHL (and any agency whitelabel domain) as a Custom
    Page. Browsers prefer this directive over X-Frame-Options when both are
    set, so XFrameOptionsMiddleware can stay in place.

    The list is configured via the ``GHL_FRAME_ANCESTORS`` env var
    (comma-separated origins). Pass ``*`` to allow any parent — appropriate
    once signed-context SSO is in place; risky while we still trust URL
    params on /embed/."""

    def __init__(self, get_response):
        self.get_response = get_response
        raw = (getattr(settings, "GHL_FRAME_ANCESTORS", "") or "").strip()
        if raw == "*":
            sources = "*"
        else:
            entries = [e.strip() for e in raw.split(",") if e.strip()]
            sources = " ".join(["'self'", *entries]) if entries else "'self'"
        self._header_value = f"frame-ancestors {sources};"

    def __call__(self, request):
        response = self.get_response(request)
        # Don't clobber an existing CSP (e.g. set by Cloudflare or a view).
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self._header_value
        return response


def _x_forwarded_host_first(request, fallback_host: str) -> str:
    """
    First host in ``X-Forwarded-Host`` (proxy chains may send a list).
    Used when an edge proxy rewrites ``Host`` (e.g. Cloudflare → Railway)
    but preserves the original domain here. Falls back to ``fallback_host``.
    """
    forwarded = (request.META.get("HTTP_X_FORWARDED_HOST") or "").split(",")[0].strip().lower()
    if not forwarded:
        return fallback_host
    return forwarded.split(":")[0].rstrip(".")


class TenantResolverMiddleware:
    """
    Resolves a tenant from the host's leftmost subdomain when the host is
    a tenant host (`<sub>.<TENANT_BASE_DOMAIN>`). Falls back to looking up
    a verified ``CustomDomain`` when the host doesn't match a subdomain
    pattern; that lookup uses ``X-Forwarded-Host`` (first value) when set,
    so a proxy can rewrite ``Host`` while preserving the client domain.
    The resolved tenant is attached to ``request.tenant``. If the host is
    the bare base domain or a reserved subdomain, ``request.tenant`` is
    ``None`` and the custom-domain fallback is skipped.
    """

    APP_HOSTS = {"127.0.0.1", "0.0.0.0"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = self._resolve_tenant(request)
        return self.get_response(request)

    def _resolve_tenant(self, request):
        http_host = request.META.get("HTTP_HOST")
        http_x_original_host = request.META.get("HTTP_X_ORIGINAL_HOST")
        host = request.get_host().split(":")[0].lower().rstrip(".")
        lookup_host = _x_forwarded_host_first(request, host)
        logger.debug(
            "TenantResolverMiddleware: HTTP_HOST=%r HTTP_X_ORIGINAL_HOST=%r host=%r lookup_host=%r",
            http_host,
            http_x_original_host,
            host,
            lookup_host,
        )

        # Check for Cloudflare Worker forwarded host
        x_original_host = (
            request.META.get("HTTP_X_ORIGINAL_HOST", "")
            .split(",")[0]
            .strip()
            .lower()
            .rstrip(".")
        )
        logger.debug(f"X-Original-Host: {x_original_host}, host: {host}")
        if x_original_host:
            custom = (
                CustomDomain.objects.select_related("tenant__template")
                .filter(domain=x_original_host, is_verified=True)
                .first()
            )
            if custom:
                return custom.tenant

        if not host or host in self.APP_HOSTS:
            return None

        reserved = set(getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()))

        # Candidate base domains the host may sit under: the configured base,
        # any additional configured bases, plus the local-dev wildcard bases
        # (localhost / lvh.me) — but the dev bases only in DEBUG, so production
        # behavior is unchanged.
        bases = [
            b for b in (
                (settings.TENANT_BASE_DOMAIN or "").lower().rstrip("."),
                *[
                    (d or "").lower().rstrip(".")
                    for d in getattr(settings, "TENANT_ADDITIONAL_BASE_DOMAINS", [])
                ],
            ) if b
        ]
        if settings.DEBUG:
            for dev_base in ("localhost", (getattr(settings, "TENANT_DEV_BASE_DOMAIN", "") or "").lower().rstrip(".")):
                if dev_base and dev_base not in bases:
                    bases.append(dev_base)

        # Bare base domain (e.g. `localhost`, `yourdomain.com`) — agency host.
        if host in bases:
            return None

        # Subdomain pattern: host is `<sub>.<base>` for one of the bases.
        for base in bases:
            if not host.endswith("." + base):
                continue
            sub_part = host[: -(len(base) + 1)]
            if sub_part and "." not in sub_part:
                # Reserved subdomain — never fall through to custom-domain lookup.
                if sub_part in reserved:
                    return None
                tenant = (
                    Tenant.objects.select_related("template")
                    .filter(subdomain=sub_part)
                    .first()
                )
                if tenant:
                    return tenant
            break

        # Fallback: verified custom domain (e.g. `training.acme.com`).
        custom = (
            CustomDomain.objects.select_related("tenant__template")
            .filter(domain=lookup_host, is_verified=True)
            .first()
        )
        if custom:
            return custom.tenant

        return None
