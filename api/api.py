import os

from django.conf import settings
from ninja import NinjaAPI
from ninja.errors import HttpError


api = NinjaAPI(title="Katek Sites API", version="1.0.0")


@api.get("/health")
def health(request):
    return {"status": "ok"}


@api.get("/monitored-hosts")
def monitored_hosts(request):
    """Every client-facing host, for the off-box uptime checker.

    The checker cannot reach this database directly, so it asks here. Guarded by
    a shared token rather than left open: the domains are public, but the list of
    who our clients are is not something to hand out on an unauthenticated GET.

    Returns 503 rather than an empty list when the token is unset, so a
    misconfigured deploy reads as broken instead of as "no sites to watch".
    """
    from core.models import CustomDomain

    expected = os.environ.get("MONITOR_TOKEN") or ""
    if not expected:
        raise HttpError(503, "MONITOR_TOKEN unset")
    if request.headers.get("X-Monitor-Token") != expected:
        raise HttpError(401, "bad token")

    base = settings.TENANT_BASE_DOMAIN
    hosts = [f"https://{base}/", f"https://{base}/login/"]
    hosts += [
        f"https://{d}/"
        for d in CustomDomain.objects.filter(is_verified=True)
        .order_by("domain")
        .values_list("domain", flat=True)
    ]
    # Tenant subdomains are deliberately absent. They all share one wildcard
    # Traefik router and one backend, so the two agency hosts above already
    # cover them, and a 200 from `<anything>.<base>` proves nothing: an
    # unknown subdomain returns 200 as well. Verified custom domains are
    # different and each one is worth a check: every domain gets its own
    # router in the Traefik dynamic file and its own certificate, which is
    # exactly what failed on 2026-09-01.
    return {"hosts": list(dict.fromkeys(hosts))}
