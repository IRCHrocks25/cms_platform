from typing import NamedTuple

import httpx
from django.conf import settings

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"


class TxtRecords(NamedTuple):
    ssl_txt_name: str
    ssl_txt_value: str
    ssl_txt_name_2: str
    ssl_txt_value_2: str


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
    """Pull both SSL TXT validation records out of an add/status
    response. Cloudflare may return one or two entries in
    ``result.ssl.validation_records``; missing slots come back as
    empty strings."""
    result = (cf_response or {}).get("result") or {}
    ssl_records = (result.get("ssl") or {}).get("validation_records") or []
    first = (ssl_records[0] or {}) if len(ssl_records) >= 1 else {}
    second = (ssl_records[1] or {}) if len(ssl_records) >= 2 else {}
    return TxtRecords(
        ssl_txt_name=first.get("txt_name") or "",
        ssl_txt_value=first.get("txt_value") or "",
        ssl_txt_name_2=second.get("txt_name") or "",
        ssl_txt_value_2=second.get("txt_value") or "",
    )


def delete_custom_hostname(cloudflare_hostname_id: str) -> bool:
    """Remove a custom hostname from Cloudflare for SaaS."""
    resp = httpx.delete(
        f"{CLOUDFLARE_API}/zones/{settings.CLOUDFLARE_ZONE_ID}/custom_hostnames/{cloudflare_hostname_id}",
        headers=_headers(),
        timeout=10,
    )
    return resp.status_code == 200
