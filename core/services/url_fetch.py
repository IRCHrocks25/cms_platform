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


def discover_sibling_html_urls(html: str, base_url: str) -> list[dict]:
    """Return same-origin sibling .html links found in `html`.

    Used by the multi-page importer: operator pastes a home URL, we fetch
    it, then surface every other .html page on the same origin so the
    operator can import them as CMS Pages alongside the home.

    Filter:
    - Same hostname as base_url
    - Path ends with .html or .htm
    - Not the same page as base_url itself (skip the home loopback)
    - Not in _INDEX_PATHS (skip /, /index.html, /index.htm — those ARE the home)

    Returns a list of dicts, deduped by URL, in document order:

        [
            {
                "url": "https://susan-rabbyv2.pages.dev/privacy-policy.html",
                "slug": "privacy-policy",
                "title": "Privacy",
            },
            ...
        ]

    The `slug` is the URL filename with the .html/.htm extension stripped,
    safe to use directly as a CMS Page slug. The `title` is the link text
    from the `<a>` tag, useful as a default Page title.
    """
    if not html or not base_url:
        return []
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    results: list[dict] = []
    for el in soup.find_all("a"):
        href = el.get("href")
        if not href:
            continue
        stripped = href.strip()
        if not stripped or stripped.startswith(_ABSOLUTE_URL_PREFIXES[3:]):
            # skip data:, mailto:, tel:, javascript:, #fragment-only.
            # Leave http:// / https:// / // for the same-origin check below.
            pass
        absolute = urljoin(base_url, stripped)
        a = urlparse(absolute)
        b = urlparse(base_url)
        if a.netloc != b.netloc:
            continue
        path = a.path or "/"
        if path in _INDEX_PATHS:
            continue
        if not path.lower().endswith((".html", ".htm")):
            continue
        if _is_same_page(absolute, base_url):
            continue
        # Drop fragment + query for the dedup key — same .html page with
        # different anchors is still the same page.
        canonical = f"{a.scheme}://{a.netloc}{path}"
        if canonical in seen:
            continue
        seen.add(canonical)
        filename = path.rsplit("/", 1)[-1]
        for ext in (".html", ".htm"):
            if filename.lower().endswith(ext):
                slug = filename[: -len(ext)]
                break
        else:
            slug = filename
        title = (el.get_text() or "").strip() or slug.replace("-", " ").title()
        results.append({"url": canonical, "slug": slug, "title": title})
    return results


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


# --------------------------------------------------------------------------- #
# SPA detection + JavaScript-rendered fallback                                #
# --------------------------------------------------------------------------- #
#
# Some agency clients hand over Figma Make exports built as Vite + React (or
# Next.js, SvelteKit, etc.) SPAs. A plain HTTP GET on those URLs returns only
# the index.html shell: a `<div id="root">` mount point and a `<script>` tag.
# The real content lives in the JS bundle and is rendered into the DOM at
# load time, which means the annotator has nothing to mark up.
#
# We deal with this in three steps, all exposed below:
#
#   1. ``looks_like_spa_shell()`` — heuristic to flag a response as SPA-style
#   2. ``render_url_html()``      — re-fetch through a headless browser so
#                                   the JS runs and we capture the hydrated
#                                   document. Requires the optional playwright
#                                   dependency (see README); raises a clear
#                                   error when unavailable so the operator can
#                                   either paste manually or enable it on the
#                                   server.
#   3. ``inline_external_assets()`` — purely string-level cleanup that turns
#                                   the captured DOM into something the CMS
#                                   can render standalone: inlines external
#                                   stylesheets, absolutizes image and other
#                                   asset URLs to the origin host, and strips
#                                   the now-redundant client-side JS bundle.
#
# The view in dashboard/views.py wires these together so the operator's
# normal "Fetch from URL" button just works on SPA sites whenever the optional
# render dependency is installed.

DEFAULT_RENDER_TIMEOUT_SECONDS = 60
_SPA_BODY_TEXT_THRESHOLD = 200  # chars of real text below which we suspect a shell
_SPA_MOUNT_IDS = ("root", "app", "__next", "__nuxt", "main", "svelte")


