"""
Blog rendering helpers: queryset scoping, per-site blog settings, and
injecting the featured-posts strip into a rendered homepage.

Blog index/detail pages are plain Django templates (one of the built-in
styles) rendered server-side. They are NOT run through the annotated-HTML
parser. The only blog surface that touches the homepage render is the strip,
injected here.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from django.utils.html import strip_tags
from django.core.paginator import Paginator
from django.template.loader import render_to_string

from core.models import (
    BLOG_TEMPLATE_IDS,
    DEFAULT_BLOG_TEMPLATE,
    BLOG_STRIP_IDS,
    DEFAULT_BLOG_STRIP,
    BLOG_INDEX_LAYOUT_IDS,
    BLOG_INDEX_LAYOUT_STYLE,
    DEFAULT_BLOG_INDEX_LAYOUT,
    BlogPost,
)
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
    "index_layout": DEFAULT_BLOG_INDEX_LAYOUT,
    "title": "Blog",
    "strip_enabled": True,
    "strip_count": 3,
    "strip_heading": "From the blog",
    "strip_eyebrow": "What’s working now",
    "strip_subtitle": "",
    "strip_style": DEFAULT_BLOG_STRIP,
}

# Posts per page on the index, by style (or explicit listing layout).
INDEX_PAGE_SIZE = {
    "minimal": 8, "magazine": 7, "cards": 9,
    "image_left": 8, "image_right": 8, "image_above": 6,
}

# Public index sort orders (?sort=). pk is the final tie-breaker so posts that
# share a publish_date/created_at keep one stable position across pages: no
# post is repeated on, or skipped between, two pages.
INDEX_SORTS = {
    "newest": ("-publish_date", "-created_at", "-pk"),
    "oldest": ("publish_date", "created_at", "pk"),
}
DEFAULT_INDEX_SORT = "newest"
STRIP_MAX = 6


def get_blog_settings(tenant) -> dict:
    """Per-site blog config with defaults applied and values clamped."""
    merged = dict(BLOG_SETTINGS_DEFAULTS)
    merged.update(tenant.blog_settings or {})

    if merged.get("template") not in BLOG_TEMPLATE_IDS:
        merged["template"] = DEFAULT_BLOG_TEMPLATE

    if merged.get("index_layout") not in BLOG_INDEX_LAYOUT_IDS:
        merged["index_layout"] = DEFAULT_BLOG_INDEX_LAYOUT

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
    merged["strip_eyebrow"] = str(merged.get("strip_eyebrow") or "What’s working now").strip()[:120]
    merged["strip_subtitle"] = str(merged.get("strip_subtitle") or "").strip()[:240]
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


def index_posts(tenant, *, category: str = "", sort: str = DEFAULT_INDEX_SORT):
    """Published posts for the public index, filtered and ordered.

    ``category`` is a category slug ('' = all categories, uncategorized posts
    included). An unknown slug yields an empty queryset, which the templates
    show as an empty state, never an error. An unknown ``sort`` falls back to
    newest first.
    """
    qs = published_posts(tenant)
    if category:
        qs = qs.filter(category_slug=category)
    return qs.order_by(*INDEX_SORTS.get(sort, INDEX_SORTS[DEFAULT_INDEX_SORT]))


def index_categories(tenant) -> list[dict]:
    """Categories that have at least one published post, A→Z.

    Returns ``[{"slug", "name"}]``. Posts are grouped by slug; the display
    name is taken from the most recently published post in the group.
    """
    seen = {}
    rows = (
        published_posts(tenant)
        .exclude(category_slug="")
        .order_by("-publish_date", "-pk")
        .values_list("category_slug", "category")
    )
    for slug, name in rows:
        seen.setdefault(slug, name)
    return sorted(
        ({"slug": slug, "name": name} for slug, name in seen.items()),
        key=lambda c: c["name"].lower(),
    )


def index_context(
    tenant,
    *,
    settings: dict,
    blog_base: str,
    category: str = "",
    sort: str = "",
    page=None,
) -> dict:
    """Template context for a blog index page (public view + dashboard preview).

    Every index template gets the same filtered, sorted page. ``filter_qs`` is
    the active category/sort as a query-string prefix (``"category=news&"``)
    so pager and filter links keep the visitor's selection.
    """
    category = (category or "").strip().lower()
    sort = sort if sort in INDEX_SORTS else DEFAULT_INDEX_SORT
    style = settings["template"]
    index_layout = settings["index_layout"]

    categories = index_categories(tenant)
    paginator = Paginator(
        index_posts(tenant, category=category, sort=sort),
        index_page_size(style, index_layout),
    )
    page_obj = paginator.get_page(page)

    params = {}
    if category:
        params["category"] = category
    if sort != DEFAULT_INDEX_SORT:
        params["sort"] = sort
    category_qs = urlencode({"category": category}) + "&" if category else ""
    sort_qs = "sort=" + sort + "&" if sort != DEFAULT_INDEX_SORT else ""

    return {
        "tenant": tenant,
        "settings": settings,
        "style": style,
        "index_layout": index_layout,
        "page_obj": page_obj,
        "posts": page_obj.object_list,
        "accent": accent_color(tenant),
        "blog_base": blog_base,
        "categories": categories,
        "current_category": category,
        "current_category_name": next(
            (c["name"] for c in categories if c["slug"] == category), ""
        ),
        "current_sort": sort,
        "is_filtered": bool(category),
        "filter_qs": urlencode(params) + "&" if params else "",
        "category_qs": category_qs,
        "sort_qs": sort_qs,
    }


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
            "site_styled": _site_style_opt_in(tenant),
        },
        request=request,
    )
    html = wrap_in_site_chrome(
        tenant, fragment, request=request, home_url=_home_from_blog_base(blog_base)
    )
    return html, style


def index_template_path(style: str, index_layout: str) -> str:
    """The index template: an explicit listing layout, else the style's own."""
    if index_layout in BLOG_INDEX_LAYOUT_IDS and index_layout != BLOG_INDEX_LAYOUT_STYLE:
        return "blog/listing/index.html"
    return template_path(style, "index")


