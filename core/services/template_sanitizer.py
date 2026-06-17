"""
Renderer-side richtext sanitizer for agency-annotated templates.

This is the permissive sibling of ``core/services/sanitizer.py``. The
blog sanitizer is built for untrusted contenteditable input (a small
allowlist, no class/style/data-*, span/div/h1/h5/h6 all unwrapped) —
applying it to agency-authored template HTML that uses utility classes
and structural wrappers visibly destroys the design every render.

Trust model:

- Template HTML is uploaded by the agency, then AI-annotated. Defaults
  for every field are extracted from this trusted source by the parser.
- Tenant richtext edits flow back through this sanitizer too, so the
  filter still has to defend against ``<script>``, event handlers, and
  ``javascript:`` URLs. Clients can't paste arbitrary HTML safely.

The two demands together: preserve every class, id, style, data-*,
ARIA, and structural tag the agency put there; still remove the small,
well-known XSS surface that a malicious tenant could try to inject.

Built on BeautifulSoup with ``html.parser`` so fragments stay fragments
(lxml wraps in ``<html><body>``).
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment


# Tags allowed to survive. Much wider than the blog sanitizer's list — the
# template author controls every one of these, and the visual design
# routinely uses structural wrappers (``<div>``, ``<section>``, ``<nav>``)
# and the full heading scale (``<h1>`` … ``<h6>``).
ALLOWED_TAGS = frozenset({
    # block structure
    "div", "section", "article", "header", "footer", "main", "aside", "nav",
    "p", "br", "hr",
    # headings
    "h1", "h2", "h3", "h4", "h5", "h6",
    # inline phrasing
    "span", "a", "strong", "b", "em", "i", "u", "s", "mark", "small",
    "sub", "sup", "time", "cite", "code", "kbd", "var", "samp",
    # lists
    "ul", "ol", "li", "dl", "dt", "dd",
    # quotes / pre
    "blockquote", "q", "pre",
    # tables
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "colgroup", "col",
    # media
    "img", "picture", "source", "figure", "figcaption",
    "audio", "video", "track",
    # misc safe
    "details", "summary", "address", "abbr", "wbr",
})

# Tags removed together with everything inside them. These can execute
# code, load external context, or capture form data — none of which a
# tenant richtext edit has any business doing.
DANGEROUS_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "form", "input",
    "button", "textarea", "select", "option", "link", "meta", "base",
    "svg", "math", "template", "noscript", "title", "head",
})

# Per-tag attribute allowlist. Anything else gets dropped per tag.
# Universal attrs (class, id, style, data-*, aria-*, role, lang, dir,
# title, hidden, tabindex) apply on top of this for every allowed tag.
ALLOWED_ATTRS = {
    "a": {"href", "target", "rel", "download", "name"},
    "img": {"src", "alt", "width", "height", "srcset", "sizes",
            "loading", "decoding", "fetchpriority"},
    "source": {"src", "srcset", "sizes", "media", "type"},
    "picture": set(),
    "video": {"src", "poster", "preload", "controls", "autoplay", "loop",
              "muted", "playsinline", "width", "height"},
    "audio": {"src", "preload", "controls", "autoplay", "loop", "muted"},
    "track": {"src", "kind", "srclang", "label", "default"},
    "time": {"datetime"},
    "td": {"colspan", "rowspan", "headers", "scope"},
    "th": {"colspan", "rowspan", "headers", "scope", "abbr"},
    "col": {"span"},
    "colgroup": {"span"},
    "ol": {"start", "reversed", "type"},
    "li": {"value"},
    "details": {"open"},
    "abbr": {"title"},
}

# Attributes that may appear on *any* allowed tag. The agency uses these
# constantly for design (Tailwind / utility classes, design tokens via
# inline ``style``, ARIA labels, hooks for analytics via ``data-*``).
UNIVERSAL_ATTRS = frozenset({
    "class", "id", "style", "title", "lang", "dir", "role",
    "hidden", "tabindex", "translate", "draggable",
    # Editor wiring attributes — preserved end-to-end so re-annotation /
    # re-render keeps the dashboard's field bindings intact.
    "data-section", "data-edit", "data-type", "data-label",
    "data-icon", "data-group", "data-tokens", "data-cms-ref",
})

# URL schemes considered safe for a/href and media/src.
_SAFE_HREF_SCHEMES = ("http://", "https://", "mailto:", "tel:")
_SAFE_SRC_SCHEMES = ("http://", "https://", "data:image/")

# ``style="..."`` patterns we refuse to keep — CSS-based XSS is a smaller
# surface than direct ``<script>`` but worth blocking. If the value contains
# any of these markers we drop the entire ``style`` attribute rather than
# trying to surgically rewrite CSS.
_STYLE_DENY_RE = re.compile(
    r"(?:javascript:|expression\s*\(|-moz-binding|@import)",
    re.IGNORECASE,
)


def _is_safe_url(value: str, schemes: tuple[str, ...], *, allow_anchor: bool,
                 allow_relative: bool = True) -> bool:
    if not value:
        return False
    v = value.strip()
    # Reject control chars / whitespace tricks like "java\tscript:".
    if re.search(r"[\x00-\x1f]", v):
        return False
    lowered = v.lower()
    if lowered.startswith(schemes):
        return True
    if allow_relative and v.startswith("/"):
        return True
    if allow_anchor and v.startswith("#"):
        return True
    # No scheme at all (e.g. "page/sub") = relative.
    if allow_relative and ":" not in v.split("/", 1)[0]:
        return True
    return False


def _is_attr_allowed(tag_name: str, attr: str) -> bool:
    if attr in UNIVERSAL_ATTRS:
        return True
    if attr.startswith("data-") or attr.startswith("aria-"):
        return True
    return attr in ALLOWED_ATTRS.get(tag_name, set())


def sanitize_template_html(html: str) -> str:
    """Return ``html`` cleaned for re-injection into the rendered template.

    Preserves every class / style / id / data-* / aria-* the design needs.
    Strips ``<script>`` and friends, every ``on*`` event handler, every
    ``javascript:`` URL, and ``<style>`` content with CSS-based XSS markers.
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Comments out: conditional comments are an old IE XSS vector and
    # they're not load-bearing for any template we ship.
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # First pass: remove dangerous subtrees entirely.
    for tag in soup.find_all(list(DANGEROUS_TAGS)):
        tag.decompose()

    # Second pass: per-element attribute scrub.
    for tag in soup.find_all(True):
        name = tag.name.lower()

        if name not in ALLOWED_TAGS:
            # Unknown tag: unwrap (drop the wrapper, keep children + text).
            tag.unwrap()
            continue

        for attr in list(tag.attrs):
            attr_l = attr.lower()
            # Always drop event handlers.
            if attr_l.startswith("on"):
                del tag[attr]
                continue
            # Drop anything not on the per-tag or universal allowlist.
            if not _is_attr_allowed(name, attr_l):
                del tag[attr]
                continue
            # Normalize list-valued attrs (BS gives ["class1","class2"]).
            value = tag.get(attr)
            if isinstance(value, (list, tuple)):
                tag[attr] = " ".join(value)
            # Defuse CSS-based XSS in inline style values.
            if attr_l == "style" and tag.get(attr):
                if _STYLE_DENY_RE.search(tag.get(attr) or ""):
                    del tag[attr]

        # URL-bearing attrs get scheme-checked.
        if name == "a":
            href = (tag.get("href") or "").strip()
            if href and not _is_safe_url(href, _SAFE_HREF_SCHEMES,
                                         allow_anchor=True):
                del tag["href"]

        if name in {"img", "source", "video", "audio", "track"}:
            src = (tag.get("src") or "").strip()
            if src and not _is_safe_url(src, _SAFE_SRC_SCHEMES,
                                        allow_anchor=False):
                del tag["src"]
            # srcset is comma-separated "<url> <descriptor>" pairs.
            srcset = tag.get("srcset")
            if srcset:
                pieces = []
                for entry in str(srcset).split(","):
                    entry = entry.strip()
                    if not entry:
                        continue
                    url = entry.split()[0]
                    if _is_safe_url(url, _SAFE_SRC_SCHEMES,
                                    allow_anchor=False):
                        pieces.append(entry)
                if pieces:
                    tag["srcset"] = ", ".join(pieces)
                else:
                    del tag["srcset"]

    return str(soup).strip()


def canonicalize_fragment(html: str) -> str:
    """Normalize an HTML fragment so byte-identical equality survives
    BS4 round-trips. Re-parses through ``lxml`` (the renderer's parser)
    and re-serializes, collapsing entity / attribute-order / whitespace
    drift between the parser's default extraction and the renderer's
    re-parse. Used by the renderer's no-op short-circuit so unedited
    richtext fields skip the destructive sanitize-and-reinject path
    even when BS4's first and second passes disagree on serialization.
    """
    if html is None:
        return ""
    text = html.strip()
    if not text:
        return ""
    wrapper = BeautifulSoup(f"<cms-root>{text}</cms-root>", "lxml")
    root = wrapper.find("cms-root")
    if root is None:
        return text
    return root.decode_contents().strip()