def looks_like_spa_shell(html: str) -> bool:
    """Return True when ``html`` looks like an unrendered SPA index document.

    Heuristic: strip script/style/comments/noscript, then if the visible
    body text is short *and* the body holds a known SPA mount point (e.g.
    ``<div id="root">`` for Vite/React, ``<div id="__next">`` for Next),
    treat this as a shell. Falsy on substantial static pages even when
    they happen to have a ``<div id="app">``.
    """
    if not html or not html.strip():
        return False
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 — never let parser failure crash the fetch
        return False
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    body = soup.find("body") or soup
    text = body.get_text(" ", strip=True)
    if len(text) > _SPA_BODY_TEXT_THRESHOLD:
        return False
    for mount_id in _SPA_MOUNT_IDS:
        if body.find(id=mount_id) is not None:
            return True
    return False


def render_url_html(
    url: str,
    *,
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> str:
    """Fetch ``url`` through a headless browser and return the hydrated DOM.

    Used as the SPA-aware sibling of :func:`fetch_url_html`. The optional
    ``playwright`` dependency must be installed on the server alongside the
    chromium browser bundle (``pip install playwright && playwright install
    --with-deps chromium``). When it isn't, this raises ``UrlFetchError``
    with a message the operator can act on; the caller is expected to fall
    back to the static fetch and surface the message verbatim.

    The function scrolls the page top-to-bottom after navigation so reveal-
    on-scroll components mount and emit their final markup, then captures
    ``document.documentElement.outerHTML``. ``<!DOCTYPE html>`` is prepended
    so the result is a complete document the parser can consume.
    """
    url = (url or "").strip()
    if not url:
        raise UrlFetchError("URL is required.")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlFetchError(
            f"Only http and https URLs are supported (got {parsed.scheme!r})."
        )

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        from playwright._impl._errors import TimeoutError as PWTimeoutError  # type: ignore
    except ImportError as exc:  # pragma: no cover — env-dependent
        raise UrlFetchError(
            "Server-side JavaScript rendering isn't enabled on this deploy. "
            "Install the optional dependency: `pip install playwright` then "
            "`playwright install --with-deps chromium`."
        ) from exc

    scroll_script = """
        async () => {
            const step = Math.max(window.innerHeight - 50, 400);
            for (let y = 0; y < document.body.scrollHeight; y += step) {
                window.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 200));
            }
            window.scrollTo(0, 0);
        }
    """

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=USER_AGENT,
                )
                page = ctx.new_page()
                try:
                    page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=timeout_seconds * 1000,
                    )
                except PWTimeoutError as exc:
                    raise UrlFetchError(
                        f"Browser render timed out after {timeout_seconds}s."
                    ) from exc
                page.wait_for_timeout(1500)
                page.evaluate(scroll_script)
                page.wait_for_timeout(800)
                html = page.evaluate("() => document.documentElement.outerHTML")
            finally:
                browser.close()
    except UrlFetchError:
        raise
    except Exception as exc:  # noqa: BLE001 — playwright wraps its own errors
        raise UrlFetchError(f"Browser render failed: {exc}") from exc

    return "<!DOCTYPE html>\n" + html


# Regex hot-paths for the inliner. Kept module-level so the patterns compile
# once per process instead of per call.
import re as _re_assets

_RE_STYLESHEET_LINK = _re_assets.compile(
    r'<link\b[^>]*?rel=(?:"|\')stylesheet(?:"|\')[^>]*?href=(?:"|\')([^"\']+)(?:"|\')[^>]*>',
    _re_assets.IGNORECASE,
)
_RE_MODULE_SCRIPT = _re_assets.compile(
    r'<script\b[^>]*\btype=(?:"|\')module(?:"|\')[^>]*></script>',
    _re_assets.IGNORECASE,
)
_RE_EXTERNAL_SCRIPT = _re_assets.compile(
    r'<script\b[^>]*\bsrc=(?:"|\')[^"\']+(?:"|\')[^>]*></script>',
    _re_assets.IGNORECASE,
)
_RE_NOINDEX_META = _re_assets.compile(
    r'<meta\b[^>]*\bname=(?:"|\')robots(?:"|\')[^>]*>\s*',
    _re_assets.IGNORECASE,
)
_RE_ASSET_SRC = _re_assets.compile(
    r'(<(?:img|source|video|audio|track)\b[^>]*?\ssrc=)(?:"|\')(/[^"\']*)(?:"|\')',
    _re_assets.IGNORECASE,
)
_RE_SRCSET = _re_assets.compile(r'srcset=(?:"|\')([^"\']+)(?:"|\')', _re_assets.IGNORECASE)
_RE_CSS_URL = _re_assets.compile(r'url\(([^)]+)\)', _re_assets.IGNORECASE)