def index_page_size(style: str, index_layout: str) -> int:
    if index_layout in BLOG_INDEX_LAYOUT_IDS and index_layout != BLOG_INDEX_LAYOUT_STYLE:
        return INDEX_PAGE_SIZE[index_layout]
    return INDEX_PAGE_SIZE.get(style, 8)


def render_index(tenant, context, *, style, index_layout=BLOG_INDEX_LAYOUT_STYLE, request=None) -> str:
    """Render the blog index fragment wrapped in the client's site chrome."""
    fragment = render_to_string(
        index_template_path(style, index_layout),
        {**context, "site_styled": _site_style_opt_in(tenant)}, request=request,
    )
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


# Blog-only: a full-width grey→black strip behind light-type overlay navs
# so their designed white/light fonts stay readable on the white blog canvas.
# Applied only when JS adds .cms-nav-darkband (nav already uses light type).
# Homepage chrome is never wrapped. Separate <style> (no .cms-blog) so the
# scoped-CSS leak test ignores it.
_CHROME_CONTRAST_CSS = """
html.cms-blog-page [data-section="nav"].cms-nav-darkband,
html.cms-blog-page [data-section="navigation"].cms-nav-darkband{
  background:linear-gradient(180deg,#2a2a2e 0%,#111 100%)!important;
  background-image:linear-gradient(180deg,#2a2a2e 0%,#111 100%)!important;
  mix-blend-mode:normal!important;
  width:100%;
  box-sizing:border-box;
}
html.cms-blog-page [data-section="nav"].cms-nav-darkband img,
html.cms-blog-page [data-section="navigation"].cms-nav-darkband img{
  mix-blend-mode:normal!important;
}
""".strip()


