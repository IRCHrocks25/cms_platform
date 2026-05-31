"""
Allowlist HTML sanitizer for client-authored blog bodies.

Blog post bodies come from a ``contenteditable`` editor, so the raw
``innerHTML`` is attacker-controllable. This sanitizer strips everything
not on the allowlist before the body is ever rendered on the public site,
defending against stored XSS (``<script>``, ``onclick=``, ``javascript:``
URLs, etc.).

Built on BeautifulSoup (already a project dependency) — no new packages.
Parsed with the built-in ``html.parser`` so a fragment is treated as a
fragment (lxml would wrap it in ``<html><body>``).

Design: explicit allowlists. Unknown tags are *unwrapped* (their safe text
content is kept); known-dangerous tags are *removed with their contents*.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment


# Tags that may remain in the output.
ALLOWED_TAGS = {
    "p", "br", "hr",
    "h2", "h3", "h4",
    "strong", "b", "em", "i", "u", "s",
    "a", "ul", "ol", "li",
    "blockquote", "pre", "code",
    "figure", "figcaption", "img",
}

# Tags removed together with everything inside them.
DANGEROUS_TAGS = {
    "script", "style", "iframe", "object", "embed", "form", "input",
    "button", "textarea", "select", "option", "link", "meta", "base",
    "svg", "math", "template", "noscript", "title", "head",
}

# Per-tag attribute allowlist. Everything else is dropped.
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
}

# URL schemes considered safe for links and image sources.
_SAFE_HREF_SCHEMES = ("http://", "https://", "mailto:")
_SAFE_SRC_SCHEMES = ("http://", "https://")


def _is_safe_url(value: str, schemes: tuple[str, ...], *, allow_anchor: bool) -> bool:
    if not value:
        return False
    v = value.strip()
    # Reject control chars / whitespace tricks like "java\tscript:".
    if re.search(r"[\x00-\x1f]", v):
        return False
    lowered = v.lower()
    if lowered.startswith(schemes):
        return True
    # Relative URLs and (for links) in-page anchors are safe.
    if v.startswith("/"):
        return True
    if allow_anchor and v.startswith("#"):
        return True
    # No scheme at all (e.g. "page/sub") is treated as relative.
    if ":" not in v.split("/", 1)[0]:
        return True
    return False


def sanitize_html(html: str) -> str:
    """Return ``html`` reduced to the safe allowlist. Empty/blank → ""."""
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Drop comments outright (could carry conditional-comment payloads).
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # First pass: remove dangerous subtrees entirely.
    for tag in soup.find_all(list(DANGEROUS_TAGS)):
        tag.decompose()

    # Second pass: clean every remaining element.
    for tag in soup.find_all(True):
        name = tag.name.lower()

        if name not in ALLOWED_TAGS:
            tag.unwrap()  # keep child text, drop the wrapper
            continue

        allowed = ALLOWED_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            if attr.lower() not in allowed:
                del tag[attr]
                continue
            value = tag.get(attr)
            if isinstance(value, (list, tuple)):
                value = " ".join(value)
                tag[attr] = value

        if name == "a":
            href = (tag.get("href") or "").strip()
            if not _is_safe_url(href, _SAFE_HREF_SCHEMES, allow_anchor=True):
                del tag["href"]
            elif href.lower().startswith(("http://", "https://")):
                # External link: prevent tabnabbing / referrer leak.
                tag["rel"] = "noopener noreferrer"
                tag["target"] = "_blank"

        if name == "img":
            src = (tag.get("src") or "").strip()
            if not _is_safe_url(src, _SAFE_SRC_SCHEMES, allow_anchor=False):
                tag.decompose()
                continue
            if not tag.get("alt"):
                tag["alt"] = ""

    return str(soup).strip()


def strip_to_text(html: str, length: int | None = None) -> str:
    """Plain-text version of ``html`` (for excerpts / meta descriptions)."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    if length is not None and len(text) > length:
        text = text[:length].rsplit(" ", 1)[0].rstrip() + "…"
    return text
