import logging

from django.conf import settings

from .models import CustomDomain, Tenant

logger = logging.getLogger(__name__)


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
