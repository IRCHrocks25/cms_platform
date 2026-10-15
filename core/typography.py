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

Beyond the four roles, every font variable the template's rules consume is a
*font slot* with its own picker, and every font-consuming selector is mapped
into one of three text groups: Title, Heading, Body. Buttons, nav and labels
follow Body so a font change actually reaches them. Changing the Heading font
also restyles Title and Heading selectors that the template had left on the
body variable (large statements, class-only titles). A group can be pointed at
another slot, which re-emits its selectors with ``font-family: var(--slot)``.
"""
from __future__ import annotations

import re

import soupsieve
import tinycss2
from bs4 import BeautifulSoup

# role, contract variable, control label, ``_global`` key, kind
ROLES = (
    ("body-font", "font-body", "Body font", "fontFamily", "font"),
    ("heading-font", "font-heading", "Heading font", "headingFamily", "font"),
    ("body-size", "text-body", "Base text size", "baseSize", "size"),
    ("heading-size", "text-heading", "Heading size", "headingSize", "size"),
    ("title-font", "font-title", "Title font", "titleFamily", "font"),
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

# id, label; also the order the editor lists them in
TEXT_GROUPS = (
    ("title", "Title"),
    ("heading", "Heading"),
    ("body", "Body"),
)
GROUP_IDS = frozenset(g for g, _ in TEXT_GROUPS)
SLOT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ROLE_SLOT_KEYS = {"body-font": ("fontFamily", "Body font", "body"),
                   "heading-font": ("headingFamily", "Heading font", "heading"),
                   "title-font": ("titleFamily", "Title font", "title")}
_FONT_NOISE_PREFIXES = ("tw-", "radix-", "bits-", "reka-", "kb-", "ngp-")
_FONT_NOISE_NAMES = {"font-sans", "font-serif", "font-mono"}
_TITLE_CLASSES = {"h-title", "hero-title", "page-title", "site-title"}
_HEADING_CLASS_RE = re.compile(r"(?:^|-)(?:h|heading|title)$", re.I)
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([^;]+)")
_SIZE_NUM_RE = re.compile(r"(\d*\.?\d+)(rem|em|px)")
_TITLE_REM = 4.0
_HEADING_REM = 1.75
_LINK_TAGS = {"a", "button"}
_DYNAMIC_PSEUDO_RE = re.compile(r"(?<!:):(?:hover|active|focus-visible|focus-within|focus|visited)\b", re.I)


def _selectors(text):
    """Split selector lists without splitting commas in functions/attributes."""
    current = []
    for token in tinycss2.parse_component_value_list(text):
        if token.type == "literal" and token.value == ",":
            yield tinycss2.serialize(current).strip()
            current = []
        else:
            current.append(token)
    if current:
        yield tinycss2.serialize(current).strip()


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
    parts = []
    for style in soup.find_all("style"):
        css = style.get_text()
        if style.get("media"):
            css = "@media " + style["media"] + "{" + css + "}"
        parts.append(css)
    return "\n".join(parts)


def _rules(css: str, context=()):
    """Qualified CSS rules with their enclosing conditions, excluding keyframes."""
    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type == "qualified-rule":
            yield tinycss2.serialize(rule.prelude).strip(), tinycss2.serialize(rule.content), context
        elif rule.type == "at-rule" and rule.content is not None:
            if rule.lower_at_keyword in {"media", "supports", "layer", "container", "scope"}:
                scope = "@" + rule.at_keyword + " " + tinycss2.serialize(rule.prelude).strip()
                yield from _rules(tinycss2.serialize(rule.content), context + (scope.strip(),))


def _font_name_allowed(name):
    low = name.lower()
    return bool(SLOT_NAME_RE.fullmatch(name)) and low not in _FONT_NOISE_NAMES and not low.startswith(_FONT_NOISE_PREFIXES)


def _root_vars(css: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for selectors, body, context in _rules(css):
        if context or ":root" not in _selectors(selectors):
            continue
        for decl in tinycss2.parse_declaration_list(body, skip_comments=True, skip_whitespace=True):
            if decl.type == "declaration" and decl.name.startswith("--"):
                values[decl.name[2:]] = tinycss2.serialize(decl.value).strip()
    return values


def _uses(css: str, kind: str) -> list[tuple[str, str]]:
    """``(selector list, variable)`` for every rule that consumes a variable."""
    pattern = _USE_RE[kind]
    return [
        (selectors, use.group(1))
        for selectors, body, context in _rules(css)
        for use in pattern.finditer(body)
        if not context and _font_name_allowed(use.group(1))
    ]


def _is_page_root(selectors: str) -> bool:
    return any(s.lower() in _PAGE_ROOTS for s in _selectors(selectors))


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
    for sel in _selectors(selectors):
        sel = sel.strip()
        if not sel or "::" in sel or sel.startswith("@"):
            continue
        sel = _DYNAMIC_PSEUDO_RE.sub("", sel).strip()
        if not sel or ("(" not in sel and not _could_match_heading(sel, profile)):
            continue
        try:
            pattern = soupsieve.compile(sel)
        except Exception:
            continue
        count += sum(1 for el in headings if pattern.match(el))
    return count


def detect_typography_tokens(soup: BeautifulSoup) -> list[dict[str, str]]:
    """The template's typography tokens, one per role, in ``ROLES`` order."""
    return detect_typography(soup)["tokens"]


