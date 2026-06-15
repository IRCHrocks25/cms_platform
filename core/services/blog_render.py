"""
Blog rendering helpers: queryset scoping, per-site blog settings, and
injecting the featured-posts strip into a rendered homepage.

Blog index/detail pages are plain Django templates (one of the built-in
styles) rendered server-side — they are NOT run through the annotated-HTML
parser. The only blog surface that touches the homepage render is the strip,
injected here.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from django.template.loader import render_to_string

from core.models import (
    BLOG_TEMPLATE_IDS,
    DEFAULT_BLOG_TEMPLATE,
    BLOG_STRIP_IDS,
    DEFAULT_BLOG_STRIP,
    BlogPost,
)
from core.renderer import render_site, merge_with_defaults, apply_head_settings
from .sanitizer import sanitize_html


# Web fonts the blog typography expects (Fraunces headings + Roboto body).
# Injected into the site head when the client's template doesn't already load
# them, so blog content keeps its designed look inside the site chrome.
_BLOG_FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet">'
)


BLOG_SETTINGS_DEFAULTS = {
    "template": DEFAULT_BLOG_TEMPLATE,
    "title": "Blog",
    "strip_enabled": True,
    "strip_count": 3,
    "strip_heading": "From the blog",
    "strip_style": DEFAULT_BLOG_STRIP,
}

# Posts per page on the index, by style.
INDEX_PAGE_SIZE = {"minimal": 8, "magazine": 7, "cards": 9}
STRIP_MAX = 6


def get_blog_settings(tenant) -> dict:
    """Per-site blog config with defaults applied and values clamped."""
    merged = dict(BLOG_SETTINGS_DEFAULTS)
    merged.update(tenant.blog_settings or {})

    if merged.get("template") not in BLOG_TEMPLATE_IDS:
        merged["template"] = DEFAULT_BLOG_TEMPLATE

    if merged.get("strip_style") not in BLOG_STRIP_IDS:
        merged["strip_style"] = DEFAULT_BLOG_STRIP

    merged["strip_enabled"] = bool(merged.get("strip_enabled", True))

    try:
        count = int(merged.get("strip_count", 3))
    except (TypeError, ValueError):
        count = 3
    merged["strip_count"] = max(1, min(STRIP_MAX, count))

    merged["title"] = (str(merged.get("title") or "Blog")).strip() or "Blog"
    merged["strip_heading"] = (
        str(merged.get("strip_heading") or "From the blog")
    ).strip() or "From the blog"
    return merged


def strip_template_path(strip_style: str) -> str:
    """Resolve a homepage-strip layout id to its self-contained template."""
    if strip_style not in BLOG_STRIP_IDS:
        strip_style = DEFAULT_BLOG_STRIP
    return f"blog/strips/{strip_style}.html"


def published_posts(tenant):
    """Posts safe to show publicly.

    Visibility is governed by STATUS: a post is public once its status is
    ``published`` (drafts are never public). We intentionally do NOT gate on
    ``publish_date <= now``. The publish_date is an *optional display/ordering*
    date entered via a timezone-naive ``<input type=datetime-local>``; a client
    publishing "now" in their local zone routinely yields a value a few hours in
    the future once the server stores it as UTC, and a ``<= now`` gate then
    silently hid the freshly-published post from the index, post pages, and the
    homepage strip. ``publish_date`` is still required (``BlogPost.save`` stamps
    it on publish) so ordering/display stay stable. See BUGFIX.md.
    """
    return tenant.blog_posts.filter(
        status=BlogPost.STATUS_PUBLISHED,
        publish_date__isnull=False,
    )


def featured_posts(tenant, limit: int):
    return list(
        published_posts(tenant)
        .filter(featured=True)
        .order_by("featured_order", "-publish_date")[:limit]
    )


def accent_color(tenant) -> str:
    """Best-effort brand accent pulled from the homepage brand tokens."""
    brand = (tenant.content or {}).get("brand") or {}
    return (brand.get("primary") or brand.get("accent") or "").strip()


def template_path(style: str, surface: str) -> str:
    if style not in BLOG_TEMPLATE_IDS:
        style = DEFAULT_BLOG_TEMPLATE
    return f"blog/{style}/{surface}.html"


def render_detail(
    tenant,
    post,
    *,
    style: str | None = None,
    request=None,
    blog_base: str = "/blog/",
    preview_bridge: bool = False,
    is_preview: bool = False,
) -> tuple[str, str]:
    """Render a post's detail page, wrapped in the client's site chrome.

    Returns ``(html, style)``. Shared by the public blog detail view and the
    dashboard live-preview endpoint. ``style`` forces a specific blog style
    (used when the editor swaps styles); otherwise the post's effective style
    is used. The blog template renders only a content *fragment*
    (``_content.html``); ``wrap_in_site_chrome`` drops it inside the client's
    real navbar/footer/head. When ``preview_bridge`` is set, the fragment
    includes the editor bridge so the dashboard can patch content in place.
    """
    settings = get_blog_settings(tenant)
    if style not in BLOG_TEMPLATE_IDS:
        style = post.effective_template(settings["template"])

    fragment = render_to_string(
        template_path(style, "detail"),
        {
            "tenant": tenant,
            "settings": settings,
            "style": style,
            "post": post,
            "safe_body": sanitize_html(post.body),
            "accent": accent_color(tenant),
            "blog_base": blog_base,
            "is_preview": is_preview,
            "preview_bridge": preview_bridge,
        },
        request=request,
    )
    html = wrap_in_site_chrome(
        tenant, fragment, request=request, home_url=_home_from_blog_base(blog_base)
    )
    return html, style


def render_index(tenant, context, *, style, request=None) -> str:
    """Render the blog index fragment wrapped in the client's site chrome."""
    fragment = render_to_string(template_path(style, "index"), context, request=request)
    home_url = _home_from_blog_base(context.get("blog_base", "/blog/"))
    return wrap_in_site_chrome(tenant, fragment, request=request, home_url=home_url)


