from typing import NamedTuple

import httpx
from django.conf import settings

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"


class TxtRecords(NamedTuple):
    ssl_txt_name: str
    ssl_txt_value: str
    pre_txt_name: str
    pre_txt_value: str


def _headers():
    return {
        "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def add_custom_hostname(domain: str) -> dict:
    """Register a custom hostname with Cloudflare for SaaS using TXT
    validation. Returns the full CF response. Use ``extract_txt_record``
    to pull the validation record the operator must publish at their
    DNS registrar."""
    resp = httpx.post(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames",
        headers=_headers(),
        json={"hostname": domain, "ssl": {"method": "txt", "type": "dv"}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_hostname_status(cloudflare_hostname_id: str) -> dict:
    """Fetch current state of a custom hostname. Returns the full CF
    response so callers can read both ``result.status`` (overall hostname
    status) and ``result.ssl.status`` plus the validation record via
    ``extract_txt_record``."""
    resp = httpx.get(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_hostname_id}",
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def extract_txt_record(cf_response: dict) -> TxtRecords:
    """Pull both TXT validation records out of an add/status response:
    the SSL cert validation record (``result.ssl.validation_records[0]``)
    and the hostname pre-validation record (``result.ownership_verification``).
    Missing fields come back as empty strings."""
    result = (cf_response or {}).get("result") or {}
    ssl_records = (result.get("ssl") or {}).get("validation_records") or []
    ssl_first = (ssl_records[0] or {}) if ssl_records else {}
    ownership = result.get("ownership_verification") or {}
    return TxtRecords(
        ssl_txt_name=ssl_first.get("txt_name") or "",
        ssl_txt_value=ssl_first.get("txt_value") or "",
        pre_txt_name=ownership.get("name") or "",
        pre_txt_value=ownership.get("value") or "",
    )


def delete_custom_hostname(cloudflare_hostname_id: str) -> bool:
    """Remove a custom hostname from Cloudflare for SaaS."""
    resp = httpx.delete(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_hostname_id}",
        headers=_headers(),
        timeout=10,
    )
    return resp.status_code == 200
