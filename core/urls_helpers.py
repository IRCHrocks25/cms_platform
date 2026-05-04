"""
URL builders for tenant-facing surfaces.

Centralizes the logic for turning a Tenant into the absolute URLs we show
on the post-create success page (and anywhere else that needs them).

Resolution priority for the public hostname:
    1. Tenant.custom_domain (if set) — always rendered https://
    2. <subdomain>.<TENANT_BASE_DOMAIN> on the same scheme/port as the
       current request

There is no TenantDomain table in this project — `core.middleware`
resolves request.tenant directly from `Tenant.subdomain`, so the
"temporary domain" is purely derived, not persisted.
"""

from django.conf import settings
from django.urls import reverse


def _split_host_port(request):
    raw = (request.get_host() or "") if request else ""
    if ":" in raw:
        host, port = raw.rsplit(":", 1)
        return host, f":{port}"
    return raw, ""


def _base_domain():
    return (settings.TENANT_BASE_DOMAIN or "").strip(".").lower()


def is_using_local_dev_base():
    """True when TENANT_BASE_DOMAIN is the dev default (localhost-like)."""
    base = _base_domain()
    return base in {"", "localhost"} or base.endswith(".local")


def tenant_public_url(request, tenant):
    """
    Absolute base URL where visitors will see the site, e.g.
        http://acme.localhost:8000/
        https://acme.example.com/
        https://www.acmeclient.com/
    """
    custom = (tenant.custom_domain or "").strip().lower()
    if custom:
        return f"https://{custom}/"

    base = _base_domain()
    _, port = _split_host_port(request)
    scheme = request.scheme if request else "http"
    if not base:
        return f"{scheme}://{tenant.subdomain}{port}/"
    return f"{scheme}://{tenant.subdomain}.{base}{port}/"


def tenant_editor_url(request, tenant):
    """Where the client logs in to edit content."""
    return f"{tenant_public_url(request, tenant).rstrip('/')}/dashboard/"


def tenant_login_url(request, tenant):
    """Login page on the tenant host (carries the client to /dashboard/)."""
    return f"{tenant_public_url(request, tenant).rstrip('/')}/login/"


def tenant_public_render_fallback_url(request, tenant):
    """
    Always-works URL on the agency host: `/site/<subdomain>/`.

    Useful when wildcard DNS isn't set up yet, or when the operator just
    wants to share a link that doesn't depend on subdomain routing.
    """
    if not request:
        return reverse("public_render", args=[tenant.subdomain])
    scheme = request.scheme
    host = request.get_host()
    return f"{scheme}://{host}{reverse('public_render', args=[tenant.subdomain])}"


def build_tenant_url_bundle(request, tenant):
    """
    Single dict consumed by the site_created template.

    Keeping it here (rather than in the view) means the same bundle can
    be reused on tenant_detail later without duplicating logic.
    """
    return {
        "public_url": tenant_public_url(request, tenant),
        "login_url": tenant_login_url(request, tenant),
        "editor_url": tenant_editor_url(request, tenant),
        "fallback_url": tenant_public_render_fallback_url(request, tenant),
        "using_local_dev_base": is_using_local_dev_base(),
        "base_domain": _base_domain(),
        "has_custom_domain": bool((tenant.custom_domain or "").strip()),
    }
