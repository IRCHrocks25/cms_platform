from django.conf import settings

from .models import CustomDomain, Tenant


class TenantResolverMiddleware:
    """
    Resolves a tenant from the host's leftmost subdomain when the host is
    a tenant host (`<sub>.<TENANT_BASE_DOMAIN>`). Falls back to looking up
    a verified ``CustomDomain`` row when the host doesn't match a
    subdomain pattern. The resolved tenant is attached to
    ``request.tenant``. If the host is the bare base domain or a reserved
    subdomain, ``request.tenant`` is ``None`` and the custom-domain
    fallback is skipped.
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
        custom = (
            CustomDomain.objects.select_related("tenant__template")
            .filter(domain=host, is_verified=True)
            .first()
        )
        if custom:
            return custom.tenant

        return None