def detect_typography(soup: BeautifulSoup) -> dict[str, list]:
    """``{"tokens", "slots", "groups"}`` for a template, from one CSS pass."""
    css = _css(soup)
    roots = _root_vars(css)
    font_rules = [
        (sel, body, use.group(1), context)
        for sel, body, context in _rules(css)
        for use in _USE_RE["font"].finditer(body)
        if _font_name_allowed(use.group(1)) and use.group(1) in roots and is_font_stack(roots[use.group(1)])
    ]
    headings = soup.find_all(list(_HEADINGS))
    tokens = _role_tokens(css, roots, font_rules, headings)
    slots = _font_slots(roots, font_rules, tokens)
    groups = _text_groups(font_rules, headings, list(_rules(css)), tokens)
    if "font-title" not in roots:
        fallback = next((t for t in tokens if t["role"] == "heading-font"), None)
        fallback = fallback or next((t for t in tokens if t["role"] == "body-font"), None)
        if fallback:
            title = {"name": "font-title", "key": "titleFamily", "label": "Title font",
                     "group": "title", "value": fallback["value"], "generic": fallback["generic"],
                     "fallback": fallback["name"]}
            index = next((i for i, s in enumerate(slots) if s["key"].startswith("font:")), len(slots))
            slots.insert(index, title)
    return {"tokens": tokens, "slots": slots, "groups": groups}


def _role_tokens(css, roots, font_rules, headings) -> list[dict[str, str]]:
    found: dict[str, str] = {}
    for role, contract, _label, _key, kind in ROLES:
        if contract in roots and _valid(kind, roots[contract]):
            found[role] = contract

    font_uses = [(sel, name) for sel, _body, name, context in font_rules if not context]
    if "body-font" not in found:
        for sel, name in font_uses:
            if _is_page_root(sel):
                found["body-font"] = name
                break
    if "heading-font" not in found:
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


def _font_slots(roots, font_rules, tokens) -> list[dict[str, str]]:
    """One picker per font variable the template's rules use. The body and
    heading role variables keep their existing ``_global`` keys."""
    roles = {t["name"]: t["role"] for t in tokens if t["role"] in _ROLE_SLOT_KEYS}
    order = list(_ROLE_SLOT_KEYS)
    names: dict[str, None] = {}
    for name in sorted(roles, key=lambda n: order.index(roles[n])):
        names.setdefault(name, None)
    for _sel, _body, name, _context in font_rules:
        if SLOT_NAME_RE.match(name):
            names.setdefault(name, None)
    slots = []
    for name in names:
        if name in roles:
            key, label, group = _ROLE_SLOT_KEYS[roles[name]]
        else:
            pretty = re.sub(r"[-_]+", " ", name).strip().capitalize()
            key, label, group = f"font:{name}", f"{pretty} font", "other"
        slots.append({
            "name": name, "key": key, "label": label, "group": group,
            "value": roots[name], "generic": _generic_family(roots[name]),
        })
    return slots


def _rem(value: str) -> list[float]:
    return [
        float(num) / 16 if unit == "px" else float(num)
        for num, unit in _SIZE_NUM_RE.findall(value)
    ]


def _group_of(sel: str, body: str, headings, profile) -> str:
    """Map one selector to Title, Heading or Body.

    Title is the page/hero title (``h1``, ``.h-title``). Heading is every other
    heading tag, heading-like class, and large display statement. Everything
    else (buttons, nav, labels, copy) is Body, so those faces actually
    follow the Body font instead of sitting in groups the automap missed.
    """
    bare = _PSEUDO_RE.sub("", sel).strip()
    last = re.split(r"[\s>+~]+", bare)[-1] if bare else ""
    tag_m = re.match(r"[a-zA-Z][a-zA-Z0-9]*", last)
    tag = tag_m.group(0).lower() if tag_m else ""
    classes = {c.lower() for c in re.findall(r"\.([\w-]+)", last)}
    size = _FONT_SIZE_RE.search(body)
    sizes = _rem(size.group(1)) if size else []
    biggest = max(sizes) if sizes else 0
    heading_el = tag in _HEADINGS or _heading_matches(headings, profile, sel)
    titles = [el for el in headings if el.name == "h1"]
    if tag == "h1" or classes & _TITLE_CLASSES or _heading_matches(titles, _heading_profile(titles), sel):
        return "title"
    if heading_el:
        return "heading"
    if tag in _LINK_TAGS:
        return "body"
    if biggest >= _TITLE_REM:
        return "title"
    if any(_HEADING_CLASS_RE.search(c) for c in classes):
        return "heading" if "h-title" not in classes else "title"
    if biggest >= _HEADING_REM:
        return "heading"
    return "body"


