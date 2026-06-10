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

# Paths that a static host treats as "the home page" — equivalent to "/".
_INDEX_PATHS = ("/", "/index.html", "/index.htm")


def _is_same_page(absolute_url: str, base_url: str) -> bool:
    """True when absolute_url and base_url point at the same page.

    Treats `/`, `/index.html`, and `/index.htm` as equivalent, since most
    static hosts serve any of them as the root document.
    """
    a = urlparse(absolute_url)
    b = urlparse(base_url)
    if a.netloc != b.netloc:
        return False
    a_path = a.path or "/"
    b_path = b.path or "/"
    if a_path in _INDEX_PATHS and b_path in _INDEX_PATHS:
        return True
    return a_path == b_path


def rewrite_relative_urls(html: str, base_url: str) -> str:
    """Convert relative URLs in the parsed HTML to absolute URLs against base_url.

    Touches `<a href>`, `<link href>`, `<img src>`, `<script src>`,
    `<source src>`, `<video src>`, `<audio src>`, `<iframe src>`. Leaves
    fragment-only (#section), already-absolute, protocol-relative (//),
    data:, mailto:, tel:, and javascript: URLs untouched.

    Special case for navigation: `<a>` links that point at the SAME page as
    base_url (e.g. a brand-logo "back to home" link, `href="./"`, `href="/"`,
    `href="./index.html"`) are set to `href="/"` instead of the absolute
    source-origin URL — so when the CMS hosts the imported page at a new
    host, the home link stays on the CMS-hosted site instead of bouncing
    visitors back to the original origin.
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
            absolute = urljoin(base_url, stripped)
            if tag == "a" and _is_same_page(absolute, base_url):
                el[attr] = "/"
                continue
            el[attr] = absolute
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