def inline_external_assets(html: str, base_url: str) -> str:
    """Make ``html`` self-contained relative to ``base_url``.

    Designed for the rendered output of :func:`render_url_html`: turns the
    captured SPA DOM into HTML that the CMS can serve standalone. Three
    operations, in order:

    1. Each external ``<link rel="stylesheet">`` is replaced inline by the
       fetched CSS, with relative ``url(...)`` references inside the CSS
       rewritten to absolute origin URLs. If a stylesheet can't be fetched,
       the link is rewritten to absolute and left as a ``<link>`` (the
       template still works, it just depends on the origin host).
    2. Relative ``src`` (and ``srcset`` entries) on ``<img>``/``<source>``/
       ``<video>``/``<audio>``/``<track>`` are absolutized so images load
       from the origin host instead of the CMS subdomain.
    3. The hydrated DOM is now static, so the bundle ``<script type="module">``
       and the ``robots: noindex`` meta from the original deploy are removed.
    """
    if not html or not html.strip():
        return html
    if not base_url:
        return html

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return html
    origin = f"{parsed.scheme}://{parsed.netloc}"

    def _abs(url_ref: str) -> str:
        ref = url_ref.strip().strip("'\"")
        if not ref or ref.startswith(_ABSOLUTE_URL_PREFIXES):
            return ref
        return urljoin(base_url, ref)

    def _inline_link(match: "_re_assets.Match[str]") -> str:
        href = match.group(1)
        abs_url = _abs(href)
        if not abs_url or not abs_url.startswith(("http://", "https://")):
            return match.group(0)
        try:
            with httpx.Client(
                timeout=DEFAULT_TIMEOUT_SECONDS,
                follow_redirects=True,
                max_redirects=DEFAULT_MAX_REDIRECTS,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = client.get(abs_url)
            if resp.status_code >= 400:
                raise UrlFetchError(f"HTTP {resp.status_code}")
            css = resp.text
        except (httpx.HTTPError, UrlFetchError) as exc:
            logger.warning(
                "inline_external_assets: keeping external <link href=%r>: %s",
                abs_url, exc,
            )
            return f'<link rel="stylesheet" href="{abs_url}">'

        def _rewrite_css_url(m: "_re_assets.Match[str]") -> str:
            ref = m.group(1).strip().strip("'\"")
            if not ref or ref.startswith(_ABSOLUTE_URL_PREFIXES):
                return m.group(0)
            return f"url({urljoin(abs_url, ref)})"

        css = _RE_CSS_URL.sub(_rewrite_css_url, css)
        return f'<style data-inlined-from="{href}">\n{css}\n</style>'

    def _abs_asset_src(match: "_re_assets.Match[str]") -> str:
        path = match.group(2)
        return f'{match.group(1)}"{origin}{path}"'

    def _abs_srcset(match: "_re_assets.Match[str]") -> str:
        out: list[str] = []
        for entry in match.group(1).split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(None, 1)
            url_part = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            if not url_part.startswith(_ABSOLUTE_URL_PREFIXES):
                url_part = urljoin(base_url, url_part)
            out.append((url_part + " " + rest).strip())
        return 'srcset="' + ", ".join(out) + '"'

    html = _RE_STYLESHEET_LINK.sub(_inline_link, html)
    html = _RE_ASSET_SRC.sub(_abs_asset_src, html)
    html = _RE_SRCSET.sub(_abs_srcset, html)
    html = _RE_MODULE_SCRIPT.sub("", html)
    html = _RE_EXTERNAL_SCRIPT.sub("", html)
    html = _RE_NOINDEX_META.sub("", html)
    return html