# Body content of the homepage is everything marked with `data-section`,
# except the navbar and footer, which are the chrome we keep.
def _find_chrome(soup, body):
    nav = soup.find(attrs={"data-section": "nav"})
    if nav is None:
        nav = soup.find(attrs={"data-section": "navigation"})
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
    """The client's hero background, the dark/colored backdrop the navbar was
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
        "<!doctype html><html lang=\"en\" class=\"cms-blog-page\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        + _BLOG_FONTS_LINK
        + "<style>body{margin:0;}</style>"
        + '<style id="cms-blog-chrome-contrast">' + _CHROME_CONTRAST_CSS + "</style>"
        + "</head><body>"
        + inner_html
        + "</body></html>"
    )


def _ensure_head(soup):
    """Return the document ``<head>``, creating one if the template has none.

    Some imported templates start straight with body content (their own
    ``<style>`` included), so the parsed document has no ``<head>``. Anything
    the blog moves into the head (its scoped stylesheet, the fonts link, the
    chrome-contrast rule) would otherwise be silently dropped. Browsers treat
    an inserted head exactly like an authored one.
    """
    head = soup.find("head")
    if head is not None:
        return head
    head = soup.new_tag("head")
    html_tag = soup.find("html")
    if html_tag is None:
        return None
    html_tag.insert(0, head)
    return head


def wrap_in_site_chrome(tenant, inner_html: str, *, request=None, home_url: str = "/") -> str:
    """Render ``inner_html`` (a blog content fragment) inside the client's
    actual site chrome: same ``<head>`` (global CSS, fonts, brand tokens),
    navbar and footer. The homepage's own ``data-section`` content blocks are
    removed and the blog content takes their place, preserving a single source of truth
    for the layout, no per-page fork. Falls back to a minimal standalone shell
    if the template has no recognizable ``<body>``.

    SEO/analytics head tags are NOT injected here (the chrome receives only
    the footer-credit visibility setting); the caller layers them on with
    ``apply_head_settings`` so per-page SEO wins.
    """
    shell_html = ""
    if tenant.template_id and tenant.template.html_source:
        # Resolve block instances before typography discovery. On an installed
        # block shell the hero/heading elements live in blocks, not html_source;
        # rendering only the shell loses the site's semantic heading aliases.
        from .blocks import render_content
        shell_html = render_content(
            tenant.template,
            tenant.content,
            preview=False,
            site_settings={
                "show_footer_credit": (tenant.site_settings or {}).get("show_footer_credit") is not False,
            },
        )

    soup = BeautifulSoup(shell_html, "lxml") if shell_html else None
    body = soup.find("body") if soup else None
    if soup is None or body is None:
        return _wrap_minimal(inner_html)
    mounted_font_aliases = (
        _site_font_aliases(soup)
        if soup.find(attrs={"data-blog-strip": True}) and not _has_saved_typography(tenant)
        else []
    )

    nav, footer = _find_chrome(soup, body)

    html_tag = soup.find("html")
    if html_tag is not None:
        classes = list(html_tag.get("class") or [])
        if "cms-blog-page" not in classes:
            html_tag["class"] = classes + ["cms-blog-page"]

    head = _ensure_head(soup)
    if head is not None:
        contrast = soup.new_tag("style", id="cms-blog-chrome-contrast")
        contrast.string = _CHROME_CONTRAST_CSS
        head.append(contrast)

    # Strip the homepage's content sections, but first RESCUE any site
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
    # between the content sections are content too. Left in place, they pile
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
        decls.extend(mounted_font_aliases)
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
    (``.cms-blog``), and the homepage itself, which never calls this wrapper.
    """
    prefix = (home_url or "/").rstrip("/")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if len(href) < 2 or not href.startswith("#"):
            continue  # skip bare "#" and non-anchor hrefs
        if blog_root is not None and blog_root in a.parents:
            continue  # leave the post's own content links alone
        a["href"] = f"{prefix}/{href}"


IMAGE_STRIP_LAYOUTS = {"image_left", "image_right", "image_above"}
PER_BLOCK_STRIP_LAYOUTS = IMAGE_STRIP_LAYOUTS | {"front_line"}


