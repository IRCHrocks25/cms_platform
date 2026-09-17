"""
URL builders for tenant-facing surfaces.

Centralizes the logic for turning a Tenant into the absolute URLs we show
on the post-create success page (and anywhere else that needs them).

Resolution priority for the public hostname:
    1. The tenant's earliest *verified* ``CustomDomain`` row, always rendered
       https:// (CMS-63). ``Tenant.custom_domain`` is a display-only hint that
       drifted in production and is never consulted here.
    2. <subdomain>.<TENANT_BASE_DOMAIN> on the same scheme/port as the
       current request

There is no TenantDomain table in this project. `core.middleware`
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


def tenant_primary_custom_domain(tenant) -> str:
    """Host routing actually serves for ``tenant``: the earliest verified
    ``CustomDomain`` (lower-cased), or ``""`` when none is verified.

    Same rule as ``core.services.custom_domains.sync_tenant_primary_domain``
    so the URL helpers and the legacy display field agree. Honours a
    ``prefetch_related("custom_domains")`` cache so list views don't N+1;
    otherwise it is exactly one query.
    """
    cache = getattr(tenant, "_prefetched_objects_cache", None) or {}
    if "custom_domains" in cache:
        verified = sorted(
            (cd for cd in tenant.custom_domains.all() if cd.is_verified),
            key=lambda cd: (cd.created_at, cd.pk),
        )
        domain = verified[0].domain if verified else ""
    else:
        domain = (
            tenant.custom_domains.filter(is_verified=True)
            .order_by("created_at", "pk")
            .values_list("domain", flat=True)
            .first()
        )
    return (domain or "").strip().lower()


def tenant_canonical_public_url(tenant, *, page_slug: str | None = None) -> str:
    """Absolute public URL from TENANT_BASE_DOMAIN (no request).

    Used by MCP write tools so callers get a link to inspect. Builds
    ``{scheme}://{subdomain}.{TENANT_BASE_DOMAIN}/`` and never hardcodes a
    production hostname. Prefers the earliest verified ``CustomDomain``.
    """
    custom = tenant_primary_custom_domain(tenant)
    if custom:
        base_url = f"https://{custom}/"
    else:
        base = _base_domain()
        if is_using_local_dev_base() or not base:
            host = f"{tenant.subdomain}.{base}" if base else tenant.subdomain
            base_url = f"http://{host}/"
        else:
            base_url = f"https://{tenant.subdomain}.{base}/"

    if page_slug:
        return f"{base_url.rstrip('/')}/{page_slug.strip('/')}"
    return base_url


def tenant_public_url(request, tenant):
    """
    Absolute base URL where visitors will see the site, e.g.
        http://acme.lvh.me:8000/      (local dev; reaches this server)
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

    custom = tenant_primary_custom_domain(tenant)
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
    # standard port, independent of the host the operator is browsing from.
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
        "has_custom_domain": bool(tenant_primary_custom_domain(tenant)),
    }


def tenant_canonical_base_url(tenant) -> str:
    """Absolute base URL crawlers should treat as canonical for a tenant.

    Prefers the first *verified* ``CustomDomain`` row (the only hosts the
    route-syncer actually serves), and otherwise the ``<subdomain>.<base>``
    URL. Deliberately ignores the display-only ``Tenant.custom_domain`` hint:
    an unverified domain does not answer requests yet, so pointing search
    engines at it would 404. Used by sitemap.xml and robots.txt (CMS-57/58).
    """
    verified = tenant_primary_custom_domain(tenant)
    if verified:
        return f"https://{verified}/"
    base = _base_domain()
    if is_using_local_dev_base() or not base:
        host = f"{tenant.subdomain}.{base}" if base else tenant.subdomain
        return f"http://{host}/"
    return f"https://{tenant.subdomain}.{base}/"