def _home_from_blog_base(blog_base: str) -> str:
    """The site homepage URL for a given blog base, so chrome anchor links on
    blog pages can point back to the homepage's sections.
    ``/blog/`` → ``/`` ; ``/site/acme/blog/`` → ``/site/acme/``.
    """
    base = blog_base or "/blog/"
    if base.endswith("blog/"):
        return base[: -len("blog/")] or "/"
    return "/"


# Body content of the homepage is everything marked with `data-section`,
# except the navbar and footer — those are the chrome we keep.
def _find_chrome(soup, body):
    nav = soup.find(attrs={"data-section": "nav"})
    if nav is None:
        nav = body.find("nav", recursive=False) or body.find("header", recursive=False)
        if nav is None:
            nav = body.find("nav") or body.find("header")
    footer = soup.find(attrs={"data-section": "footer"}) or soup.find("footer")
    return nav, footer


def _detect_site_container(shell_html: str):
    """Best-effort: find the client's dominant centered container so the blog
    can line up with the navbar. Returns ``(max_width, gutter)`` CSS values
    (e.g. ``("1200px", "48px")``) from the widest rule that both caps
    ``max-width`` and centers with auto horizontal margins; falls back to a
    comfortable default when nothing matches.
    """
    default = ("1100px", "24px")
    if not shell_html:
        return default
    best_px, best = 0.0, None
    for m in re.finditer(r"\{([^{}]*)\}", shell_html):
        block = m.group(1)
        if "max-width" not in block or "margin" not in block:
            continue
        if not re.search(r"margin[^;]*:\s*(0\s+auto|auto)", block) and \
           not re.search(r"margin-(?:left|right)\s*:\s*auto", block):
            continue
        wm = re.search(r"max-width\s*:\s*(\d+(?:\.\d+)?)(px|rem)", block)
        if not wm:
            continue
        w_px = float(wm.group(1)) * (16 if wm.group(2) == "rem" else 1)
        if w_px < 700 or w_px > 1600 or w_px <= best_px:
            continue
        cw = f"{wm.group(1)}{wm.group(2)}"
        gut = default[1]
        pm = re.search(r"padding\s*:\s*([^;]+)", block)
        if pm:
            parts = pm.group(1).split()
            gut = parts[1] if len(parts) >= 2 else parts[0]
        else:
            pm2 = re.search(r"padding-(?:left|right)\s*:\s*([^;]+)", block)
            if pm2:
                gut = pm2.group(1).strip()
        best_px, best = w_px, (cw, gut)
    return best or default


