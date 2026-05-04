from django.conf import settings

from .models import Tenant


class TenantResolverMiddleware:
    """
    Resolves a tenant from the host's leftmost subdomain when the host is
    a tenant host (`<sub>.<TENANT_BASE_DOMAIN>`). The resolved tenant is
    attached to ``request.tenant``. If the host is the bare base domain,
    a reserved subdomain (``www``/``app``/``api``), or doesn't match the
    base domain at all, ``request.tenant`` is ``None`` and standard URL
    routing applies.
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

        if not base or not host.endswith("." + base):
            return None

        sub_part = host[: -(len(base) + 1)]
        if not sub_part:
            return None

        # Only treat the leftmost label as the tenant subdomain. If the
        # host has additional labels between the subdomain and the base
        # domain (e.g. `acme.staging.yourdomain.com`), don't try to be
        # clever — leave it unresolved.
        if "." in sub_part:
            return None

        if sub_part in reserved:
            return None

        return Tenant.objects.select_related("template").filter(subdomain=sub_part).first()
