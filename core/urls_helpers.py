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


def _dev_base_domain():
    return (getattr(settings, "TENANT_DEV_BASE_DOMAIN", "") or "lvh.me").strip(".").lower()


def is_local_request(request):
    """True when the operator is browsing from a local-dev host.

    Drives environment-aware links: when the dashboard itself is served on
    localhost/127.0.0.1/`*.lvh.me`, the client's site links should point at
    the *local* server, not the production domain.
    """
    if not request:
        return False
    host, _ = _split_host_port(request)
    host = host.lower()
    dev = _dev_base_domain()
    return (
        host in {"localhost", "127.0.0.1", "0.0.0.0"}
        or host.endswith(".localhost")
        or host == dev
        or host.endswith("." + dev)
    )


def tenant_public_url(request, tenant):
    """
    Absolute base URL where visitors will see the site, e.g.
        http://acme.lvh.me:8000/      (local dev — reaches this server)
        https://acme.sites.katek.app/ (production)
        https://www.acmeclient.com/   (custom domain)

    Environment-aware: if the operator is on a local-dev host, the link uses a
    wildcard dev base (lvh.me / localhost) that routes to the local server so
    they can preview their own changes; otherwise it's the canonical https URL.
    """
    if is_local_request(request):
        # Pick a wildcard dev base that supports subdomains and points here.
        # `127.0.0.1` (an IP) can't carry a subdomain, so fall back to lvh.me.
        host, port = _split_host_port(request)
        host = host.lower()
        if host == "localhost" or host.endswith(".localhost"):
            dev_base = "localhost"
        else:
            dev_base = _dev_base_domain()
        return f"http://{tenant.subdomain}.{dev_base}{port}/"

    custom = (tenant.custom_domain or "").strip().lower()
    if custom:
        return f"https://{custom}/"

    base = _base_domain()
    _, port = _split_host_port(request)
    scheme = request.scheme if request else "http"
    if not base:
        return f"{scheme}://{tenant.subdomain}{port}/"
    if is_using_local_dev_base():
        # Dev base configured server-side: keep the caller's scheme/port.
        return f"{scheme}://{tenant.subdomain}.{base}{port}/"
    # Real base domain: the client's canonical public URL is https on the
    # standard port — independent of the host the operator is browsing from.
    return f"https://{tenant.subdomain}.{base}/"


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