def _css_var_value(css: str, name: str) -> str:
    m = re.search(r"--" + re.escape(name) + r"\s*:\s*([^;]+);", css)
    return m.group(1).strip() if m else ""


def _detect_masthead_bg(shell_html: str, hero_el) -> str:
    """The client's hero background — the dark/colored backdrop the navbar was
    designed to sit over. Reproducing it behind the nav on blog pages keeps the
    nav's (unchanged) light/dark text readable, exactly as on the homepage.
    Returns a CSS color (var()s resolved from the stylesheet) or ''.
    """
    if not shell_html or hero_el is None:
        return ""
    classes = hero_el.get("class") or []
    for cls in classes:
        for m in re.finditer(r"\." + re.escape(cls) + r"\s*\{([^{}]*)\}", shell_html):
            bm = re.search(r"background(?:-color)?\s*:\s*([^;]+);", m.group(1))
            if not bm:
                continue
            val = bm.group(1)
            vm = re.search(r"var\(\s*--([\w-]+)", val)
            if vm:
                resolved = _css_var_value(shell_html, vm.group(1))
                if resolved:
                    return resolved.split("!")[0].strip().split()[0]
            cm = re.search(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", val)
            if cm:
                return cm.group(0)
    return ""


def _wrap_minimal(inner_html: str) -> str:
    """Fallback when the client's template has no usable <body> chrome."""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        + _BLOG_FONTS_LINK
        + "<style>body{margin:0;}</style></head><body>"
        + inner_html
        + "</body></html>"
    )


