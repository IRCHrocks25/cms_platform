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
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

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


# Schemes / prefixes that are NOT relative URLs and must be left alone.
_ABSOLUTE_URL_PREFIXES = (
    "http://", "https://", "//", "data:", "mailto:", "tel:",
    "javascript:", "#",
)

# Tag + attribute pairs that carry URLs we want to rewrite.
_URL_ATTRS = (
    ("a", "href"),
    ("link", "href"),
    ("img", "src"),
    ("script", "src"),
    ("source", "src"),
    ("video", "src"),
    ("audio", "src"),
    ("iframe", "src"),
)


def rewrite_relative_urls(html: str, base_url: str) -> str:
    """Convert relative URLs in the parsed HTML to absolute URLs against base_url.

    Touches `<a href>`, `<link href>`, `<img src>`, `<script src>`,
    `<source src>`, `<video src>`, `<audio src>`, `<iframe src>`. Leaves
    fragment-only (#section), already-absolute, protocol-relative (//),
    data:, mailto:, tel:, and javascript: URLs untouched.

    Used by `fetch_url_html` so a fetched landing page's footer links and
    image references continue to resolve once the page is rendered from a
    different host (the CMS).
    """
    if not html or not base_url:
        return html
    soup = BeautifulSoup(html, "lxml")
    for tag, attr in _URL_ATTRS:
        for el in soup.find_all(tag):
            val = el.get(attr)
            if not val:
                continue
            stripped = val.strip()
            if not stripped or stripped.startswith(_ABSOLUTE_URL_PREFIXES):
                continue
            el[attr] = urljoin(base_url, stripped)
    return str(soup)


def fetch_url_html(
    url: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    rewrite_urls: bool = True,
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
        body = response.text
    except UnicodeDecodeError as exc:
        raise UrlFetchError(f"Response is not valid text: {exc}") from exc

    if rewrite_urls:
        # str(response.url) reflects the final URL after redirects, which is the
        # correct base for resolving relative references in the body.
        body = rewrite_relative_urls(body, str(response.url))

    return body
