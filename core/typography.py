"""Semantic typography tokens (CMS-74).

A template exposes site fonts and sizes through four CSS custom properties
declared in ``:root``. The Design tab's controls (``content["_global"]``) drive
them, so one change reaches every rule that uses the variable, including class
rules that a plain ``body`` / ``h1-h6`` rule would never beat.

Templates that predate the contract are covered by deterministic inference:
the variable a ``html``/``body`` rule uses for ``font-family`` is the body font,
and the font variable whose rules style the most ``h1``-``h6`` elements is the
heading font. A variable only qualifies by playing one of these roles, so
framework variables are never exposed.

Discovered roles that use another name also get a contract alias
(``--font-body: var(--body)``), which shared surfaces such as the blog read.
"""
from __future__ import annotations

import re

import soupsieve
from bs4 import BeautifulSoup

# role, contract variable, control label, ``_global`` key, kind
ROLES = (
    ("body-font", "font-body", "Body font", "fontFamily", "font"),
    ("heading-font", "font-heading", "Heading font", "headingFamily", "font"),
    ("body-size", "text-body", "Base text size", "baseSize", "size"),
    ("heading-size", "text-heading", "Heading size", "headingSize", "size"),
)

GENERIC_FAMILIES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded",
}

LENGTH_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3})?(?:px|rem|em)$")
_SIZE_FUNCTION_RE = re.compile(r"^(?:clamp|min|max)\([0-9a-z.%,\s+\-*/]+\)$")
_FONT_ITEM_RE = re.compile(
    r"""^(?:'[A-Za-z0-9 \-]+'|"[A-Za-z0-9 \-]+"|[A-Za-z][A-Za-z0-9 \-]*)$"""
)
_FONT_NAME_RE = re.compile(r"[^A-Za-z0-9 \-]")
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_ROOT_BLOCK_RE = re.compile(r":root[^{]*\{([^}]*)\}", re.I)
_DECL_RE = re.compile(r"--([a-zA-Z0-9_-]+)\s*:\s*([^;]+)")
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_USE_RE = {
    "font": re.compile(r"font-family\s*:\s*var\(\s*--([a-zA-Z0-9_-]+)"),
    "size": re.compile(r"font-size\s*:\s*var\(\s*--([a-zA-Z0-9_-]+)"),
}
_PSEUDO_RE = re.compile(r":{1,2}[a-zA-Z-]+(?:\([^)]*\))?")
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_PAGE_ROOTS = {"html", "body", ":root"}


def is_font_stack(value: str) -> bool:
    items = [i.strip() for i in (value or "").split(",")]
    return 0 < len(items) <= 8 and all(_FONT_ITEM_RE.match(i) for i in items)


def is_size(value: str) -> bool:
    v = (value or "").strip().lower()
    return bool(LENGTH_RE.match(v) or _SIZE_FUNCTION_RE.match(v))


def _valid(kind: str, value: str) -> bool:
    return is_font_stack(value) if kind == "font" else is_size(value)


def _generic_family(stack: str) -> str:
    for item in reversed(stack.split(",")):
        name = item.strip().strip("'\"").lower()
        if name in GENERIC_FAMILIES:
            return name
    return "sans-serif"


def _css(soup: BeautifulSoup) -> str:
    return _COMMENT_RE.sub("", "\n".join(s.get_text() for s in soup.find_all("style")))