def wrap_in_site_chrome(tenant, inner_html: str, *, request=None, home_url: str = "/") -> str:
    """Render ``inner_html`` (a blog content fragment) inside the client's
    actual site chrome: same ``<head>`` (global CSS, fonts, brand tokens),
    navbar and footer. The homepage's own ``data-section`` content blocks are
    removed and the blog content takes their place — single source of truth
    for the layout, no per-page fork. Falls back to a minimal standalone shell
    if the template has no recognizable ``<body>``.

    SEO/analytics head tags are NOT injected here (the chrome is rendered with
    ``site_settings=None`` to avoid double-injecting GA/custom scripts); the
    caller layers them on with ``apply_head_settings`` so per-page SEO wins.
    """
    shell_html = ""
    if tenant.template_id and tenant.template.html_source:
        content = merge_with_defaults(tenant.template.schema, tenant.content)
        shell_html = render_site(
            tenant.template.html_source,
            content,
            preview=False,
            site_settings=None,
        )

    soup = BeautifulSoup(shell_html, "lxml") if shell_html else None
    body = soup.find("body") if soup else None
    if soup is None or body is None:
        return _wrap_minimal(inner_html)

    nav, footer = _find_chrome(soup, body)

    # The client navbar is often transparent with light text designed to sit
    # over the (now-removed) hero. Capture the hero's background BEFORE we strip
    # it, so we can recreate that backdrop behind the navbar on the blog page —
    # otherwise the nav text is invisible on the light blog background.
    hero = None
    for section in soup.find_all(attrs={"data-section": True}):
        if section is nav or section is footer:
            continue
        hero = section
        break
    masthead_bg = _detect_masthead_bg(shell_html, hero)

    head = soup.find("head")

    # Strip the homepage's content sections — but first RESCUE any site
    # <script>/<style>/<link> living inside them, so the chrome's JS/CSS is
    # still present on blog pages (scripts → end of body, styles/links → head).
    # The chrome behaves identically to the homepage regardless of where the
    # agency placed its scripts.
    def _rescue_assets(el):
        for asset in el.find_all(["script", "style", "link"]):
            asset.extract()
            if asset.name == "script":
                body.append(asset)
            elif head is not None:
                head.append(asset)

    content_sections = [
        s for s in soup.find_all(attrs={"data-section": True})
        if s is not nav and s is not footer
    ]

    # UNANNOTATED homepage blocks (logo tickers, divider rules, …) that sit
    # between the content sections are content too — left in place they pile
    # up under the navbar on blog pages (the nav is often transparent/fixed,
    # designed to sit over the now-removed hero). Sweep the top-level span
    # from the first content section down to the footer and drop everything
    # that isn't nav/footer chrome. Elements BEFORE the first section (page
    # loaders, mobile-menu drawers, overlays the nav JS needs) are kept.
    def _top_level_host(el):
        host = el
        while host is not None and host.parent is not None and host.parent is not body:
            host = host.parent
        return host if host is not None and host.parent is body else None

    hosts = [h for h in (_top_level_host(s) for s in content_sections) if h is not None]
    if hosts:
        top = list(body.find_all(recursive=False))
        idx = {id(el): i for i, el in enumerate(top)}
        nav_host = _top_level_host(nav) if nav is not None else None
        footer_host = _top_level_host(footer) if footer is not None else None
        start = min(idx[id(h)] for h in hosts if id(h) in idx)
        end = idx.get(id(footer_host), max(idx[id(h)] for h in hosts if id(h) in idx) + 1)
        for el in top[start:end]:
            if el is nav_host or el is footer_host:
                continue
            if nav is not None and any(d is nav for d in el.descendants):
                continue
            if footer is not None and any(d is footer for d in el.descendants):
                continue
            _rescue_assets(el)
            el.decompose()

    # Any content section the sweep didn't reach (nested outside the span,
    # or no top-level host at all) still gets removed individually.
    for section in content_sections:
        if section.decomposed:
            continue
        # Don't destroy chrome nested INSIDE a content section. A plain
        # <nav>/<footer> (no data-section) can live within a content block;
        # decomposing the block would take the chrome with it, detaching it
        # so the insert_before/after below raises "Element has no parent".
        # The sweep above already skips such hosts; mirror that here.
        if nav is not None and any(d is nav for d in section.descendants):
            continue
        if footer is not None and any(d is footer for d in section.descendants):
            continue
        _rescue_assets(section)
        section.decompose()

    frag = BeautifulSoup(inner_html, "lxml")
    # lxml hoists a leading <style> (and any <link>/<meta>) into the fragment's
    # <head>; move those into the real chrome <head> so the blog's stylesheet
    # is NOT dropped (the whole reason the content rendered unstyled before).
    if frag.head is not None and head is not None:
        for node in list(frag.head.children):
            head.append(node)
    body_nodes = list(frag.body.children) if frag.body else list(frag.children)
    # Guard on .parent: insert_before/after on a detached node raises. The
    # chrome-protection above normally keeps these attached, but a nav/footer
    # that ended up parentless for any reason falls through to a plain append.
    if footer is not None and footer.parent is not None:
        for node in body_nodes:
            footer.insert_before(node)
    elif nav is not None and nav.parent is not None:
        ref = nav
        for node in body_nodes:
            ref.insert_after(node)
            ref = node
    else:
        for node in body_nodes:
            body.append(node)

    # Align the blog container with the client's own centered container so the
    # blog's outer bounds match the navbar's. Expose detected width/gutter as
    # CSS vars on .cms-blog (the scoped CSS reads them with sane fallbacks).
    cw, gut = _detect_site_container(shell_html)
    blog_root = soup.find(class_="cms-blog")
    if blog_root is not None:
        decls = [f"--site-cw: {cw}", f"--site-gut: {gut}"]
        if masthead_bg:
            decls.append(f"--cms-masthead: {masthead_bg}")
        existing = str(blog_root.get("style", "") or "").rstrip().rstrip(";")
        blog_root["style"] = (existing + "; " if existing else "") + "; ".join(decls) + ";"

    if head is not None and "family=Fraunces" not in shell_html:
        for node in list(BeautifulSoup(_BLOG_FONTS_LINK, "html.parser").children):
            head.append(node)

    _rewrite_chrome_anchors(soup, blog_root, home_url)

    return str(soup)


def _rewrite_chrome_anchors(soup, blog_root, home_url: str) -> None:
    """On blog pages the injected navbar/footer keep the homepage's in-page
    anchor hrefs (``#about`` …) which point at sections that don't exist here.
    Repoint them at the homepage (``/#about`` / ``/site/<sub>/#about``) so they
    navigate + scroll. Left untouched: bare ``#`` (JS handlers), absolute/full
    URLs, ``mailto:``/``tel:``, and any link inside the blog content
    (``.cms-blog``) — and the homepage itself, which never calls this wrapper.
    """
    prefix = (home_url or "/").rstrip("/")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if len(href) < 2 or not href.startswith("#"):
            continue  # skip bare "#" and non-anchor hrefs
        if blog_root is not None and blog_root in a.parents:
            continue  # leave the post's own content links alone
        a["href"] = f"{prefix}/{href}"


