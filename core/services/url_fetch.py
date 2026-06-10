"""Fetch an HTML page from a URL for use by the template annotator.

Used by `dashboard:template_fetch_url`: operator pastes a URL, we fetch the
live HTML, drop it into the template_form textarea, and they kick off the
existing AI annotator on it. The annotator's strip-and-restore handles the
inline <style> / <script> blocks.

Safety:
- http/https schemes only
- 10s timeout
- 2 MB response size cap (most landing pages are 30-300 KB)
- follows up to 5 redirects (Cloudflare Pages, Webflow, etc. 301 to https)
- requires text/html (or similar) Content-Type

This is agency-operator-only on the view layer. We don't filter private
IPs / localhost here because operators are trusted; if that ever ships to
non-staff, add an SSRF allowlist before exposing the endpoint.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (compatible; LockedCMS-Annotator/1.0; "
    "+https://github.com/IRCHrocks25/cms_platform)"
)
ALLOWED_SCHEMES = {"http", "https"}
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


class UrlFetchError(Exception):
    """Raised when the URL can't be fetched into usable HTML."""


def fetch_url_html(
    url: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> str:
    """Fetch an HTML page and return the body as a string.

    Raises UrlFetchError on any failure mode (bad scheme, timeout, non-2xx
    status, non-HTML content type, oversized body, malformed response).
    """
    url = (url or "").strip()
    if not url:
        raise UrlFetchError("URL is required.")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlFetchError(
            f"Only http and https URLs are supported (got {parsed.scheme!r})."
        )
    if not parsed.netloc:
        raise UrlFetchError("URL is missing a host.")

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            max_redirects=max_redirects,
            headers=headers,
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise UrlFetchError(f"Request timed out after {timeout_seconds}s.") from exc
    except httpx.TooManyRedirects as exc:
        raise UrlFetchError(
            f"Too many redirects (max {max_redirects})."
        ) from exc
    except httpx.HTTPError as exc:
        raise UrlFetchError(f"Could not reach the URL: {exc}") from exc

    if response.status_code >= 400:
        raise UrlFetchError(
            f"Server returned HTTP {response.status_code}."
        )

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type and not any(content_type.startswith(c) for c in HTML_CONTENT_TYPES):
        raise UrlFetchError(
            f"Expected HTML, got Content-Type {content_type!r}."
        )

    content = response.content
    if len(content) > max_bytes:
        raise UrlFetchError(
            f"Response too large ({len(content):,} bytes; limit {max_bytes:,})."
        )

    try:
        return response.text
    except UnicodeDecodeError as exc:
        raise UrlFetchError(f"Response is not valid text: {exc}") from exc
