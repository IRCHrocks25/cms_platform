"""Curated block palette — catalog, instance helpers, and shell conversion.

The block-instance model lets a client add / reorder / remove / duplicate
agency-designed blocks inside a *region* of an otherwise locked template shell.
This module is the seam between the data model (``BlockType`` +
``Template.allowed_block_types``) and the renderer
(``core.renderer.render_page_from_blocks``):

* ``catalog_for_template`` / ``palette_for_template`` — what a client may insert.
* ``render_content`` — the dual-read dispatcher (block shell vs classic page).
* ``new_instance_id`` / ``seed_instance`` / ``count_region_blocks`` — editing.
* ``split_shell_and_blocks`` / ``convert_content_to_regions`` — one-time
  migration of a classic annotated template + its tenants into the shell model,
  with ``normalize_for_diff`` gating the rollout on byte-identical render.
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import Any, Iterable

logger = logging.getLogger(__name__)

from bs4 import BeautifulSoup, Comment, Tag


# Abuse / performance caps (team decision §12 of the plan).
MAX_PAGES_PER_TENANT = 20
MAX_BLOCKS_PER_PAGE = 40
# How deep block instances may nest (rows-in-columns). Top-level instances are
# depth 0; a row's column children are depth 1; those may hold one more row
# whose children are depth 2. Anything deeper is dropped on save + at render.
MAX_BLOCK_DEPTH = 2

# Sections that stay fixed in the shell during migration — the header nav, the
# footer, and the synthetic brand-tokens section. Everything else in the page
# body becomes an insertable block.
CHROME_GROUPS = {"header", "footer", "global"}
CHROME_IDS = {"nav", "footer", "brand", "header"}
SHELL_REGION_ORDER = (
    "header-left", "header-center", "header-right", "header", "nav",
    "main",
    "footer-left", "footer-center", "footer-right", "footer",
)
# Navbar slots are typed: logo cluster is not a dump zone, the menu takes
# links, the right side takes buttons. Empty allow-list = no new inserts.
HEADER_REGION_BLOCKS = {
    "header-left": (),
    "header-center": ("nav-link",),
    "header-right": ("button",),
    "header": ("button",),
    "nav": ("nav-link",),
}
HEADER_BUTTON_CAP = 2
HEADER_PIECES = ("brand", "nav", "actions")
HEADER_ZONES = ("left", "center", "right")
HEADER_LAYOUTS = ("classic", "packed", "centered")
LOGO_SIZE_MIN = 24
LOGO_SIZE_MAX = 80
DEFAULT_LOGO_SIZE = 40
DEFAULT_HEADER_PLACE = {"brand": "left", "nav": "center", "actions": "right"}
_NAV_ID_RE = re.compile(r"^nav_[a-f0-9]{8}$")
_HEADER_SLOTS = frozenset({
    "header-left", "header-center", "header-right", "header", "nav",
})


def new_nav_id() -> str:
    return "nav_" + secrets.token_hex(4)


def header_place_for_layout(layout: str) -> dict[str, str]:
    if layout == "packed":
        return {"brand": "left", "nav": "right", "actions": "right"}
    if layout == "centered":
        return {"brand": "center", "nav": "center", "actions": "center"}
    return dict(DEFAULT_HEADER_PLACE)


def _safe_logo_src(value: str) -> str:
    raw = str(value or "").strip()[:2000]
    if not raw:
        return ""
    low = raw.lower().replace(" ", "")
    if low.startswith(("javascript:", "vbscript:", "data:text")):
        return ""
    if raw.lower().startswith("data:image/"):
        return raw
    if raw.startswith("/") or raw.lower().startswith(("http://", "https://")):
        return raw
    if ":" not in raw.split("/", 1)[0]:
        return raw
    return ""


def _safe_header_href(value: str) -> str:
    raw = str(value or "").strip()[:500]
    if not raw:
        return "/"
    low = raw.lower().replace(" ", "")
    if low.startswith(("javascript:", "vbscript:", "data:")):
        return "/"
    return raw


def _menu_from_regions(regions: dict | None) -> list[dict]:
    items = []
    if not isinstance(regions, dict):
        return items
    for slot in ("header-center", "header", "nav"):
        for inst in regions.get(slot) or []:
            if not isinstance(inst, dict) or inst.get("type") != "nav-link":
                continue
            fields = inst.get("fields") or {}
            label = str(fields.get("text") or "Link").strip()[:80] or "Link"
            href = _safe_header_href(
                fields.get("text_href") or fields.get("href") or "/"
            )
            items.append({
                "id": new_nav_id(), "label": label, "href": href, "page_id": None,
            })
    return items


def _button_from_regions(regions: dict | None) -> dict | None:
    if not isinstance(regions, dict):
        return None
    for slot in ("header-right", "header"):
        for inst in regions.get(slot) or []:
            if not isinstance(inst, dict) or inst.get("type") != "button":
                continue
            fields = inst.get("fields") or {}
            label = str(fields.get("label") or "Get Started").strip()[:80]
            href = _safe_header_href(fields.get("link") or fields.get("href") or "#")
            return {"on": True, "label": label or "Get Started", "href": href}
    return None


def normalize_header(
    raw,
    *,
    regions: dict | None = None,
    nav_pages: list | None = None,
    pages: list | None = None,
) -> dict:
    """One Header object: layout preset, menu list, optional CTA."""
    raw = raw if isinstance(raw, dict) else {}
    layout = raw.get("layout") if raw.get("layout") in HEADER_LAYOUTS else ""
    if not layout:
        place = normalize_header_place(raw)
        if place["brand"] == "center":
            layout = "centered"
        elif place["nav"] == "right":
            layout = "packed"
        else:
            layout = "classic"

    page_map: dict[int, str] = {}
    for page in pages or []:
        if not isinstance(page, dict) or page.get("id") in (None, ""):
            continue
        try:
            page_map[int(page["id"])] = page.get("url") or "/"
        except (TypeError, ValueError):
            continue

    menu: list[dict] = []
    seen: set[str] = set()
    raw_menu = raw.get("menu")
    if isinstance(raw_menu, list) and raw_menu:
        for item in raw_menu:
            if not isinstance(item, dict):
                continue
            nid = str(item.get("id") or "").strip()
            if not _NAV_ID_RE.match(nid) or nid in seen:
                nid = new_nav_id()
            seen.add(nid)
            label = str(item.get("label") or "Link").strip()[:80] or "Link"
            href = _safe_header_href(item.get("href") or "/")
            page_id = item.get("page_id")
            try:
                page_id = int(page_id) if page_id not in (None, "") else None
            except (TypeError, ValueError):
                page_id = None
            if page_id is not None and page_id in page_map:
                href = _safe_header_href(page_map[page_id])
            menu.append({"id": nid, "label": label, "href": href, "page_id": page_id})
    if not menu:
        menu = _menu_from_regions(regions)
        if not menu and nav_pages:
            for page in nav_pages:
                if not isinstance(page, dict):
                    continue
                menu.append({
                    "id": new_nav_id(),
                    "label": str(page.get("title") or "Page").strip()[:80] or "Page",
                    "href": _safe_header_href(page.get("url") or "/"),
                    "page_id": None,
                })

    button = {"on": False, "label": "Get Started", "href": "#"}
    raw_btn = raw.get("button")
    if isinstance(raw_btn, dict):
        button["on"] = bool(raw_btn.get("on"))
        button["label"] = str(raw_btn.get("label") or "Get Started").strip()[:80] or "Get Started"
        button["href"] = _safe_header_href(raw_btn.get("href") or "#")
    elif not raw_menu:
        migrated = _button_from_regions(regions)
        if migrated:
            button = migrated

    logo = ""
    raw_logo = raw.get("logo")
    raw_size = raw.get("logo_size")
    if isinstance(raw_logo, dict):
        raw_size = raw_logo.get("size", raw_size)
        raw_logo = raw_logo.get("src") or raw_logo.get("url") or ""
    if raw_logo:
        logo = _safe_logo_src(raw_logo)
    try:
        logo_size = int(raw_size)
    except (TypeError, ValueError):
        logo_size = DEFAULT_LOGO_SIZE
    logo_size = max(LOGO_SIZE_MIN, min(LOGO_SIZE_MAX, logo_size))
    if "show_name" in raw:
        show_name = bool(raw.get("show_name"))
    else:
        show_name = not bool(logo)

    return {
        "layout": layout,
        "menu": menu,
        "button": button,
        "logo": logo,
        "logo_size": logo_size,
        "show_name": show_name,
    }


def ensure_header(content: dict) -> dict:
    """Normalize ``_header`` and drop leftover header block instances."""
    if not isinstance(content, dict):
        return {
            "layout": "classic",
            "menu": [],
            "button": {"on": False, "label": "Get Started", "href": "#"},
            "logo": "",
            "logo_size": DEFAULT_LOGO_SIZE,
            "show_name": True,
        }
    header = normalize_header(content.get("_header"), regions=content.get("regions"))
    content["_header"] = header
    regions = content.get("regions")
    if isinstance(regions, dict):
        for slot in _HEADER_SLOTS:
            if slot in regions:
                regions[slot] = []
    return header


def _header_meta_is_customized(meta: dict | None) -> bool:
    """True when the client actually configured the Header panel."""
    if not meta:
        return False
    if meta.get("menu") or meta.get("logo"):
        return True
    btn = meta.get("button") or {}
    if btn.get("on") and btn.get("label"):
        return True
    if meta.get("show_name") is False:
        return True
    if meta.get("layout") in ("packed", "centered"):
        return True
    return False


def _header_has_designed_markup(soup) -> bool:
    """True when the header still carries agency-designed chrome (logo, burger)."""
    header = soup.find("header")
    if header is None:
        return False
    if header.find(class_="burger") or header.find(class_="nav-cta"):
        return True
    if header.find(class_="nav-links") or header.find(class_="brand"):
        return True
    extra = header.find(class_="site-header-brand-extra")
    if extra is not None and extra.find(["img", "a", "button", "nav"]):
        return True
    return False


def should_paint_header(soup, header_meta=None) -> bool:
    """Whether ``apply_header_chrome`` should replace the header contents.

    Blank builder shells get the unified Header panel paint. Designed agency
    pages keep their own navbar unless the client has actually configured
    ``_header``. Headers we never chrome-ified are left alone.
    """
    meta = header_meta if isinstance(header_meta, dict) else normalize_header(header_meta)
    if _header_meta_is_customized(meta):
        return True
    if soup.find(class_="site-header-inner") is None:
        return False
    return not _header_has_designed_markup(soup)


def apply_header_chrome(soup, header_meta=None, *, preview: bool = False) -> None:
    """Paint the navbar from ``_header`` (menu list + optional button)."""
    meta = normalize_header(header_meta)
    if not should_paint_header(soup, meta):
        return
    nav = None
    header = soup.find("header")
    if header is not None:
        nav = header.find(class_="site-nav") or header.find("nav")
    host = None
    if nav is not None:
        host = (
            nav.find(class_="site-nav-links")
            or nav.find(attrs={"data-region": "header-center"})
            or nav.find(attrs={"data-nav-pages": True})
            or nav
        )
        for extra in nav.find_all(attrs={"data-nav-pages": True}):
            if extra is not host:
                extra.clear()
        host.clear()
        for item in meta["menu"]:
            link = soup.new_tag("a", href=item["href"])
            link.string = item["label"]
            link["data-header-link"] = item["id"]
            link["data-type"] = "text"
            host.append(link)
        if preview:
            add = soup.new_tag(
                "button",
                attrs={
                    "type": "button",
                    "class": "cms-chrome-add",
                    "data-header-add-link": "1",
                    "aria-label": "Add link",
                },
            )
            add.string = "+ Add link"
            host.append(add)

    actions = soup.find(class_="site-header-actions")
    if actions is not None:
        actions.clear()
        btn = meta["button"]
        if btn.get("on") and btn.get("label"):
            cta = soup.new_tag("a", href=btn["href"])
            cta["class"] = ["site-header-cta"]
            cta.string = btn["label"]
            cta["data-header-button"] = "1"
            actions.append(cta)
        elif preview:
            add = soup.new_tag(
                "button",
                attrs={
                    "type": "button",
                    "class": "cms-chrome-add",
                    "data-header-add-button": "1",
                    "aria-label": "Add button",
                },
            )
            add.string = "+ Add button"
            actions.append(add)

    apply_header_layout(soup, {"place": header_place_for_layout(meta["layout"])})
    inner = soup.find(class_="site-header-inner") if soup.find("header") else None
    if inner is not None:
        inner["data-header-layout"] = meta["layout"]
    _apply_header_logo(soup, meta)
    _apply_header_name(soup, meta)


def _apply_header_logo(soup, meta: dict) -> None:
    """Put the logo image beside the wordmark so text apply cannot wipe it."""
    src = (meta or {}).get("logo") or ""
    if not src:
        return
    wrap = soup.find(class_="site-header-brand")
    wordmark = soup.find(class_="site-brand")
    if wordmark is None and wrap is not None:
        wordmark = wrap.find(attrs={"data-edit": re.compile(r"\.brand$")})
    host = wrap or wordmark
    if host is None:
        return
    for old in host.find_all(class_="site-header-logo"):
        old.decompose()
    img = soup.new_tag("img", src=src)
    img["class"] = ["site-header-logo"]
    img["alt"] = (wordmark.get_text(" ", strip=True) if wordmark else "") or "Logo"
    img["data-header-logo"] = "1"
    size = meta.get("logo_size") or DEFAULT_LOGO_SIZE
    try:
        size = max(LOGO_SIZE_MIN, min(LOGO_SIZE_MAX, int(size)))
    except (TypeError, ValueError):
        size = DEFAULT_LOGO_SIZE
    img["style"] = f"height:{size}px;width:auto;max-width:min(40vw,{size * 5}px);"
    classes = host.get("class") or []
    if "has-logo" not in classes:
        host["class"] = list(classes) + ["has-logo"]
    if wordmark is not None and wordmark.parent is not None:
        wordmark.insert_before(img)
    else:
        host.insert(0, img)


_BRAND_EDIT_RE = re.compile(r"\.brand$")
_HIDE_NAME_STYLE = "display:none!important"


def _header_wordmarks(soup):
    """Every site-name node in the header (class or data-edit *.brand)."""
    header = soup.find("header")
    scope = header or soup
    found = []
    seen = set()
    for el in scope.find_all(attrs={"data-edit": _BRAND_EDIT_RE}):
        found.append(el)
        seen.add(id(el))
    for el in scope.find_all(class_="site-brand"):
        if id(el) not in seen:
            found.append(el)
    return found


def _apply_header_name(soup, meta: dict) -> None:
    """Show or hide the site-name wordmark independently of the logo image."""
    show = bool((meta or {}).get("show_name", True))
    wrap = soup.find(class_="site-header-brand")
    wordmarks = _header_wordmarks(soup)
    hosts = []
    if wrap is not None:
        hosts.append(wrap)
    hosts.extend(wordmarks)
    seen = set()
    for host in hosts:
        if host is None or id(host) in seen:
            continue
        seen.add(id(host))
        classes = [c for c in (host.get("class") or []) if c != "hide-name"]
        if not show:
            classes.append("hide-name")
        host["class"] = classes
        if host in wordmarks:
            if show:
                host.attrs.pop("hidden", None)
                if host.get("data-header-name") == "off":
                    del host["data-header-name"]
                style = (host.get("style") or "").replace(_HIDE_NAME_STYLE, "").strip("; ")
                if style:
                    host["style"] = style
                elif host.has_attr("style"):
                    del host["style"]
            else:
                host["hidden"] = True
                host["data-header-name"] = "off"
                style = host.get("style") or ""
                if "display:none" not in style.replace(" ", "").lower():
                    host["style"] = (style + ";" if style else "") + _HIDE_NAME_STYLE


def normalize_header_place(raw) -> dict[str, str]:
    """Keep each navbar piece in one of left / center / right."""
    out = dict(DEFAULT_HEADER_PLACE)
    place = raw
    if isinstance(raw, dict) and isinstance(raw.get("place"), dict):
        place = raw["place"]
    if isinstance(place, dict):
        for piece in HEADER_PIECES:
            zone = str(place.get(piece) or "").strip()
            if zone in HEADER_ZONES:
                out[piece] = zone
    return out


def apply_header_layout(soup, header_meta=None) -> None:
    """Put brand / menu / buttons into the three header zones."""
    header = soup.find("header")
    if header is None:
        return
    inner = header.find(class_="site-header-inner")
    if inner is None:
        return
    place = normalize_header_place(header_meta)
    brand = inner.find(class_="site-header-brand")
    nav = inner.find("nav")
    actions = inner.find(class_="site-header-actions")
    if brand is not None:
        brand["data-chrome-piece"] = "brand"
    if nav is not None:
        nav["data-chrome-piece"] = "nav"
        classes = nav.get("class") or []
        if "site-nav" not in classes:
            nav["class"] = list(classes) + ["site-nav"]
    if actions is not None:
        actions["data-chrome-piece"] = "actions"
    pieces = {"brand": brand, "nav": nav, "actions": actions}
    for el in pieces.values():
        if el is not None:
            el.extract()
    zones = {}
    for name in HEADER_ZONES:
        zone = soup.new_tag(
            "div", attrs={"class": "site-header-zone", "data-header-zone": name}
        )
        zones[name] = zone
    for piece, el in pieces.items():
        if el is not None:
            zones[place[piece]].append(el)
    inner.clear()
    for name in HEADER_ZONES:
        inner.append(zones[name])


def shell_region_names(html: str | None) -> list[str]:
    """Top-level ``data-region`` slots on a block shell (header / main / footer)."""
    names = block_region_names(html)
    if not names:
        return ["main"]
    ordered = [n for n in SHELL_REGION_ORDER if n in names]
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


_NAVBAR_CSS = (
    ".site-header{width:100%;border-bottom:1px solid #e5e7eb;"
    "background:var(--bg,#fff);padding:0;}"
    ".site-header-inner{width:100%;max-width:1120px;margin:0 auto;"
    "padding:14px 24px;display:grid;"
    "grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);"
    "align-items:center;gap:16px;min-height:64px;box-sizing:border-box;}"
    ".site-header-zone{display:flex;align-items:center;gap:16px;"
    "min-width:0;min-height:36px;}"
    ".site-header-zone[data-header-zone=left]{justify-content:flex-start;}"
    ".site-header-zone[data-header-zone=center]{justify-content:center;}"
    ".site-header-zone[data-header-zone=right]{justify-content:flex-end;}"
    ".site-header-brand{display:flex;align-items:center;gap:10px;flex:0 0 auto;}"
    ".site-brand{font-weight:700;font-size:20px;color:var(--text,#172033);"
    "text-decoration:none;white-space:nowrap;}"
    ".site-header-logo{display:block;height:40px;width:auto;max-width:200px;"
    "object-fit:contain;}"
    ".site-header-brand.hide-name .site-brand,"
    ".site-header-brand.hide-name [data-edit$='.brand'],"
    ".site-brand.hide-name{display:none!important;}"
    ".site-header-brand.has-logo:not(.hide-name){display:flex;align-items:center;gap:10px;}"
    ".site-nav,.site-nav-pages,.site-nav-links{display:flex;align-items:center;"
    "flex-wrap:wrap;gap:4px 18px;min-width:0;}"
    ".site-nav{justify-content:center;}"
    ".site-nav a,.site-nav-links a{color:inherit;text-decoration:none;font-weight:600;"
    "font-size:15px;line-height:1.2;padding:6px 8px;}"
    ".site-header [data-instance-id]{padding:2px 4px;}"
    ".site-header-actions{display:flex;align-items:center;justify-content:flex-end;"
    "gap:10px;}"
    ".site-header-cta{display:inline-flex;align-items:center;padding:10px 18px;"
    "background:var(--primary,#2563eb);color:#fff;border-radius:8px;"
    "text-decoration:none;font-weight:600;font-size:14px;}"
    ".site-header-inner[data-header-layout=packed]{"
    "grid-template-columns:auto minmax(0,1fr);}"
    ".site-header-inner[data-header-layout=packed] "
    "[data-header-zone=center]{display:none;}"
    ".site-header-inner[data-header-layout=packed] "
    "[data-header-zone=right]{justify-content:flex-end;gap:20px;}"
    ".site-header-inner[data-header-layout=centered]{"
    "grid-template-columns:1fr;justify-items:center;}"
    ".site-header-inner[data-header-layout=centered] "
    "[data-header-zone=left],"
    ".site-header-inner[data-header-layout=centered] "
    "[data-header-zone=right]{display:none;}"
    ".site-header-inner[data-header-layout=centered] "
    "[data-header-zone=center]{flex-wrap:wrap;justify-content:center;}"
    ".site-header-brand-extra:empty{display:none;}"
    ".site-header [data-instance-id]{display:inline-flex;align-items:center;}"
    ".site-header [data-instance-id]>div{max-width:none!important;"
    "padding:0!important;margin:0!important;}"
    ".site-header-actions [data-edit$='.subtext']:empty{display:none;}"
    ".site-footer-row{display:grid;"
    "grid-template-columns:minmax(0,1fr) minmax(0,1.3fr) minmax(0,1fr);"
    "gap:16px 24px;align-items:center;width:100%;}"
    ".site-footer-col{display:flex;flex-wrap:wrap;align-items:center;"
    "gap:8px 14px;min-width:0;}"
    ".site-footer-col--center{justify-content:center;flex-direction:column;"
    "text-align:center;}"
    ".site-footer-col>[data-instance-id]>div,"
    "[data-region^=footer]>[data-instance-id]>div{"
    "max-width:none!important;padding-left:0!important;"
    "padding-right:0!important;margin:0!important;}"
)


def _emit_shell_fragment(soup, original: str) -> str:
    if "<html" not in original.lower() and soup.body is not None:
        return soup.body.decode_contents()
    return str(soup)


_NAVBAR_ZONE_CSS = (
    ".site-header-inner{width:100%!important;display:grid!important;"
    "grid-template-columns:minmax(0,1fr) auto minmax(0,1fr)!important;"
    "align-items:center;gap:16px;box-sizing:border-box;}"
    ".site-header-zone{display:flex;align-items:center;gap:16px;"
    "min-width:0;min-height:36px;}"
    ".site-header-zone[data-header-zone=left]{justify-content:flex-start;}"
    ".site-header-zone[data-header-zone=center]{justify-content:center;}"
    ".site-header-zone[data-header-zone=right]{justify-content:flex-end;}"
    ".site-header-brand,.site-nav,.site-header-actions{margin-left:0;margin-right:0;}"
)


def _ensure_navbar_css(html: str) -> str:
    compact = html.replace(" ", "")
    extra = ""
    if ".site-header-inner{" not in compact:
        extra += _NAVBAR_CSS
    if ".site-header-zone{" not in compact:
        extra += _NAVBAR_ZONE_CSS
    if "cms-nav-read" not in html:
        extra += (
            "/*cms-nav-read*/.site-nav a,.site-nav-links a{padding:6px 8px;}"
            ".site-header [data-instance-id]{padding:2px 4px;}"
        )
    if ".site-header-logo{" not in html.replace(" ", ""):
        extra += (
            ".site-header-logo{display:block;height:40px;width:auto;max-width:200px;"
            "object-fit:contain;}"
        )
    if ".hide-name{" not in compact:
        extra += (
            ".site-header-brand.hide-name .site-brand,"
            ".site-header-brand.hide-name [data-edit$='.brand'],"
            ".site-brand.hide-name{display:none!important;}"
            ".site-header-brand.has-logo:not(.hide-name){display:flex;align-items:center;gap:10px;}"
        )
    if "data-header-layout=packed" not in html.replace(" ", ""):
        extra += (
            ".site-header-inner[data-header-layout=packed]{"
            "grid-template-columns:auto minmax(0,1fr)!important;}"
            ".site-header-inner[data-header-layout=packed] "
            "[data-header-zone=center]{display:none;}"
            ".site-header-inner[data-header-layout=packed] "
            "[data-header-zone=right]{justify-content:flex-end;gap:20px;}"
            ".site-header-inner[data-header-layout=centered]{"
            "grid-template-columns:1fr!important;justify-items:center;}"
            ".site-header-inner[data-header-layout=centered] "
            "[data-header-zone=left],"
            ".site-header-inner[data-header-layout=centered] "
            "[data-header-zone=right]{display:none;}"
            ".site-header-inner[data-header-layout=centered] "
            "[data-header-zone=center]{flex-wrap:wrap;justify-content:center;}"
        )
    if not extra:
        return html
    if "</style>" in html:
        return html.replace("</style>", extra + "\n</style>", 1)
    return html + f"\n<style>{extra}</style>"


# A designed page's CSS often uses `body > section` / `.page > .sec`. The
# block-editor slot is an extra wrapper; `display:contents` keeps that CSS
# matching the original box tree once instances are filled in. Empty preview
# slots keep a real box so the dashed "+" placeholder still lays out.
_REGION_LAYOUT_CSS = (
    "[data-region=main]:not([data-empty-region]){display:contents}"
)


def _ensure_region_layout_css(html: str) -> str:
    compact = html.replace(" ", "")
    if "[data-region=main]:not([data-empty-region])" in compact:
        return html
    if "</style>" in html:
        return html.replace("</style>", _REGION_LAYOUT_CSS + "\n</style>", 1)
    return html + f"\n<style>{_REGION_LAYOUT_CSS}</style>"


def _rebuild_header_navbar(soup, header) -> bool:
    """Turn dump-column / bare headers into logo | menu | button chrome."""
    if header.find(class_="site-header-inner"):
        return False
    brand = header.find(class_="site-brand") or header.find(
        attrs={"data-edit": re.compile(r"\.brand$")}
    )
    nav = header.find("nav") or header.find(attrs={"data-nav-pages": True})
    left_slot = header.find(attrs={"data-region": "header-left"})
    center_slot = header.find(attrs={"data-region": "header-center"})
    right_slot = (
        header.find(attrs={"data-region": "header-right"})
        or header.find(attrs={"data-region": "header"})
    )
    skip = {brand, nav, left_slot, center_slot, right_slot}
    skip.update(header.find_all(class_=re.compile(r"site-header-(row|col)")))
    leftovers = [
        child for child in list(header.contents)
        if child not in skip
        and not (getattr(child, "name", None) is None and not str(child).strip())
    ]
    left_kids = list(left_slot.contents) if left_slot else []
    center_kids = list(center_slot.contents) if center_slot else []
    right_kids = list(right_slot.contents) if right_slot else []
    if brand:
        brand.extract()
    header.clear()

    inner = soup.new_tag("div", attrs={"class": "site-header-inner"})
    brand_wrap = soup.new_tag("div", attrs={"class": "site-header-brand"})
    if brand:
        brand_wrap.append(brand)
    extra = soup.new_tag(
        "div", attrs={"class": "site-header-brand-extra", "data-region": "header-left"}
    )
    for child in left_kids:
        extra.append(child)
    for leftover in leftovers:
        extra.append(leftover)
    brand_wrap.append(extra)

    nav_el = soup.new_tag("nav", attrs={"class": "site-nav"})
    pages = soup.new_tag("div", attrs={"class": "site-nav-pages", "data-nav-pages": ""})
    links = soup.new_tag(
        "div", attrs={"class": "site-nav-links", "data-region": "header-center"}
    )
    for child in center_kids:
        links.append(child)
    nav_el.append(pages)
    nav_el.append(links)

    actions = soup.new_tag(
        "div", attrs={"class": "site-header-actions", "data-region": "header-right"}
    )
    for child in right_kids:
        actions.append(child)

    inner.append(brand_wrap)
    inner.append(nav_el)
    inner.append(actions)
    header.append(inner)
    return True


def upgrade_shell_chrome_slots(html: str) -> str:
    """Turn header/footer dump slots into navbar chrome + a 3-column footer."""
    if not html:
        return html

    soup = BeautifulSoup(html, "lxml")
    changed = False

    header = soup.find("header")
    if header is not None:
        if _rebuild_header_navbar(soup, header):
            changed = True
        if header.find(class_="site-header-inner") and not header.find(
            attrs={"data-header-zone": True}
        ):
            apply_header_layout(soup)
            changed = True

    footer = soup.find("footer")
    if footer is not None and not footer.find(attrs={"data-region": "footer-left"}):
        copy = footer.find(attrs={"data-edit": re.compile(r"footer\.")})
        extra = footer.find(attrs={"data-region": "footer"})
        leftovers = [
            child for child in list(footer.contents)
            if child not in (copy, extra)
            and not (getattr(child, "name", None) is None and not str(child).strip())
        ]
        footer.clear()
        row = soup.new_tag("div", attrs={"class": "site-footer-row"})
        left = soup.new_tag("div", attrs={"class": "site-footer-col", "data-region": "footer-left"})
        center = soup.new_tag("div", attrs={"class": "site-footer-col site-footer-col--center"})
        center_slot = soup.new_tag("div", attrs={"data-region": "footer-center"})
        center.append(center_slot)
        if copy:
            center.append(copy)
        right = soup.new_tag("div", attrs={"class": "site-footer-col", "data-region": "footer-right"})
        row.append(left)
        row.append(center)
        row.append(right)
        footer.append(row)
        for leftover in leftovers:
            center_slot.append(leftover)
        changed = True

    if changed:
        html = _emit_shell_fragment(soup, html)
    return _ensure_navbar_css(html)


def alias_chrome_regions(regions: dict | None) -> dict:
    """Map legacy ``header`` / ``footer`` instance lists onto the 3-column slots."""
    out = dict(regions or {})
    if out.get("header") and not out.get("header-right"):
        out["header-right"] = out["header"]
    if out.get("footer") and not out.get("footer-center"):
        out["footer-center"] = out["footer"]
    return out


# --------------------------------------------------------------------------- #
# Catalog / palette                                                            #
# --------------------------------------------------------------------------- #


def block_region_names(html: str | None) -> list[str]:
    """Column/region slot names declared inside a block fragment.

    A *layout* block (e.g. a 2-column row) carries nested ``data-region``
    slots where the client drops child instances. A leaf block (headline,
    image, ...) has none. Order is preserved and de-duplicated.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    names: list[str] = []
    for el in soup.find_all(attrs={"data-region": True}):
        name = (el.get("data-region") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def build_catalog(block_types: Iterable) -> dict[str, dict]:
    """Map a block key to the data the renderer needs (schema + html)."""
    catalog: dict[str, dict] = {}
    for bt in block_types:
        catalog[bt.key] = {
            "key": bt.key,
            "label": bt.label,
            "icon": bt.icon,
            "category": bt.category,
            "schema": bt.schema or {},
            "html": bt.html_source or "",
            "regions": block_region_names(bt.html_source),
        }
    return catalog


def catalog_for_template(template) -> dict[str, dict]:
    """The render-time catalog: only this template's allowlisted, active blocks."""
    if template is None or template.pk is None:
        return {}
    return build_catalog(template.allowed_block_types.filter(is_active=True))


def palette_for_template(template) -> list[dict]:
    """Editor-facing palette entries (no HTML), grouped-friendly + searchable."""
    if template is None or template.pk is None:
        return []
    items = []
    for bt in template.allowed_block_types.filter(is_active=True).order_by(
        "category", "label"
    ):
        regions = block_region_names(bt.html_source)
        items.append(
            {
                "key": bt.key,
                "label": bt.label,
                "icon": bt.icon,
                "category": bt.category,
                "fields": len((bt.schema or {}).get("fields") or []),
                "regions": regions,
                "is_layout": bool(regions),
            }
        )
    return items


# --------------------------------------------------------------------------- #
# Instance helpers                                                             #
# --------------------------------------------------------------------------- #


def new_instance_id() -> str:
    """A fresh, collision-resistant block-instance id. Never reuse an id on
    add/duplicate (see gotcha §14.3 of the plan)."""
    return "blk_" + secrets.token_hex(4)


def seed_instance(block_key: str, catalog: dict[str, dict] | None = None) -> dict:
    """A new instance seeded from a block type — empty fields fall back to the
    block's defaults at render time via ``merge_block_defaults``. Layout blocks
    (rows with column slots) get empty ``children`` lists so the client can drop
    blocks into each column."""
    inst = {"id": new_instance_id(), "type": block_key, "fields": {}}
    entry = (catalog or {}).get(block_key)
    regions = (entry or {}).get("regions") or []
    if regions:
        inst["children"] = {name: [] for name in regions}
    return inst


def clone_instance_tree(inst: dict) -> dict | None:
    """Deep-copy one block instance, minting fresh ids all the way down.

    Used when copying a page's blocks to a new page (copy_home / copy_page): a
    shallow ``{type, fields}`` copy loses every nested column child, so a home
    page built from layout rows would arrive empty. This recurses into
    ``children`` (column slots) and gives every instance — parent and child — a
    new id so the two pages stay fully independent."""
    if not isinstance(inst, dict) or not inst.get("type"):
        return None
    out = {
        "id": new_instance_id(),
        "type": inst["type"],
        "fields": dict(inst.get("fields") or {}),
    }
    children = inst.get("children")
    if isinstance(children, dict):
        cloned_children: dict[str, list] = {}
        for slot_name, child_list in children.items():
            if not isinstance(child_list, list):
                continue
            cloned = [
                c for c in (clone_instance_tree(child) for child in child_list)
                if c is not None
            ]
            cloned_children[slot_name] = cloned
        if cloned_children:
            out["children"] = cloned_children
    return out


def _iter_instances(instances):
    """Depth-first walk over an instance list, yielding every nested instance."""
    for inst in instances or []:
        if not isinstance(inst, dict):
            continue
        yield inst
        children = inst.get("children")
        if isinstance(children, dict):
            for child_list in children.values():
                yield from _iter_instances(child_list)


def count_region_blocks(content: dict | None) -> int:
    """Total block instances on a page, counting every nested child."""
    regions = (content or {}).get("regions") or {}
    total = 0
    for instances in regions.values():
        if isinstance(instances, list):
            total += sum(1 for _ in _iter_instances(instances))
    return total


def nav_pages_for(tenant) -> list[dict]:
    """Published, in-menu pages for a tenant, ordered for the site menu.

    Returned as ``[{"title", "url"}]`` so the renderer can fill an opt-in
    ``[data-nav-pages]`` container in the shell. Empty when the tenant has no
    such pages — the renderer then leaves the container untouched.
    """
    if tenant is None or tenant.pk is None:
        return []
    pages = tenant.pages.filter(is_published=True, show_in_nav=True).order_by(
        "nav_order", "title"
    )
    return [{"title": p.title, "url": f"/{p.slug}/"} for p in pages]


def editor_header_pages(tenant) -> list[dict]:
    """Pages the Header panel can link to (home + every tenant page)."""
    rows = [{"id": None, "title": "Home", "url": "/"}]
    if tenant is None or tenant.pk is None:
        return rows
    for page in tenant.pages.order_by("nav_order", "title"):
        rows.append({"id": page.pk, "title": page.title, "url": f"/{page.slug}/"})
    return rows


# --------------------------------------------------------------------------- #
# Dual-read render dispatcher                                                  #
# --------------------------------------------------------------------------- #


def render_content(
    template, content, *, preview: bool = False, site_settings=None, nav_pages=None
) -> str:
    """Render an editable (tenant home or Page) picking block vs classic.

    Block shells (``data-region`` present, or content already carries
    ``regions``) go through ``render_page_from_blocks`` with the template's
    catalog; everything else keeps the classic merge-then-render path, so live
    sites are untouched until they are explicitly converted. ``nav_pages`` (see
    ``nav_pages_for``) fills an opt-in ``[data-nav-pages]`` menu container.
    """
    from core.renderer import (
        merge_with_defaults,
        render_page_from_blocks,
        render_site,
    )

    content = content or {}
    if template is None or not template.html_source:
        return ""

    if template.is_block_shell or content.get("regions"):
        return render_page_from_blocks(
            template.html_source,
            content,
            catalog_for_template(template),
            preview=preview,
            site_settings=site_settings,
            nav_pages=nav_pages,
        )

    merged = merge_with_defaults(template.schema, content)
    return render_site(
        template.html_source, merged, preview=preview, site_settings=site_settings
    )


# --------------------------------------------------------------------------- #
# Migration: classic annotated page -> shell + blocks                          #
# --------------------------------------------------------------------------- #


def _is_chrome(section_el) -> bool:
    group = (section_el.get("data-group") or "").lower()
    sid = (section_el.get("data-section") or "").strip()
    return group in CHROME_GROUPS or sid in CHROME_IDS


_SHELL_TAGS = frozenset({"header", "footer", "script", "style", "noscript"})
_OVERLAY_TOKENS = frozenset({"menu", "modal"})


def _node_tokens(node) -> set[str]:
    raw = " ".join(node.get("class") or [])
    raw += " " + (node.get("id") or "")
    return set(re.findall(r"[a-z0-9]+", raw.lower()))


def _is_overlay_chrome(node) -> bool:
    return bool(_node_tokens(node) & _OVERLAY_TOKENS)


def _is_body_block(node) -> bool:
    """True for a top-level body child that should become an insertable block.

    Nested ``data-section`` nodes (e.g. a testimonial slide inside Proof) stay
    inside their parent. Unannotated scaffolding (modals, mobile menus) and
    header/footer chrome stay in the shell.
    """
    if not isinstance(node, Tag):
        return False
    if node.name in _SHELL_TAGS:
        return False
    if _is_chrome(node) or _is_overlay_chrome(node):
        return False
    return bool((node.get("data-section") or "").strip())


def split_shell_and_blocks(html: str, *, region: str = "main") -> tuple[str, list[tuple[str, str]]]:
    """Split a classic annotated page into (shell_html, [(key, fragment_html)]).

    Only *top-level* body children with ``data-section`` become blocks.
    Header/footer stay in the shell. The span from the first body block to
    the last — including divider comments and whitespace between them — is
    replaced by a single ``<div data-region="...">``. Nested sections stay
    inside their parent fragment.
    """
    soup = BeautifulSoup(html or "", "lxml")
    host = soup.body
    if host is None:
        sections = [
            s for s in soup.find_all(attrs={"data-section": True})
            if not _is_chrome(s) and not _is_overlay_chrome(s)
        ]
        fragments = []
        if not sections:
            return _ensure_region_layout_css(str(soup)), fragments
        slot = soup.new_tag("div")
        slot["data-region"] = region
        sections[0].insert_before(slot)
        for section_el in sections:
            key = (section_el.get("data-section") or "").strip()
            if key:
                fragments.append((key, str(section_el)))
            section_el.extract()
        return _ensure_region_layout_css(str(soup)), fragments

    children = list(host.children)
    block_indices = [i for i, node in enumerate(children) if _is_body_block(node)]
    fragments: list[tuple[str, str]] = []
    if not block_indices:
        slot = soup.new_tag("div")
        slot["data-region"] = region
        host.append(slot)
        return _ensure_region_layout_css(str(soup)), fragments

    first_i, last_i = block_indices[0], block_indices[-1]
    slot = soup.new_tag("div")
    slot["data-region"] = region
    children[first_i].insert_before(slot)
    for i in range(first_i, last_i + 1):
        node = children[i]
        if _is_body_block(node):
            key = (node.get("data-section") or "").strip()
            if key:
                fragments.append((key, str(node)))
        node.decompose()
    return _ensure_region_layout_css(str(soup)), fragments


def convert_content_to_regions(
    content: dict | None,
    ordered_block_keys: list[str],
    *,
    region: str = "main",
) -> dict:
    """Convert classic ``{section: {field}}`` content into the region model.

    Body sections become ordered instances (instance id == original section id,
    which is unique and keeps ``_styles`` / ``_hidden`` keys valid without a
    rewrite). Chrome sections and ``_``-prefixed meta are copied verbatim.
    """
    content = content or {}
    # A previous convert attempt may have left ``regions`` / ``_classic`` on a
    # still-classic template. Rebuild instances from the current section keys
    # (and from ``_classic`` field values when that backup has any).
    source = content
    backup = content.get("_classic")
    if isinstance(backup, dict) and backup:
        source = backup
    body_keys = set(ordered_block_keys)
    new_content: dict[str, Any] = {}
    instances = []
    for key in ordered_block_keys:
        instances.append(
            {"id": key, "type": key, "fields": dict(source.get(key) or {})}
        )
    new_content["regions"] = {region: instances}
    for section_id, value in source.items():
        if section_id in body_keys or section_id in ("regions", "_classic"):
            continue  # rebuilt above, or leftover from a prior convert
        new_content[section_id] = value
    return new_content


_DIFF_STRIP_ATTRS = ("data-instance-id", "data-block-type")


def normalize_for_diff(html: str) -> str:
    """Normalize rendered HTML so a block render can be compared to the classic
    render it replaced: drop instance-marker attributes and unwrap region
    containers. Byte-identical output after this gates the migration rollout
    (plan §9.4)."""
    soup = BeautifulSoup(html or "", "lxml")
    for region_el in soup.find_all(attrs={"data-region": True}):
        region_el.unwrap()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for el in soup.find_all(True):
        for attr in _DIFF_STRIP_ATTRS:
            if el.has_attr(attr):
                del el[attr]
    # Assembling instances drops the source whitespace text nodes that sit
    # between fixed sections, so collapse inter-tag whitespace on BOTH sides —
    # it is not visually meaningful for these block-level sections and would
    # otherwise mask an otherwise byte-identical render.
    out = str(soup)
    out = out.replace(_REGION_LAYOUT_CSS, "")
    out = re.sub(r">\s+<", "><", out)
    out = re.sub(r"\s+", " ", out).strip()
    # The region shim is conversion-only CSS; it must not fail the paint gate.
    out = out.replace(_REGION_LAYOUT_CSS.replace(" ", ""), "")
    return re.sub(r"\s+", " ", out).strip()


def _first_diff_snippet(left: str, right: str, radius: int = 120) -> str:
    if left == right:
        return ""
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    start = max(0, index - radius)
    return (
        f"at {index}: classic=...{left[start:index + radius]}... "
        f"block=...{right[start:index + radius]}..."
    )


def preview_classic_upgrade(
    html: str,
    content: dict | None = None,
    *,
    region: str = "main",
) -> tuple[bool, str]:
    """Dry-run classic vs block render. Return ``(matches, diff_snippet)``."""
    from core.parser import build_schema
    from core.renderer import merge_with_defaults, render_page_from_blocks, render_site

    content = content or {}
    shell_html, fragments = split_shell_and_blocks(html, region=region)
    if not fragments:
        return False, "no body sections to convert"
    schema = build_schema(html)
    classic = render_site(html, merge_with_defaults(schema, content))
    block_keys = [k for k, _ in fragments]
    catalog = _catalog_from_fragments(fragments)
    new_content = convert_content_to_regions(content, block_keys, region=region)
    block_out = render_page_from_blocks(shell_html, new_content, catalog)
    left = normalize_for_diff(classic)
    right = normalize_for_diff(block_out)
    if left == right:
        return True, ""
    return False, _first_diff_snippet(left, right)


def classic_upgrade_is_safe(template, *, region: str = "main") -> tuple[bool, str]:
    """Whether every page on ``template`` still matches after a dry-run convert."""
    from core.models import Page, Tenant

    html = template.html_source or ""
    editables: list = list(Tenant.objects.filter(template=template))
    editables += list(Page.objects.filter(template=template))
    if not editables:
        return preview_classic_upgrade(html, {}, region=region)
    for editable in editables:
        ok, snippet = preview_classic_upgrade(
            html, editable.content or {}, region=region
        )
        if not ok:
            return False, snippet
    return True, ""


def _catalog_from_fragments(fragments: list[tuple[str, str]]) -> dict[str, dict]:
    from core.parser import build_block_schema

    catalog: dict[str, dict] = {}
    for key, frag in fragments:
        schema = build_block_schema(frag)
        catalog[key] = {
            "key": key,
            "label": schema.get("label") or key,
            "icon": schema.get("icon") or "square",
            "category": schema.get("category") or "General",
            "schema": schema,
            "html": frag,
        }
    return catalog


def attach_builder_primitives(template) -> None:
    """Allowlist the shared primitive catalog on a shell (idempotent).

    Does not rewrite the template HTML. Designed agency pages keep their own
    header/footer; blank builder shells already ship with chrome slots.
    """
    if template is None or not template.is_block_shell:
        return
    from core.management.commands.seed_builder_blocks import seed_block_types

    block_types, _created, _updated = seed_block_types()
    if block_types:
        template.allowed_block_types.add(*block_types)


def apply_classic_upgrade(template, *, region: str = "main") -> None:
    """Convert a classic annotated template and every page that uses it.

    Chrome stays in the shell; body sections become allowlisted blocks and
    each tenant/page's ``{section: fields}`` content is rewritten into
    ``regions``. Original content is kept under ``_classic`` for rollback.
    """
    from django.db import transaction

    from core.models import BlockType, Page, Tenant
    from core.parser import build_block_schema

    if template.is_block_shell:
        attach_builder_primitives(template)
        return

    old_html = template.html_source or ""
    shell_html, fragments = split_shell_and_blocks(old_html, region=region)
    if not fragments:
        soup = BeautifulSoup(old_html, "lxml")
        host = soup.body or soup
        slot = soup.new_tag("div")
        slot["data-region"] = region
        host.append(slot)
        shell_html = _ensure_region_layout_css(str(soup))

    from core.management.commands.seed_builder_blocks import BUILDER_BLOCKS

    resolved: list[tuple[str, str]] = []
    for key, frag in fragments:
        bt_key = f"{key}_section" if key in BUILDER_BLOCKS else key
        resolved.append((bt_key, frag))
    block_keys = [k for k, _ in resolved]
    editables = list(Tenant.objects.filter(template=template))
    editables += list(Page.objects.filter(template=template))
    conversions = [
        (ed, convert_content_to_regions(ed.content or {}, block_keys, region=region))
        for ed in editables
    ]

    with transaction.atomic():
        block_types = []
        for key, frag in resolved:
            schema = build_block_schema(frag)
            bt, _ = BlockType.objects.get_or_create(
                key=key,
                defaults={
                    "html_source": frag,
                    "label": schema.get("label") or key,
                },
            )
            bt.html_source = frag
            bt.label = schema.get("label") or bt.label or key
            bt.icon = schema.get("icon") or bt.icon or "square"
            bt.category = schema.get("category") or bt.category or "General"
            bt.save()
            block_types.append(bt)

        template.html_source = shell_html
        template.save()
        if block_types:
            template.allowed_block_types.add(*block_types)
        attach_builder_primitives(template)

        for ed, new_content in conversions:
            new_content["_classic"] = ed.content or {}
            ed.content = new_content
            ed.save(update_fields=["content"])


def _clone_shared_template(template, tenant, user=None):
    """Never mutate a library/shared template in place (A1)."""
    from core.models import Page, Tenant

    owned = template.tenant_id == tenant.pk
    others = Tenant.objects.filter(template=template).exclude(pk=tenant.pk).exists()
    if owned and not others:
        return template

    clone = template.clone_for(tenant, user=user)
    tenant.template = clone
    tenant.save(update_fields=["template"])
    Page.objects.filter(tenant=tenant, template=template).update(template=clone)
    return clone


def ensure_block_editor(editable, *, user=None):
    """Give a classic site the block editor the first time someone opens it.

    Body sections become insertable blocks (so Add section / Quick Add appear)
    and existing copy is dual-written to ``_classic``. Header/footer stay in
    the designed HTML — chrome slots are not rewritten here. A shared/library
    template is cloned onto this tenant first so other sites keep their locked
    HTML until they open the editor themselves.
    """
    from core.models import Page, Tenant

    template = getattr(editable, "template", None)
    if template is None or not (template.html_source or "").strip():
        return template

    tenant = editable if isinstance(editable, Tenant) else editable.tenant
    if not template.is_block_shell:
        template = _clone_shared_template(template, tenant, user)
        if isinstance(editable, Tenant) and editable.template_id != template.pk:
            editable.template = template
        if isinstance(editable, Page) and editable.template_id != template.pk:
            editable.template = template
            editable.save(update_fields=["template"])
        ok, snippet = classic_upgrade_is_safe(template)
        if not ok:
            logger.warning(
                "ensure_block_editor: skipped convert for template=%s; "
                "classic vs block render differs (%s)",
                template.pk,
                snippet[:500],
            )
            return template
        apply_classic_upgrade(template)
        template.refresh_from_db()
    else:
        attach_builder_primitives(template)
    return template