def render_strip_fragment(tenant, request=None, *, blog_base: str = "/blog/") -> str:
    """HTML fragment for the homepage strip, or '' if nothing to show."""
    settings = get_blog_settings(tenant)
    if not settings["strip_enabled"]:
        return ""
    posts = featured_posts(tenant, settings["strip_count"])
    if not posts:
        return ""
    return render_to_string(
        strip_template_path(settings["strip_style"]),
        {
            "tenant": tenant,
            "posts": posts,
            "heading": settings["strip_heading"],
            "accent": accent_color(tenant),
            "blog_base": blog_base,
        },
        request=request,
    )


# Minimal page shell for the dashboard strip-preview iframe. The strip
# fragment carries its own scoped CSS; this just gives it a clean document
# with the same web fonts the public blog pages load, so the preview matches.
_STRIP_DOC_HEAD = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>"
    "<link href=\"https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Roboto:wght@300;400;500;700&display=swap\" rel=\"stylesheet\">"
    "<style>html,body{margin:0;padding:0;background:#fff;}"
    ".strip-preview-empty{font-family:'Roboto',-apple-system,sans-serif;color:#8a8fa3;"
    "text-align:center;padding:64px 24px;font-size:15px;}</style></head><body>"
)
_STRIP_DOC_TAIL = "</body></html>"


def render_strip_doc(
    tenant,
    *,
    strip_style: str | None = None,
    count=None,
    heading: str | None = None,
    enabled=None,
    request=None,
    blog_base: str = "/blog/",
) -> str:
    """Full HTML document for the dashboard's live homepage-strip preview.

    Honors *unsaved* settings passed as overrides (strip_style/count/heading/
    enabled) so the client sees their pending choices before saving. Falls
    back to the saved blog settings for any override left as ``None``.
    Matches the public strip: only published, featured posts appear.
    """
    saved = get_blog_settings(tenant)

    if strip_style not in BLOG_STRIP_IDS:
        strip_style = saved["strip_style"]
    if enabled is None:
        enabled = saved["strip_enabled"]
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = saved["strip_count"]
    count = max(1, min(STRIP_MAX, count))
    heading = (str(heading) if heading is not None else "").strip() or saved["strip_heading"]

    if not enabled:
        inner = '<p class="strip-preview-empty">The homepage strip is turned off.</p>'
    else:
        posts = featured_posts(tenant, count)
        if not posts:
            inner = (
                '<p class="strip-preview-empty">No published featured posts yet. '
                "Publish a post and tap the ☆ star to feature it here.</p>"
            )
        else:
            inner = render_to_string(
                strip_template_path(strip_style),
                {
                    "tenant": tenant,
                    "posts": posts,
                    "heading": heading,
                    "accent": accent_color(tenant),
                    "blog_base": blog_base,
                },
                request=request,
            )
    return _STRIP_DOC_HEAD + inner + _STRIP_DOC_TAIL


def inject_strip(html: str, tenant, request=None, *, blog_base: str = "/blog/") -> str:
    """Place the featured-posts strip into a rendered homepage.

    Into ``[data-blog-strip]`` if the template marks a spot; otherwise
    just before the footer, else at the end of ``<body>``. No featured
    posts → returns the HTML untouched (no empty strip on the public site).
    """
    fragment = render_strip_fragment(tenant, request=request, blog_base=blog_base)
    if not fragment:
        return html

    soup = BeautifulSoup(html, "lxml")
    frag_soup = BeautifulSoup(fragment, "lxml")
    nodes = list(
        frag_soup.body.children if frag_soup.body else frag_soup.children
    )
    if not nodes:
        return html

    marker = soup.find(attrs={"data-blog-strip": True})
    if marker is not None:
        marker.clear()
        for node in nodes:
            marker.append(node)
        return str(soup)

    body = soup.find("body")
    if body is None:
        return html

    footer = soup.find(attrs={"data-section": "footer"}) or soup.find("footer")
    if footer is not None and footer.parent is not None:
        for node in nodes:
            footer.insert_before(node)
    else:
        for node in nodes:
            body.append(node)
    return str(soup)