def front_line_cards(posts):
    """Build strip-only display metadata; do not alter stored post content."""
    cards = []
    for index, post in enumerate(posts, start=1):
        words = re.findall(r"\b[\w'-]+\b", strip_tags(post.body or ""))
        cards.append({"post": post, "number": f"{index:02d}",
                      "reading_minutes": max(1, (len(words) + 199) // 200)})
    return cards


def _has_saved_typography(tenant) -> bool:
    content = tenant.content if isinstance(tenant.content, dict) else {}
    global_styles = content.get("_global")
    if not isinstance(global_styles, dict):
        return False
    keys = {"fontFamily", "headingFamily", "titleFamily", "baseSize", "headingSize"}
    return any(
        isinstance(value, str) and value.strip()
        and (key in keys or key.startswith(("font:", "group:")))
        for key, value in global_styles.items()
    )


def _site_style_opt_in(tenant) -> bool:
    if _has_saved_typography(tenant):
        return True
    if not tenant.template_id or not tenant.template.html_source:
        return False
    from .blocks import render_content

    shell = render_content(tenant.template, tenant.content, preview=False, site_settings=None)
    return BeautifulSoup(shell, "lxml").find(attrs={"data-blog-strip": True}) is not None


def _site_font_aliases(soup) -> list[str]:
    """Aliases for the template's discovered fonts, used only after opt-in."""
    from core.typography import detect_typography_tokens

    role_names = {token["role"]: token["name"] for token in detect_typography_tokens(soup)}
    aliases = []
    for role in ("body", "heading"):
        name = role_names.get(f"{role}-font")
        if name:
            aliases.append(f"--cms-font-{role}:var(--{name})")
    return aliases


def _mount_site_font_aliases(soup, markers) -> None:
    """Scope discovered template fonts to explicit mounts on unsaved sites."""
    aliases = _site_font_aliases(soup)
    if not aliases:
        return
    for marker in markers:
        current = str(marker.get("style", "")).rstrip("; ")
        declarations = [alias for alias in aliases if alias.split(":", 1)[0] not in current]
        marker["style"] = ";".join(part for part in (current, *declarations) if part) + ";"


def blog_strip_markers(tenant) -> list[dict]:
    """List the existing homepage and inner-page markers for the settings editor."""
    from . import blocks

    saved = (tenant.blog_settings or {}).get("strip_layouts") or {}
    if not isinstance(saved, dict):
        saved = {}
    rows = []
    pages = [("home", "Homepage", tenant.template, tenant.content)]
    pages.extend(
        (f"page_{page.pk}", page.title, page.template, page.content)
        for page in tenant.pages.select_related("template").all()
    )
    nav_pages = blocks.nav_pages_for(tenant)
    for namespace, page_label, template, content in pages:
        html = blocks.render_content(
            template, content, preview=False,
            site_settings=tenant.site_settings or {}, nav_pages=nav_pages,
        )
        for index, _ in enumerate(BeautifulSoup(html, "lxml").find_all(attrs={"data-blog-strip": True})):
            key = f"{namespace}_{index}"
            choice = saved.get(key)
            rows.append({
                "key": key,
                "label": f"{page_label} blog strip {index + 1} layout",
                "choice": choice if isinstance(choice, str) and choice in PER_BLOCK_STRIP_LAYOUTS else "",
            })
    return rows


def render_strip_fragment(tenant, request=None, *, blog_base: str = "/blog/", layout: str = "", site_styled: bool = False, show_header: bool = True) -> str:
    """HTML fragment for the homepage strip, or '' if nothing to show."""
    settings = get_blog_settings(tenant)
    if not settings["strip_enabled"]:
        return ""
    posts = featured_posts(tenant, settings["strip_count"])
    if not posts:
        return ""
    selected = layout if layout in PER_BLOCK_STRIP_LAYOUTS else ""
    style = selected or settings["strip_style"]
    site_styled = site_styled or style == "front_line"
    template_path = "blog/strips/listing.html" if selected in IMAGE_STRIP_LAYOUTS else strip_template_path(style)
    return render_to_string(
        template_path,
        {
            "tenant": tenant,
            "posts": posts,
            "heading": settings["strip_heading"],
            "accent": accent_color(tenant),
            "blog_base": blog_base,
            "layout": selected,
            "site_styled": site_styled,
            "eyebrow": settings["strip_eyebrow"],
            "subtitle": settings["strip_subtitle"],
            "front_line_cards": front_line_cards(posts) if style == "front_line" else [],
            "show_header": show_header,
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
    eyebrow: str | None = None,
    subtitle: str | None = None,
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
    eyebrow = (str(eyebrow) if eyebrow is not None else saved["strip_eyebrow"]).strip()[:120] or "What’s working now"
    subtitle = (str(subtitle) if subtitle is not None else saved["strip_subtitle"]).strip()[:240]

    shell_soup = None
    if tenant.template_id and tenant.template.html_source:
        from .blocks import render_content
        shell = render_content(tenant.template, tenant.content, preview=False, site_settings=None)
        shell_soup = BeautifulSoup(shell, "lxml")
    site_styled = strip_style == "front_line" or _has_saved_typography(tenant) or bool(
        shell_soup and shell_soup.find(attrs={"data-blog-strip": True})
    )

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
                    "site_styled": site_styled,
                    "eyebrow": eyebrow,
                    "subtitle": subtitle,
                    "front_line_cards": front_line_cards(posts) if strip_style == "front_line" else [],
                    "show_header": True,
                },
                request=request,
            )
    # Use the same rendered font aliases, overrides, stylesheet links and body
    # attributes as public output. A standalone fixed-font head hides site CSS
    # from the preview even when the fragment correctly consumes site tokens.
    if site_styled and shell_soup is not None:
        soup = shell_soup
        if soup.body is not None and _ensure_head(soup) is not None:
            # Retain an explicit mount's ancestor selectors and spacing. Block
            # templates can carry their CSS in the body, outside the shell head.
            marker = soup.find(attrs={"data-blog-strip": True})
            if marker is not None and not _has_saved_typography(tenant):
                _mount_site_font_aliases(soup, [marker])
            wrappers = []
            if marker is not None:
                node = marker
                while node is not None and node is not soup.body:
                    wrappers.append((node.name, dict(node.attrs)))
                    node = node.parent
            for asset in list(soup.body.find_all(["style", "link"])):
                soup.head.append(asset.extract())
            soup.body.clear()
            for script in soup.find_all("script"):
                script.decompose()
            target = soup.body
            for name, attrs in reversed(wrappers):
                wrapper = soup.new_tag(name, attrs=attrs)
                target.append(wrapper)
                target = wrapper
            for node in list(BeautifulSoup(inner, "html.parser").children):
                target.append(node)
            for node in list(BeautifulSoup(_BLOG_FONTS_LINK, "html.parser").children):
                soup.head.append(node)
            return str(soup)
    return _STRIP_DOC_HEAD + inner + _STRIP_DOC_TAIL


def inject_strip(html: str, tenant, request=None, *, blog_base: str = "/blog/", marker_namespace: str = "home") -> str:
    """Place the featured-posts strip into a rendered homepage.

    Into ``[data-blog-strip]`` if the template marks a spot; otherwise
    just before the footer, else at the end of ``<body>``. No featured
    posts → returns the HTML untouched (no empty strip on the public site).
    """
    soup = BeautifulSoup(html, "lxml")
    markers = soup.find_all(attrs={"data-blog-strip": True})
    if markers and not _has_saved_typography(tenant):
        _mount_site_font_aliases(soup, markers)
    site_styled = bool(markers) or _has_saved_typography(tenant)
    fragment = render_strip_fragment(
        tenant, request=request, blog_base=blog_base, site_styled=site_styled,
    )
    if not fragment:
        return html

    # Re-rendering an already composed document must replace the generated
    # strip rather than append another one. Never remove authored blog blocks.
    for previous in soup.select("[data-cms-blog-strip]"):
        previous.decompose()
    if site_styled and "family=Fraunces" not in html:
        head = _ensure_head(soup)
        if head is not None:
            for node in list(BeautifulSoup(_BLOG_FONTS_LINK, "html.parser").children):
                head.append(node)
    frag_soup = BeautifulSoup(fragment, "lxml")
    nodes = list(
        frag_soup.body.children if frag_soup.body else frag_soup.children
    )
    if not nodes:
        return html

    if markers:
        saved = (tenant.blog_settings or {}).get("strip_layouts") or {}
        if not isinstance(saved, dict):
            saved = {}
        fragments = {("", True): nodes}
        global_style = get_blog_settings(tenant)["strip_style"]
        for index, marker in enumerate(markers):
            choice = saved.get(f"{marker_namespace}_{index}", "")
            layout = choice if isinstance(choice, str) and choice in PER_BLOCK_STRIP_LAYOUTS else ""
            selected_style = layout or global_style
            # A mount must explicitly say when its parent owns the heading.
            # Nearby headings can belong to unrelated content or follow it.
            show_header = (
                selected_style != "front_line"
                or marker.get("data-blog-strip-header") != "none"
            )
            cache_key = (layout, show_header)
            if cache_key not in fragments:
                selected_html = render_strip_fragment(
                    tenant, request=request, blog_base=blog_base, layout=layout,
                    site_styled=site_styled, show_header=show_header,
                )
                selected_soup = BeautifulSoup(selected_html, "lxml")
                fragments[cache_key] = list(
                    selected_soup.body.children if selected_soup.body else selected_soup.children
                )
            marker.clear()
            for node in fragments[cache_key]:
                marker.append(BeautifulSoup(str(node), "html.parser"))
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