def _text_groups(font_rules, headings, css_rules, tokens) -> list[dict]:
    """Every font-consuming selector, sorted into ``TEXT_GROUPS``. A group's
    default is the font its rules use most."""
    profile = _heading_profile(headings)
    selectors: dict[str, dict[str, None]] = {g: {} for g in GROUP_IDS}
    uses: dict[str, dict[str, int]] = {g: {} for g in GROUP_IDS}
    rules = {g: [] for g in GROUP_IDS}
    candidates = list(font_rules)
    role_names = {t["role"]: t["name"] for t in tokens}
    for sel_list, body, context in css_rules:
        # Literal-font and inherited-font titles also need to follow the group.
        # Leave unknown/framework var() consumers out of typography discovery.
        if "font-" not in body or _USE_RE["font"].search(body):
            continue
        for sel in _selectors(sel_list):
            group = _group_of(sel.strip(), body, headings, profile)
            if group in ("title", "heading"):
                name = role_names.get("heading-font", role_names.get("body-font", ""))
                candidates.append((sel, body, name, context))
    group_for = {}
    rank = {"body": 0, "heading": 1, "title": 2}
    for sel_list, body, _name, _context in candidates:
        for selector in _selectors(sel_list):
            selector = " ".join(selector.split())
            gid = _group_of(selector, body, headings, profile)
            if rank[gid] > rank.get(group_for.get(selector), -1):
                group_for[selector] = gid
    for sel_list, body, name, context in candidates:
        for sel in _selectors(sel_list):
            sel = " ".join(sel.split())
            if not sel or "<" in sel or sel.startswith("@") or sel.lower() == ":root":
                continue
            group = group_for[sel]
            selectors[group].setdefault(sel, None)
            uses[group][name] = uses[group].get(name, 0) + 1
            important = any(
                d.type == "declaration" and d.lower_name == "font-family" and d.important
                for d in tinycss2.parse_declaration_list(body)
            )
            rule = {"selector": sel, "context": list(context), "important": important}
            if rule not in rules[group]:
                rules[group].append(rule)
    # Headings without an explicit font rule inherit their site's fonts but
    # still need a group selector when Title is customized independently.
    for el in headings:
        gid = "title" if el.name == "h1" else "heading"
        covered = False
        for sel in selectors[gid]:
            try:
                if soupsieve.match(sel, el):
                    covered = True
                    break
            except soupsieve.SelectorSyntaxError:
                continue
        if not covered:
            selectors[gid].setdefault(el.name, None)
            name = role_names.get("heading-font", role_names.get("body-font", ""))
            uses[gid][name] = uses[gid].get(name, 0) + 1
            rule = {"selector": el.name, "context": [], "important": False}
            if rule not in rules[gid]:
                rules[gid].append(rule)
    groups = []
    for gid, label in TEXT_GROUPS:
        if not selectors[gid]:
            continue
        default = max(uses[gid].items(), key=lambda kv: kv[1])[0]
        groups.append({
            "id": gid, "label": label, "selectors": list(selectors[gid]),
            "default": default, "mixed": len(uses[gid]) > 1,
            "rules": rules[gid],
        })
    return groups


def compose_value(token: dict[str, str], raw) -> str:
    """The CSS value a saved ``_global`` entry gives this token, or ``""``."""
    if token["kind"] == "font":
        family = _FONT_NAME_RE.sub("", str(raw or "")).strip()
        return f"'{family}', {token['generic']}" if family else ""
    value = str(raw or "").strip()
    return value if LENGTH_RE.match(value) else ""