def _root_vars(css: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for block in _ROOT_BLOCK_RE.finditer(css):
        for name, raw in _DECL_RE.findall(block.group(1)):
            values[name.strip()] = raw.strip()
    return values


def _uses(css: str, kind: str) -> list[tuple[str, str]]:
    """``(selector list, variable)`` for every rule that consumes a variable."""
    pattern = _USE_RE[kind]
    return [
        (m.group(1).strip(), use.group(1))
        for m in _RULE_RE.finditer(css)
        for use in pattern.finditer(m.group(2))
    ]


def _is_page_root(selectors: str) -> bool:
    return any(s.strip().lower() in _PAGE_ROOTS for s in selectors.split(","))


def _heading_profile(headings: list) -> tuple[set[str], set[str]]:
    classes: set[str] = set()
    ids: set[str] = set()
    for el in headings:
        classes.update(el.get("class") or [])
        if el.get("id"):
            ids.add(el["id"])
    return classes, ids


def _could_match_heading(sel: str, profile: tuple[set[str], set[str]]) -> bool:
    """Cheap pre-check on the selector's last compound before soupsieve."""
    last = re.split(r"[\s>+~]+", sel)[-1]
    tag = re.match(r"[a-zA-Z][a-zA-Z0-9]*", last)
    if tag and tag.group(0).lower() not in _HEADINGS:
        return False
    classes, ids = profile
    if any(c not in classes for c in re.findall(r"\.([\w-]+)", last)):
        return False
    return all(i in ids for i in re.findall(r"#([\w-]+)", last))


def _heading_matches(headings: list, profile, selectors: str) -> int:
    count = 0
    for sel in selectors.split(","):
        sel = sel.strip()
        if not sel or "::" in sel or sel.startswith("@"):
            continue
        sel = _PSEUDO_RE.sub("", sel).strip()
        if not sel or not _could_match_heading(sel, profile):
            continue
        try:
            pattern = soupsieve.compile(sel)
        except Exception:
            continue
        count += sum(1 for el in headings if pattern.match(el))
    return count


def detect_typography_tokens(soup: BeautifulSoup) -> list[dict[str, str]]:
    """The template's typography tokens, one per role, in ``ROLES`` order."""
    css = _css(soup)
    if "--" not in css:
        return []
    roots = _root_vars(css)
    found: dict[str, str] = {}
    for role, contract, _label, _key, kind in ROLES:
        if contract in roots and _valid(kind, roots[contract]):
            found[role] = contract

    font_uses = [
        (sel, name) for sel, name in _uses(css, "font")
        if name in roots and is_font_stack(roots[name])
    ]
    if "body-font" not in found:
        for sel, name in font_uses:
            if _is_page_root(sel):
                found["body-font"] = name
                break
    if "heading-font" not in found:
        headings = soup.find_all(list(_HEADINGS))
        profile = _heading_profile(headings)
        counts: dict[str, int] = {}
        for sel, name in font_uses:
            if not headings or name == found.get("body-font"):
                continue
            counts[name] = counts.get(name, 0) + _heading_matches(headings, profile, sel)
        best = max(counts.items(), key=lambda kv: kv[1], default=(None, 0))
        if best[1] > 0:
            found["heading-font"] = best[0]
    if "body-size" not in found:
        for sel, name in _uses(css, "size"):
            if _is_page_root(sel) and name in roots and is_size(roots[name]):
                found["body-size"] = name
                break

    tokens = []
    for role, _contract, label, key, kind in ROLES:
        name = found.get(role)
        if not name:
            continue
        token = {
            "role": role, "name": name, "label": label, "key": key,
            "kind": kind, "value": roots[name],
        }
        if kind == "font":
            token["generic"] = _generic_family(roots[name])
        tokens.append(token)
    return tokens


def compose_value(token: dict[str, str], raw) -> str:
    """The CSS value a saved ``_global`` entry gives this token, or ``""``."""
    if token["kind"] == "font":
        family = _FONT_NAME_RE.sub("", str(raw or "")).strip()
        return f"'{family}', {token['generic']}" if family else ""
    value = str(raw or "").strip()
    return value if LENGTH_RE.match(value) else ""


def typography_css(tokens: list[dict[str, str]], global_styles: dict) -> str:
    """``:root{...}`` overriding tokens from saved values, plus contract
    aliases for discovered roles. ``""`` when there is nothing to declare."""
    global_styles = global_styles if isinstance(global_styles, dict) else {}
    decls = []
    for token in tokens:
        value = compose_value(token, global_styles.get(token["key"]))
        if value:
            decls.append(f"--{token['name']}: {value};")
    contract_names = {role: contract for role, contract, *_ in ROLES}
    for token in tokens:
        contract = contract_names[token["role"]]
        if token["name"] != contract:
            decls.append(f"--{contract}: var(--{token['name']});")
    return ":root{" + "".join(decls) + "}" if decls else ""


def preview_role_map(tokens: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """What the preview bridge needs to apply the same overrides live."""
    out = {}
    for token in tokens:
        entry = {"name": token["name"]}
        if token["kind"] == "font":
            entry["generic"] = token["generic"]
        out[token["role"]] = entry
    return out
