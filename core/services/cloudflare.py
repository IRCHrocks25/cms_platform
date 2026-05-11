import httpx
from django.conf import settings

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"


def _headers():
    return {
        "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def add_custom_hostname(domain: str) -> dict:
    """Register a custom hostname with Cloudflare for SaaS. Returns the CF response."""
    resp = httpx.post(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames",
        headers=_headers(),
        json={"hostname": domain, "ssl": {"method": "http", "type": "dv"}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_hostname_status(cloudflare_hostname_id: str) -> dict:
    """Check the current status of a custom hostname."""
    resp = httpx.get(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_hostname_id}",
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def delete_custom_hostname(cloudflare_hostname_id: str) -> bool:
    """Remove a custom hostname from Cloudflare for SaaS."""
    resp = httpx.delete(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_hostname_id}",
        headers=_headers(),
        timeout=10,
    )
    return resp.status_code == 200