def typography_css(typography: dict, global_styles: dict) -> str:
    """``:root{...}`` overriding tokens and font slots from saved values, plus
    contract aliases for discovered roles, then one rule per text group that
    was pointed at another slot. ``""`` when there is nothing to declare."""
    global_styles = global_styles if isinstance(global_styles, dict) else {}
    tokens = typography.get("tokens", [])
    slots = typography.get("slots", [])
    decls = []
    declared: set[str] = set()
    for token in tokens:
        value = compose_value(token, global_styles.get(token["key"]))
        if value:
            decls.append(f"--{token['name']}: {value};")
            declared.add(token["name"])
    for slot in slots:
        if slot["name"] in declared or not slot["key"].startswith("font:"):
            continue
        value = compose_value({"kind": "font", **slot}, global_styles.get(slot["key"]))
        if value:
            decls.append(f"--{slot['name']}: {value};")
    contract_names = {role: contract for role, contract, *_ in ROLES}
    for token in tokens:
        contract = contract_names[token["role"]]
        # A discovered template font is only a candidate for the editor. Do
        # not make it the blog font until the tenant saves that choice.
        if compose_value(token, global_styles.get(token["key"])):
            if token["name"] != contract:
                decls.append(f"--{contract}: var(--{token['name']});")
            if token["role"] in ("body-font", "heading-font"):
                blog_role = token["role"].removesuffix("-font")
                decls.append(f"--cms-font-{blog_role}: var(--{token['name']});")
    title_token = next((t for t in tokens if t["role"] == "title-font"), None)
    heading_token = next((t for t in tokens if t["role"] == "heading-font"), None)
    title_value = compose_value(title_token or {"kind": "font", "generic": (heading_token or {}).get("generic", "sans-serif")}, global_styles.get("titleFamily"))
    if title_value and not title_token:
        decls.append(f"--font-title: {title_value};")
    virtual_title = next((s for s in slots if s.get("fallback")), None)
    if virtual_title and not title_value and any(global_styles.get(f"group:{gid}") == "font-title" for gid in GROUP_IDS):
        decls.append(f"--font-title: var(--{virtual_title['fallback']});")
    css = ":root{" + "".join(decls) + "}" if decls else ""
    slot_names = {s["name"] for s in slots}
    role_slot = {t["role"]: t["name"] for t in tokens if t["role"] in _ROLE_SLOT_KEYS}
    heading_slot = role_slot.get("heading-font")
    title_slot = role_slot.get("title-font")
    body_slot = role_slot.get("body-font")
    heading_on = any(
        t["role"] == "heading-font" and compose_value(t, global_styles.get(t["key"]))
        for t in tokens
    )
    body_on = any(
        t["role"] == "body-font" and compose_value(t, global_styles.get(t["key"]))
        for t in tokens
    )
    group_aliases = []
    for group in typography.get("groups", []):
        gid = group["id"]
        remap = global_styles.get(f"group:{gid}")
        target = remap if remap in slot_names else None
        if target is None:
            if gid == "title" and (title_value or title_slot):
                target = title_slot or "font-title"
            elif gid in ("title", "heading"):
                target = heading_slot
            elif gid == "body":
                target = body_slot
        if target not in slot_names and not (gid == "title" and target == "font-title" and title_value):
            continue
        remap_on = remap in slot_names
        if not (
            remap_on
            or (gid == "title" and title_value)
            or (gid in ("title", "heading") and heading_on)
            or (gid == "body" and body_on)
        ):
            continue
        if remap_on:
            group_aliases.append(f"--cms-font-{gid}: var(--{target});")
        for rule in group.get("rules", [{"selector": s, "context": [], "important": False} for s in group["selectors"]]):
            part = rule["selector"] + "{font-family:var(--" + target + ")" + ("!important" if rule["important"] else "") + "}"
            for scope in reversed(rule["context"]):
                part = scope + "{" + part + "}"
            css += part
    # Blog heading mappings must not change an otherwise-unmapped blog title.
    if global_styles.get("group:heading") in slot_names and global_styles.get("group:title") not in slot_names:
        fallback = title_slot or ("font-title" if title_value else heading_slot)
        if fallback:
            group_aliases.append(f"--cms-font-title: var(--{fallback});")
    if group_aliases:
        css += ":root{" + "".join(group_aliases) + "}"
    return css


def font_families(typography: dict, global_styles: dict) -> list[str]:
    """Families chosen for extra font slots, for the Google Fonts link."""
    if not isinstance(global_styles, dict):
        return []
    out = []
    for slot in typography.get("slots", []):
        if slot["key"].startswith("font:"):
            family = _FONT_NAME_RE.sub("", str(global_styles.get(slot["key"]) or "")).strip()
            if family:
                out.append(family)
    return out


def preview_role_map(typography: dict) -> dict:
    """What the preview bridge needs to apply the same overrides live."""
    roles = {}
    for token in typography.get("tokens", []):
        entry = {"name": token["name"]}
        if token["kind"] == "font":
            entry["generic"] = token["generic"]
        roles[token["role"]] = entry
    return {
        "roles": roles,
        "slots": [
            {"name": s["name"], "key": s["key"], "generic": s["generic"],
             **({"fallback": s["fallback"]} if s.get("fallback") else {})}
            for s in typography.get("slots", [])
        ],
        "groups": [
            {"id": g["id"], "selectors": g["selectors"], "rules": g["rules"]}
            for g in typography.get("groups", [])
        ],
    }
