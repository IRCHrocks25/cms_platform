from django.conf import settings

from .models import CustomDomain, Tenant


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


class AllowedHostsFromCustomDomains:
    """
    Expand ``settings.ALLOWED_HOSTS`` on the fly when the incoming
    ``Host`` or ``X-Forwarded-Host`` (first value) matches a verified
    ``CustomDomain``. Must run first in ``MIDDLEWARE`` — Django's
    ``request.get_host()`` validates against ``ALLOWED_HOSTS`` and would
    raise ``DisallowedHost`` in production before we could amend the list,
    so we read ``HTTP_HOST`` straight from ``META`` here.

    A DB lookup runs only on the first request per host per worker
    process; once a host is in ``ALLOWED_HOSTS`` subsequent requests
    skip the query.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME") or ""
        host = raw.split(":")[0].lower().strip().rstrip(".")
        lookup_host = _x_forwarded_host_first(request, host)
        candidates = []
        if host:
            candidates.append(host)
        if lookup_host and lookup_host not in candidates:
            candidates.append(lookup_host)
        for candidate in candidates:
            if candidate not in settings.ALLOWED_HOSTS:
                if CustomDomain.objects.filter(domain=candidate, is_verified=True).exists():
                    settings.ALLOWED_HOSTS.append(candidate)
        return self.get_response(request)


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
        host = request.get_host().split(":")[0].lower().rstrip(".")
        if not host or host in self.APP_HOSTS:
            return None

        base = (settings.TENANT_BASE_DOMAIN or "").lower().rstrip(".")
        reserved = set(getattr(settings, "TENANT_RESERVED_SUBDOMAINS", set()))

        # Bare base domain (e.g. `localhost`, `yourdomain.com`) — agency host.
        if host == base:
            return None

        # Subdomain pattern: host is `<sub>.<base>`.
        if base and host.endswith("." + base):
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

        # Fallback: verified custom domain (e.g. `training.acme.com`).
        # Prefer ``X-Forwarded-Host`` when present (proxy rewrote ``Host``).
        lookup_host = _x_forwarded_host_first(request, host)
        custom = (
            CustomDomain.objects.select_related("tenant__template")
            .filter(domain=lookup_host, is_verified=True)
            .first()
        )
        if custom:
            return custom.tenant

        return None
