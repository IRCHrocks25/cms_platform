# Per-Element Editable Styles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let clients set color, font size, font family, weight, italic, and alignment on every editable text element, plus a site-wide global default any element can override.

**Architecture:** Two new namespaces on the existing `Tenant.content` JSON blob — `_styles` (per-element overrides, keyed by the dotted `data-edit` id) and `_global` (site-wide typography defaults). The renderer applies per-element styles as inline CSS, writes global defaults as a `<style>` block, and injects one Google Fonts `<link>` for every family used. The parser flags which fields are style-editable; the dashboard renders a collapsible Style panel and a Design tab; the live-preview postMessage bridge gains `apply-styles` / `apply-global` handlers.

**Tech Stack:** Django, BeautifulSoup + lxml, vanilla JS (no new deps).

## Global Constraints

- **No new Python/JS dependencies.** Stack is Django + BeautifulSoup + lxml + Pillow only (per CLAUDE.md).
- **`Tenant.content` is canonical; schema is derived, never stored.** Don't add per-field DB columns; don't persist schema.
- **Meta keys start with `_`.** `merge_with_defaults` already passes `_`-prefixed keys through verbatim (core/renderer.py:501-503) — `_styles` and `_global` ride that path. Do not change that behavior.
- **Dotted field IDs everywhere** (`section.field`), matching `data-edit`, schema, content, and postMessage payloads.
- **postMessage `source` strings are fixed:** `"cms-editor"` (parent→iframe) and `"cms-preview"` (iframe→parent). Do not rename.
- **Cookiebot caveat:** any injected font CDN `<link>` MUST carry `data-cookieconsent="ignore"` or consent auto-blocking strips it.
- **Tests:** `django.test.SimpleTestCase`, import from `core.renderer` / `core.parser`. Run with `python manage.py test <dotted.path> -v 2`.
- **Commits:** local identity is already set (`Jezmer Kyle G. Ramos`). No Claude attribution in commit messages. Commit to `main`.

## File structure

- **Modify** `core/renderer.py` — add per-element style application, global style block, font-link injection; extend `PREVIEW_BRIDGE_SCRIPT`; wire into `render_site`.
- **Modify** `core/parser.py` — add `style_editable` flag to text/richtext fields.
- **Modify** `dashboard/views.py` — normalize `_styles` / `_global` in `_save_content`; pass a `global_styles` context value to the editor.
- **Modify** `templates/dashboard/components/field.html` — Style panel for style-editable fields.
- **Modify** `templates/dashboard/editor.html` — rename Brand tab to Design, add global typography controls.
- **Modify** `static/js/editor.js` — bind Style panels + Design controls, push `apply-styles` / `apply-global`, save.
- **Create** `core/tests/test_renderer_styles.py`, `core/tests/test_renderer_fonts.py`, `core/tests/test_parser_style_editable.py`, `core/tests/test_save_styles.py`.

---

### Task 1: Renderer — per-element inline style application

**Files:**
- Modify: `core/renderer.py` (add helpers near `_apply_brand_tokens`, ~line 312)
- Test: `core/tests/test_renderer_styles.py`

**Interfaces:**
- Produces: `_STYLE_PROPERTIES: dict[str,str]`, `_set_css_prop(el, prop: str, value: str) -> None`, `_apply_element_styles(el, style: dict) -> None`, `_apply_styles(soup, styles: dict) -> None`.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_renderer_styles.py`:

```python
"""Tests for per-element and global editable styles."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.renderer import _apply_element_styles, _apply_styles


def _el(html):
    return BeautifulSoup(html, "lxml").find(attrs={"data-edit": True})


class ApplyElementStylesTests(SimpleTestCase):
    def test_maps_each_property_to_css(self):
        el = _el('<h1 data-edit="hero.title">Hi</h1>')
        _apply_element_styles(el, {
            "color": "#b91c1c", "fontSize": "56px", "fontFamily": "Poppins",
            "fontWeight": "700", "italic": True, "align": "center",
        })
        style = el.get("style", "")
        self.assertIn("color: #b91c1c;", style)
        self.assertIn("font-size: 56px;", style)
        self.assertIn("font-family: Poppins;", style)
        self.assertIn("font-weight: 700;", style)
        self.assertIn("font-style: italic;", style)
        self.assertIn("text-align: center;", style)

    def test_italic_false_omits_font_style(self):
        el = _el('<p data-edit="a.b">x</p>')
        _apply_element_styles(el, {"italic": False, "color": "#000000"})
        self.assertNotIn("font-style", el.get("style", ""))

    def test_empty_values_skipped(self):
        el = _el('<p data-edit="a.b">x</p>')
        _apply_element_styles(el, {"color": "", "fontSize": None, "align": "left"})
        style = el.get("style", "")
        self.assertNotIn("color", style)
        self.assertNotIn("font-size", style)
        self.assertIn("text-align: left;", style)

    def test_reapply_replaces_not_appends(self):
        el = _el('<p data-edit="a.b" style="color: red;">x</p>')
        _apply_element_styles(el, {"color": "#111111"})
        self.assertEqual(el.get("style", "").count("color"), 1)
        self.assertIn("color: #111111;", el.get("style", ""))

    def test_apply_styles_targets_by_data_edit(self):
        soup = BeautifulSoup(
            '<body><h1 data-edit="hero.title">Hi</h1>'
            '<p data-edit="hero.body">B</p></body>', "lxml")
        _apply_styles(soup, {"hero.title": {"color": "#abcabc"}})
        self.assertIn("color: #abcabc;", soup.find(attrs={"data-edit": "hero.title"}).get("style", ""))
        self.assertEqual(soup.find(attrs={"data-edit": "hero.body"}).get("style", ""), "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_renderer_styles -v 2`
Expected: FAIL — `ImportError: cannot import name '_apply_element_styles'`.

- [ ] **Step 3: Write minimal implementation**

In `core/renderer.py`, after `_apply_brand_tokens` (after line 325), add:

```python
# Per-element editable styles. Keys are the client-facing style names stored in
# content["_styles"][<data-edit id>]; values map to CSS declarations. `italic`
# is handled separately (boolean -> font-style: italic).
_STYLE_PROPERTIES = {
    "color": "color",
    "fontSize": "font-size",
    "fontFamily": "font-family",
    "fontWeight": "font-weight",
    "align": "text-align",
}


def _set_css_prop(el, prop: str, value: str) -> None:
    """Set one CSS declaration on an element's inline style, replacing any
    existing declaration of the same property (mirrors the `color` field type
    in _apply_field so re-renders don't stack duplicates)."""
    existing = el.get("style", "")
    cleaned = re.sub(rf"{re.escape(prop)}\s*:[^;]*;?", "", existing).strip()
    el["style"] = (cleaned + f" {prop}: {value};").strip()


def _apply_element_styles(el, style: dict) -> None:
    if not isinstance(style, dict):
        return
    for key, css_prop in _STYLE_PROPERTIES.items():
        value = style.get(key)
        if value is None or value == "":
            continue
        _set_css_prop(el, css_prop, str(value))
    if style.get("italic"):
        _set_css_prop(el, "font-style", "italic")


def _apply_styles(soup: BeautifulSoup, styles: dict) -> None:
    """Apply every per-element style override to its `data-edit` element(s)."""
    if not isinstance(styles, dict):
        return
    for element_id, style in styles.items():
        if not isinstance(element_id, str) or "." not in element_id:
            continue
        for el in soup.find_all(attrs={"data-edit": element_id}):
            _apply_element_styles(el, style)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_renderer_styles -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add core/renderer.py core/tests/test_renderer_styles.py
git commit -m "feat(renderer): per-element inline style application"
```

---

### Task 2: Renderer — global typography defaults block

**Files:**
- Modify: `core/renderer.py`
- Test: `core/tests/test_renderer_styles.py` (append)

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `_apply_global_styles(soup, global_styles: dict) -> None` — injects a `<style data-cms-global>` block into `<head>` (falls back to body). Recognized keys: `fontFamily` (body), `baseSize` (body font-size), `headingFamily` (h1–h6), `textColor` (body color).

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_renderer_styles.py`:

```python
from core.renderer import _apply_global_styles


class ApplyGlobalStylesTests(SimpleTestCase):
    def _render(self, global_styles):
        soup = BeautifulSoup("<html><head></head><body><h1>H</h1></body></html>", "lxml")
        _apply_global_styles(soup, global_styles)
        return soup

    def test_injects_body_and_heading_rules(self):
        soup = self._render({
            "fontFamily": "Inter", "baseSize": "16px",
            "headingFamily": "Poppins", "textColor": "#1f2937",
        })
        block = soup.find("style", attrs={"data-cms-global": True})
        self.assertIsNotNone(block)
        css = block.string
        self.assertIn("font-family: Inter", css)
        self.assertIn("font-size: 16px", css)
        self.assertIn("color: #1f2937", css)
        self.assertIn("Poppins", css)
        self.assertIn("h1", css)

    def test_empty_global_injects_nothing(self):
        soup = self._render({})
        self.assertIsNone(soup.find("style", attrs={"data-cms-global": True}))

    def test_partial_global_only_sets_provided(self):
        soup = self._render({"textColor": "#123456"})
        css = soup.find("style", attrs={"data-cms-global": True}).string
        self.assertIn("color: #123456", css)
        self.assertNotIn("font-size", css)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_renderer_styles.ApplyGlobalStylesTests -v 2`
Expected: FAIL — `cannot import name '_apply_global_styles'`.

- [ ] **Step 3: Write minimal implementation**

In `core/renderer.py`, after `_apply_styles`, add:

```python
def _apply_global_styles(soup: BeautifulSoup, global_styles: dict) -> None:
    """Write site-wide typography defaults as a low-specificity <style> block.
    Per-element inline styles (Task 1) always win over these; the template's own
    element-specific CSS may still override the body-level defaults."""
    if not isinstance(global_styles, dict):
        return
    body_decls = []
    font_family = global_styles.get("fontFamily")
    base_size = global_styles.get("baseSize")
    text_color = global_styles.get("textColor")
    heading_family = global_styles.get("headingFamily")
    if font_family:
        body_decls.append(f"font-family: {font_family};")
    if base_size:
        body_decls.append(f"font-size: {base_size};")
    if text_color:
        body_decls.append(f"color: {text_color};")

    rules = []
    if body_decls:
        rules.append("body{" + " ".join(body_decls) + "}")
    if heading_family:
        rules.append("h1,h2,h3,h4,h5,h6{font-family: " + str(heading_family) + ";}")
    if not rules:
        return

    style = soup.new_tag("style")
    style["data-cms-global"] = "true"
    style.string = "".join(rules)
    (soup.find("head") or soup.find("body") or soup).append(style)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_renderer_styles.ApplyGlobalStylesTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/renderer.py core/tests/test_renderer_styles.py
git commit -m "feat(renderer): global typography defaults block"
```

---

### Task 3: Renderer — Google Fonts link injection

**Files:**
- Modify: `core/renderer.py`
- Test: `core/tests/test_renderer_fonts.py`

**Interfaces:**
- Produces: `_sanitize_font_family(name: str) -> str`, `_collect_font_families(content: dict) -> list[str]`, `_inject_font_links(soup, families: list[str]) -> None`.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_renderer_fonts.py`:

```python
"""Tests for Google Fonts collection + injection."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.renderer import (
    _collect_font_families,
    _inject_font_links,
    _sanitize_font_family,
)


class SanitizeFontFamilyTests(SimpleTestCase):
    def test_strips_unsafe_chars(self):
        self.assertEqual(_sanitize_font_family('Poppins"><script>'), "Poppinsscript")

    def test_keeps_spaces_and_hyphen(self):
        self.assertEqual(_sanitize_font_family("  Playfair Display "), "Playfair Display")

    def test_empty_returns_empty(self):
        self.assertEqual(_sanitize_font_family(""), "")
        self.assertEqual(_sanitize_font_family(None), "")


class CollectFontFamiliesTests(SimpleTestCase):
    def test_dedupes_across_styles_and_global(self):
        content = {
            "_styles": {
                "hero.title": {"fontFamily": "Poppins"},
                "hero.body": {"fontFamily": "Inter"},
                "a.b": {"fontFamily": "Poppins"},
            },
            "_global": {"fontFamily": "Inter", "headingFamily": "Lora"},
        }
        fams = _collect_font_families(content)
        self.assertEqual(sorted(fams), ["Inter", "Lora", "Poppins"])

    def test_no_styles_returns_empty(self):
        self.assertEqual(_collect_font_families({"hero": {"title": "x"}}), [])


class InjectFontLinksTests(SimpleTestCase):
    def test_injects_single_link_with_consent_ignore(self):
        soup = BeautifulSoup("<html><head></head><body></body></html>", "lxml")
        _inject_font_links(soup, ["Poppins", "Playfair Display"])
        links = soup.find_all("link", href=lambda h: h and "fonts.googleapis.com/css2" in h)
        self.assertEqual(len(links), 1)
        href = links[0]["href"]
        self.assertIn("family=Poppins", href)
        self.assertIn("family=Playfair+Display", href)
        self.assertIn("display=swap", href)
        self.assertEqual(links[0].get("data-cookieconsent"), "ignore")
        preconnects = soup.find_all("link", attrs={"rel": "preconnect"})
        self.assertTrue(preconnects)
        self.assertTrue(all(p.get("data-cookieconsent") == "ignore" for p in preconnects))

    def test_empty_families_injects_nothing(self):
        soup = BeautifulSoup("<html><head></head></html>", "lxml")
        _inject_font_links(soup, [])
        self.assertFalse(soup.find_all("link"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_renderer_fonts -v 2`
Expected: FAIL — `cannot import name '_sanitize_font_family'`.

- [ ] **Step 3: Write minimal implementation**

In `core/renderer.py`, add near the other style helpers:

```python
_FONT_NAME_RE = re.compile(r"[^A-Za-z0-9 \-]")
# Weights we request so the per-element weight control (300-800) always has glyphs.
_FONT_WEIGHTS = "300;400;500;600;700;800"


def _sanitize_font_family(name: str) -> str:
    """Reduce a family name to a Google-Fonts-safe token (letters, digits,
    spaces, hyphens). Prevents URL/HTML injection from free-text font input."""
    return _FONT_NAME_RE.sub("", (name or "")).strip()


def _collect_font_families(content: dict) -> list[str]:
    """Every distinct family used across per-element _styles and _global,
    sanitized and de-duplicated in first-seen order."""
    if not isinstance(content, dict):
        return []
    seen: dict[str, None] = {}
    styles = content.get("_styles")
    if isinstance(styles, dict):
        for style in styles.values():
            if isinstance(style, dict):
                fam = _sanitize_font_family(style.get("fontFamily", ""))
                if fam:
                    seen.setdefault(fam, None)
    glob = content.get("_global")
    if isinstance(glob, dict):
        for key in ("fontFamily", "headingFamily"):
            fam = _sanitize_font_family(glob.get(key, ""))
            if fam:
                seen.setdefault(fam, None)
    return list(seen.keys())


def _inject_font_links(soup: BeautifulSoup, families: list[str]) -> None:
    """Inject one Google Fonts stylesheet <link> (+ preconnects) for the given
    families. All carry data-cookieconsent="ignore" so Cookiebot auto-blocking
    doesn't strip the font CDN (see agency history)."""
    if not families:
        return
    head = soup.find("head") or soup.find("body")
    if head is None:
        return
    params = "&".join(
        f"family={fam.replace(' ', '+')}:wght@{_FONT_WEIGHTS}" for fam in families
    )
    href = f"https://fonts.googleapis.com/css2?{params}&display=swap"

    pre1 = soup.new_tag("link", rel="preconnect", href="https://fonts.googleapis.com")
    pre1["data-cookieconsent"] = "ignore"
    pre2 = soup.new_tag("link", rel="preconnect", href="https://fonts.gstatic.com")
    pre2["crossorigin"] = ""
    pre2["data-cookieconsent"] = "ignore"
    link = soup.new_tag("link", rel="stylesheet", href=href)
    link["data-cookieconsent"] = "ignore"
    head.append(pre1)
    head.append(pre2)
    head.append(link)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_renderer_fonts -v 2`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add core/renderer.py core/tests/test_renderer_fonts.py
git commit -m "feat(renderer): inject Google Fonts links for used families"
```

---

### Task 4: Renderer — wire styles + global + fonts into `render_site`

**Files:**
- Modify: `core/renderer.py` (`render_site`, lines 445-488)
- Test: `core/tests/test_renderer_styles.py` (append round-trip)

**Interfaces:**
- Consumes: `_apply_styles`, `_apply_global_styles`, `_collect_font_families`, `_inject_font_links` (Tasks 1-3).

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_renderer_styles.py`:

```python
from core.renderer import render_site

_TEMPLATE = (
    "<html><head></head><body>"
    '<section data-section="hero"><h1 data-edit="hero.title" data-type="text">Hi</h1></section>'
    "</body></html>"
)


class RenderSiteStylesTests(SimpleTestCase):
    def test_round_trip_applies_inline_and_global_and_font(self):
        content = {
            "hero": {"title": "Welcome"},
            "_styles": {"hero.title": {"color": "#b91c1c", "fontSize": "56px",
                                       "fontFamily": "Poppins"}},
            "_global": {"fontFamily": "Inter", "textColor": "#1f2937"},
        }
        html = render_site(_TEMPLATE, content)
        soup = BeautifulSoup(html, "lxml")
        h1 = soup.find(attrs={"data-edit": "hero.title"})
        self.assertIn("color: #b91c1c;", h1.get("style", ""))
        self.assertIn("font-size: 56px;", h1.get("style", ""))
        self.assertEqual(h1.get_text(), "Welcome")
        self.assertIsNotNone(soup.find("style", attrs={"data-cms-global": True}))
        self.assertTrue(soup.find_all("link", href=lambda h: h and "fonts.googleapis.com" in h))

    def test_no_style_namespaces_is_noop(self):
        html = render_site(_TEMPLATE, {"hero": {"title": "Hi"}})
        soup = BeautifulSoup(html, "lxml")
        self.assertIsNone(soup.find("style", attrs={"data-cms-global": True}))
        self.assertFalse(soup.find_all("link", href=lambda h: h and "fonts.googleapis.com" in h))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_renderer_styles.RenderSiteStylesTests -v 2`
Expected: FAIL — no inline style / no global block (helpers not wired in).

- [ ] **Step 3: Write minimal implementation**

In `core/renderer.py::render_site`, insert after the `_apply_field` loop and before the `_hidden` block (after line 474):

```python
    if isinstance(content, dict) and isinstance(content.get("_styles"), dict):
        _apply_styles(soup, content["_styles"])
    if isinstance(content, dict) and isinstance(content.get("_global"), dict):
        _apply_global_styles(soup, content["_global"])
    _inject_font_links(soup, _collect_font_families(content if isinstance(content, dict) else {}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_renderer_styles core.tests.test_renderer_fonts -v 2`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add core/renderer.py core/tests/test_renderer_styles.py
git commit -m "feat(renderer): wire per-element + global styles and fonts into render_site"
```

---

### Task 5: Parser — `style_editable` flag on text/richtext fields

**Files:**
- Modify: `core/parser.py` (`build_schema` field loop, lines 116-139)
- Test: `core/tests/test_parser_style_editable.py`

**Interfaces:**
- Produces: each field dict in the schema gains `"style_editable": bool` — `True` for `text`/`richtext` fields whose element does NOT have `data-style="off"`, else `False`.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_parser_style_editable.py`:

```python
"""Tests for the style_editable schema flag."""
from django.test import SimpleTestCase

from core.parser import build_schema

_HTML = """<html><body>
<section data-section="hero" data-label="Hero">
  <h1 data-edit="hero.title" data-type="text">Hi</h1>
  <div data-edit="hero.body" data-type="richtext"><p>b</p></div>
  <img data-edit="hero.image" data-type="image" src="x.jpg">
  <span data-edit="hero.locked" data-type="text" data-style="off">L</span>
</section>
</body></html>"""


class StyleEditableFlagTests(SimpleTestCase):
    def _fields(self):
        schema = build_schema(_HTML)
        hero = next(s for s in schema["sections"] if s["id"] == "hero")
        return {f["id"]: f for f in hero["fields"]}

    def test_text_and_richtext_are_style_editable(self):
        fields = self._fields()
        self.assertTrue(fields["hero.title"]["style_editable"])
        self.assertTrue(fields["hero.body"]["style_editable"])

    def test_image_is_not_style_editable(self):
        self.assertFalse(self._fields()["hero.image"]["style_editable"])

    def test_data_style_off_opts_out(self):
        self.assertFalse(self._fields()["hero.locked"]["style_editable"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_parser_style_editable -v 2`
Expected: FAIL — `KeyError: 'style_editable'`.

- [ ] **Step 3: Write minimal implementation**

In `core/parser.py`, inside the field loop, replace the append block (lines 129-138) with:

```python
            default = _extract_default(field_el, ftype)

            style_editable = (
                ftype in ("text", "richtext")
                and field_el.get("data-style", "").strip().lower() != "off"
            )

            section_entry["fields"].append(
                {
                    "id": full_id,
                    "label": field_el.get("data-label", _humanize(field_part)),
                    "type": ftype,
                    "default": default,
                    "style_editable": style_editable,
                }
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_parser_style_editable -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/parser.py core/tests/test_parser_style_editable.py
git commit -m "feat(parser): flag text/richtext fields as style_editable"
```

---

### Task 6: Server — normalize `_styles` / `_global` on save

**Files:**
- Modify: `dashboard/views.py` (`_save_content`, after the `_hidden` block, ~line 2230)
- Test: `core/tests/test_save_styles.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: a module-level helper `_normalize_styles(content: dict) -> None` in `dashboard/views.py` that mutates `content` in place, keeping only allowed style keys/values.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_save_styles.py`:

```python
"""Tests for _styles / _global normalization on save."""
from django.test import SimpleTestCase

from dashboard.views import _normalize_styles


class NormalizeStylesTests(SimpleTestCase):
    def test_keeps_allowed_style_keys_and_drops_others(self):
        content = {"_styles": {"hero.title": {
            "color": "#b91c1c", "fontSize": "56px", "fontFamily": "Poppins",
            "fontWeight": "700", "italic": True, "align": "center",
            "evil": "x", "onclick": "alert(1)",
        }}}
        _normalize_styles(content)
        style = content["_styles"]["hero.title"]
        self.assertEqual(set(style), {
            "color", "fontSize", "fontFamily", "fontWeight", "italic", "align"})
        self.assertTrue(style["italic"])

    def test_drops_non_dotted_and_non_dict_entries(self):
        content = {"_styles": {"nodot": {"color": "#000000"}, "a.b": "notadict"}}
        _normalize_styles(content)
        self.assertEqual(content["_styles"], {})

    def test_truncates_long_values(self):
        content = {"_styles": {"a.b": {"fontFamily": "x" * 300}}}
        _normalize_styles(content)
        self.assertEqual(len(content["_styles"]["a.b"]["fontFamily"]), 120)

    def test_normalizes_global(self):
        content = {"_global": {
            "fontFamily": "Inter", "baseSize": "16px",
            "headingFamily": "Poppins", "textColor": "#1f2937", "junk": "no"}}
        _normalize_styles(content)
        self.assertEqual(set(content["_global"]), {
            "fontFamily", "baseSize", "headingFamily", "textColor"})

    def test_missing_namespaces_are_untouched(self):
        content = {"hero": {"title": "x"}}
        _normalize_styles(content)
        self.assertEqual(content, {"hero": {"title": "x"}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_save_styles -v 2`
Expected: FAIL — `cannot import name '_normalize_styles'`.

- [ ] **Step 3: Write minimal implementation**

In `dashboard/views.py`, add above `_save_content` (before line 2211):

```python
_ALLOWED_STYLE_KEYS = {"color", "fontSize", "fontFamily", "fontWeight", "align"}
_ALLOWED_GLOBAL_KEYS = {"fontFamily", "baseSize", "headingFamily", "textColor"}


def _clean_style_value(value):
    if isinstance(value, bool):
        return value
    return str(value)[:120]


def _normalize_styles(content: dict) -> None:
    """Defensively sanitize the _styles / _global meta namespaces in place so a
    malformed client payload can't inject arbitrary keys the renderer trusts."""
    raw_styles = content.get("_styles")
    if raw_styles is not None:
        clean_styles = {}
        if isinstance(raw_styles, dict):
            for element_id, style in raw_styles.items():
                if not (isinstance(element_id, str) and "." in element_id):
                    continue
                if not isinstance(style, dict):
                    continue
                kept = {k: _clean_style_value(v) for k, v in style.items()
                        if k in _ALLOWED_STYLE_KEYS and v not in (None, "")}
                if style.get("italic"):
                    kept["italic"] = True
                if kept:
                    clean_styles[element_id[:120]] = kept
        content["_styles"] = clean_styles

    raw_global = content.get("_global")
    if raw_global is not None:
        if isinstance(raw_global, dict):
            content["_global"] = {
                k: str(v)[:120] for k, v in raw_global.items()
                if k in _ALLOWED_GLOBAL_KEYS and v not in (None, "")
            }
        else:
            content.pop("_global", None)
```

Then, inside `_save_content`, after the `_hidden` normalization block (after line 2230) add:

```python
    _normalize_styles(content)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_save_styles -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py core/tests/test_save_styles.py
git commit -m "feat(dashboard): normalize _styles/_global on save"
```

---

### Task 7: Preview bridge — `apply-styles` / `apply-global` handlers

**Files:**
- Modify: `core/renderer.py` (`PREVIEW_BRIDGE_SCRIPT`, message handler ~lines 86-157)
- Test: `core/tests/test_renderer_styles.py` (append — assert the compiled bridge contains the handlers)

**Interfaces:**
- Consumes: nothing. Produces: the preview bridge script, when rendered with `preview=True`, contains `apply-styles` and `apply-global` message branches plus a preview-side font loader.

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_renderer_styles.py`:

```python
class PreviewBridgeStyleTests(SimpleTestCase):
    def test_bridge_has_style_handlers(self):
        html = render_site(_TEMPLATE, {"hero": {"title": "Hi"}}, preview=True)
        self.assertIn("apply-styles", html)
        self.assertIn("apply-global", html)
        self.assertIn("cmsEnsureFont", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_renderer_styles.PreviewBridgeStyleTests -v 2`
Expected: FAIL — strings absent.

- [ ] **Step 3: Write minimal implementation**

In `core/renderer.py`, inside `PREVIEW_BRIDGE_SCRIPT`, add a font-loader helper near the top of the IIFE (after the `send` function, ~line 26) :

```javascript
  var CMS_STYLE_PROP = { color: 'color', fontSize: 'font-size',
    fontFamily: 'font-family', fontWeight: 'font-weight', align: 'text-align' };
  function cmsEnsureFont(family) {
    if (!family) return;
    var safe = String(family).replace(/[^A-Za-z0-9 \\-]/g, '').trim();
    if (!safe) return;
    var id = 'cms-font-' + safe.replace(/ /g, '-');
    if (document.getElementById(id)) return;
    var link = document.createElement('link');
    link.id = id;
    link.rel = 'stylesheet';
    link.setAttribute('data-cookieconsent', 'ignore');
    link.href = 'https://fonts.googleapis.com/css2?family=' +
      safe.replace(/ /g, '+') + ':wght@300;400;500;600;700;800&display=swap';
    document.head.appendChild(link);
  }
  function cmsApplyStyle(el, style) {
    Object.keys(CMS_STYLE_PROP).forEach(function (k) {
      if (style[k] !== undefined && style[k] !== null && style[k] !== '') {
        el.style[CMS_STYLE_PROP[k] === 'text-align' ? 'textAlign' :
          (k === 'fontSize' ? 'fontSize' : (k === 'fontFamily' ? 'fontFamily' :
          (k === 'fontWeight' ? 'fontWeight' : 'color')))] = style[k];
      }
    });
    el.style.fontStyle = style.italic ? 'italic' : '';
    if (style.fontFamily) cmsEnsureFont(style.fontFamily);
  }
```

Then add two branches inside the `window.addEventListener('message', ...)` handler, after the `apply-content` block (after line 125):

```javascript
    if (data.type === 'apply-styles') {
      Object.entries(data.payload || {}).forEach(function (entry) {
        var fid = entry[0], style = entry[1] || {};
        document.querySelectorAll('[data-edit="' + fid + '"]').forEach(function (el) {
          cmsApplyStyle(el, style);
        });
      });
    }
    if (data.type === 'apply-global') {
      var g = data.payload || {};
      var css = '';
      var bodyDecls = '';
      if (g.fontFamily) { bodyDecls += 'font-family:' + g.fontFamily + ';'; cmsEnsureFont(g.fontFamily); }
      if (g.baseSize) bodyDecls += 'font-size:' + g.baseSize + ';';
      if (g.textColor) bodyDecls += 'color:' + g.textColor + ';';
      if (bodyDecls) css += 'body{' + bodyDecls + '}';
      if (g.headingFamily) { css += 'h1,h2,h3,h4,h5,h6{font-family:' + g.headingFamily + ';}'; cmsEnsureFont(g.headingFamily); }
      var tag = document.getElementById('cms-global-style');
      if (!tag) { tag = document.createElement('style'); tag.id = 'cms-global-style'; document.head.appendChild(tag); }
      tag.textContent = css;
    }
```

Note: the doubled backslash in the regex (`\\-`) is required because `PREVIEW_BRIDGE_SCRIPT` is a normal (non-raw) Python string.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_renderer_styles.PreviewBridgeStyleTests -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/renderer.py core/tests/test_renderer_styles.py
git commit -m "feat(preview): apply-styles/apply-global bridge handlers + font loader"
```

---

### Task 8: Editor UI — Style panel markup in `field.html`

**Files:**
- Modify: `templates/dashboard/components/field.html`

**Interfaces:**
- Consumes: `field.style_editable` (Task 5). Produces: a `.cms-style-panel[data-style-panel="<field.id>"]` block whose controls carry `data-style-bind="<prop>"` (props: `color`, `fontSize`, `fontFamily`, `fontWeight`, `italic`, `align`). Task 9 binds these.

- [ ] **Step 1: Add the Style panel markup**

In `templates/dashboard/components/field.html`, immediately before the final `</div>` that closes `.field` (line 80), insert:

```html
  {% if field.style_editable %}
    <details class="cms-style-panel" data-style-panel="{{ field.id }}">
      <summary class="cms-style-summary">Style</summary>
      <div class="cms-style-grid">
        <label class="cms-style-row">
          <span>Color</span>
          <input type="color" data-style-bind="color" data-style-color-picker value="#000000">
          <input class="input" type="text" data-style-bind="colorText" placeholder="#000000">
        </label>
        <label class="cms-style-row">
          <span>Size</span>
          <input class="input" type="number" min="8" max="200" step="1"
                 data-style-bind="fontSize" placeholder="px">
        </label>
        <label class="cms-style-row">
          <span>Font</span>
          <input class="input" type="text" data-style-bind="fontFamily"
                 placeholder="e.g. Poppins">
        </label>
        <label class="cms-style-row">
          <span>Weight</span>
          <select class="select" data-style-bind="fontWeight">
            <option value="">Default</option>
            <option value="300">Light</option>
            <option value="400">Normal</option>
            <option value="500">Medium</option>
            <option value="600">Semibold</option>
            <option value="700">Bold</option>
            <option value="800">Extra bold</option>
          </select>
        </label>
        <label class="cms-style-row cms-style-inline">
          <span>Italic</span>
          <input type="checkbox" data-style-bind="italic">
        </label>
        <div class="cms-style-row">
          <span>Align</span>
          <div class="cms-align-group" role="group" aria-label="Text alignment">
            <button type="button" data-style-align="left" title="Left">≡</button>
            <button type="button" data-style-align="center" title="Center">≡</button>
            <button type="button" data-style-align="right" title="Right">≡</button>
          </div>
        </div>
      </div>
    </details>
  {% endif %}
```

- [ ] **Step 2: Add minimal styling**

In `static/css/editor.css`, append:

```css
.cms-style-panel { margin-top: 8px; border-top: 1px solid var(--color-border, #e5e7eb); padding-top: 8px; }
.cms-style-summary { cursor: pointer; font-size: 12px; color: var(--color-muted, #6b7280); user-select: none; }
.cms-style-grid { display: grid; gap: 8px; margin-top: 8px; }
.cms-style-row { display: grid; grid-template-columns: 56px 1fr auto; gap: 8px; align-items: center; font-size: 13px; }
.cms-style-inline { grid-template-columns: 56px auto; }
.cms-align-group button { padding: 4px 8px; border: 1px solid var(--color-border, #e5e7eb); background: #fff; cursor: pointer; }
.cms-align-group button[aria-pressed="true"] { background: var(--color-blue, #2563eb); color: #fff; }
```

- [ ] **Step 3: Verify it renders (manual)**

Run: `python manage.py runserver`, open the editor for a tenant, confirm a **Style** disclosure appears under each text/richtext field and expands. (No automated test — pure template markup; behavior is covered by Task 9's manual smoke.)

- [ ] **Step 4: Commit**

```bash
git add templates/dashboard/components/field.html static/css/editor.css
git commit -m "feat(editor): per-field Style panel markup"
```

---

### Task 9: Editor JS — bind Style panels, push preview, save

**Files:**
- Modify: `static/js/editor.js`

**Interfaces:**
- Consumes: `.cms-style-panel[data-style-panel]` markup (Task 8); the `apply-styles` bridge handler (Task 7). Produces: `content._styles` writes on every control change, live `apply-styles` patches, autosave via existing `scheduleSave`.

- [ ] **Step 1: Add style helpers and binding**

In `static/js/editor.js`, after the `_hidden` init line (`if (!Array.isArray(content._hidden)) content._hidden = [];`, line 124) add:

```javascript
  if (typeof content._styles !== "object" || content._styles === null) content._styles = {};

  function getStyle(fieldId) {
    return content._styles[fieldId] || {};
  }
  function setStyleProp(fieldId, prop, value) {
    var s = content._styles[fieldId] || {};
    if (value === "" || value === null || value === undefined || value === false) {
      delete s[prop];
    } else {
      s[prop] = value;
    }
    if (Object.keys(s).length) content._styles[fieldId] = s;
    else delete content._styles[fieldId];
  }
  function pushStyleToPreview(fieldId) {
    if (!previewReady) return;
    var p = {}; p[fieldId] = getStyle(fieldId);
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-styles", payload: p }, "*");
  }
```

- [ ] **Step 2: Wire the panel controls inside `init()`**

In `static/js/editor.js`, inside `init()`, immediately after the `document.querySelectorAll("[data-field-id]").forEach(...)` field-binding loop closes (after line 563), add:

```javascript
    document.querySelectorAll("[data-style-panel]").forEach(function (panel) {
      var fieldId = panel.getAttribute("data-style-panel");
      var current = getStyle(fieldId);

      function commit(prop, value) {
        setStyleProp(fieldId, prop, value);
        pushStyleToPreview(fieldId);
        scheduleSave();
      }

      var colorPicker = panel.querySelector('[data-style-color-picker]');
      var colorText = panel.querySelector('[data-style-bind="colorText"]');
      if (current.color) {
        if (colorText) colorText.value = current.color;
        if (colorPicker && /^#[0-9a-fA-F]{6}$/.test(current.color)) colorPicker.value = current.color;
      }
      if (colorPicker) colorPicker.addEventListener("input", function () {
        if (colorText) colorText.value = colorPicker.value;
        commit("color", colorPicker.value);
      });
      if (colorText) colorText.addEventListener("input", function () {
        if (colorPicker && /^#[0-9a-fA-F]{6}$/.test(colorText.value)) colorPicker.value = colorText.value;
        commit("color", colorText.value);
      });

      var size = panel.querySelector('[data-style-bind="fontSize"]');
      if (size) {
        if (current.fontSize) size.value = parseInt(current.fontSize, 10) || "";
        size.addEventListener("input", function () {
          commit("fontSize", size.value ? size.value + "px" : "");
        });
      }

      var fam = panel.querySelector('[data-style-bind="fontFamily"]');
      if (fam) {
        if (current.fontFamily) fam.value = current.fontFamily;
        fam.addEventListener("input", function () { commit("fontFamily", fam.value.trim()); });
      }

      var weight = panel.querySelector('[data-style-bind="fontWeight"]');
      if (weight) {
        if (current.fontWeight) weight.value = current.fontWeight;
        weight.addEventListener("change", function () { commit("fontWeight", weight.value); });
      }

      var italic = panel.querySelector('[data-style-bind="italic"]');
      if (italic) {
        italic.checked = !!current.italic;
        italic.addEventListener("change", function () { commit("italic", italic.checked); });
      }

      var alignBtns = panel.querySelectorAll("[data-style-align]");
      function reflectAlign(val) {
        alignBtns.forEach(function (b) {
          b.setAttribute("aria-pressed", b.getAttribute("data-style-align") === val ? "true" : "false");
        });
      }
      reflectAlign(current.align || "");
      alignBtns.forEach(function (b) {
        b.addEventListener("click", function () {
          var val = b.getAttribute("data-style-align");
          if (getStyle(fieldId).align === val) val = ""; // toggle off
          reflectAlign(val);
          commit("align", val);
        });
      });
    });
```

- [ ] **Step 3: Re-assert styles on preview ready**

In `static/js/editor.js`, inside the `data.type === "ready"` branch (after line 238, after the `_hidden` re-assert loop) add:

```javascript
      Object.keys(content._styles).forEach(function (fid) { pushStyleToPreview(fid); });
```

- [ ] **Step 4: Manual smoke test**

Run: `python manage.py runserver`. In a tenant editor:
1. Expand **Style** under a heading, set color, size 56, font `Poppins`, weight Bold, italic, center.
2. Confirm the preview updates live for each control (font loads within ~1s).
3. Reload the editor — controls should reflect the saved values.
4. Visit the published/preview public render and confirm the inline styles + the Google Fonts `<link>` are present (view source).

- [ ] **Step 5: Commit**

```bash
git add static/js/editor.js
git commit -m "feat(editor): bind per-field style controls to preview + autosave"
```

---

### Task 10: Design tab — global typography controls

**Files:**
- Modify: `templates/dashboard/editor.html` (Brand tab → Design tab, lines 104-110 and 177-190)
- Modify: `static/js/editor.js` (bind global controls)

**Interfaces:**
- Consumes: `content._global`; the `apply-global` bridge handler (Task 7). Produces: `content._global` writes + live `apply-global` patch + autosave. Global controls carry `data-global-bind="<key>"` (keys: `fontFamily`, `baseSize`, `headingFamily`, `textColor`).

- [ ] **Step 1: Add the global controls to the Design tab**

In `templates/dashboard/editor.html`, inside the `{% if brand_section %}` Design panel (after the `brand-intro` block, before the brand `editor-form-section` at line 183), insert:

```html
          <div class="editor-form-section" data-section-id="_global">
            <div class="editor-form-section-head"><h2>Typography</h2></div>
            <div class="stack-5">
              <label class="field"><span class="field-label">Body font</span>
                <input class="input" type="text" data-global-bind="fontFamily" placeholder="e.g. Inter"></label>
              <label class="field"><span class="field-label">Base text size</span>
                <input class="input" type="number" min="10" max="32" data-global-bind="baseSize" placeholder="16"></label>
              <label class="field"><span class="field-label">Heading font</span>
                <input class="input" type="text" data-global-bind="headingFamily" placeholder="e.g. Poppins"></label>
              <label class="field"><span class="field-label">Default text color</span>
                <input class="input" type="text" data-global-bind="textColor" placeholder="#1f2937"></label>
            </div>
          </div>
```

Note: the Design tab only shows when `brand_section` exists (template has `<style data-tokens>`). That matches today's behavior; templates without brand tokens keep per-element styling via Task 8/9. (Broadening the tab to always show is out of scope for v1.)

- [ ] **Step 2: Rename the tab label**

In `templates/dashboard/editor.html`, change the Brand tab button `aria-label`/`title`/text (line 105) from "Brand" / "Brand colors" to "Design", and the panel heading (line 180) from "Brand colors" to "Design". Leave `data-tab="brand"` / `data-panel="brand"` unchanged (JS keys).

- [ ] **Step 3: Bind global controls in editor.js**

In `static/js/editor.js`, after the style-panel binding block (end of Task 9 Step 2), add:

```javascript
    if (typeof content._global !== "object" || content._global === null) content._global = {};
    function pushGlobalToPreview() {
      if (!previewReady) return;
      previewFrame.contentWindow.postMessage(
        { source: "cms-editor", type: "apply-global", payload: content._global }, "*");
    }
    document.querySelectorAll("[data-global-bind]").forEach(function (input) {
      var key = input.getAttribute("data-global-bind");
      var cur = content._global[key];
      if (cur) input.value = key === "baseSize" ? parseInt(cur, 10) || "" : cur;
      input.addEventListener("input", function () {
        var v = input.value.trim();
        if (key === "baseSize" && v) v = v + "px";
        if (v) content._global[key] = v; else delete content._global[key];
        pushGlobalToPreview();
        scheduleSave();
      });
    });
```

- [ ] **Step 4: Re-assert global on preview ready**

In `static/js/editor.js`, inside the `data.type === "ready"` branch (alongside Task 9 Step 3), add:

```javascript
      if (content._global && Object.keys(content._global).length) pushGlobalToPreview();
```

- [ ] **Step 5: Manual smoke test**

Run: `python manage.py runserver`. On a `data-tokens` template: open the **Design** tab, set Body font `Inter`, base size 18, heading font `Poppins`, text color `#1f2937`. Confirm the preview updates globally and the values persist after reload. Confirm public render contains `<style data-cms-global>` and the font `<link>`.

- [ ] **Step 6: Commit**

```bash
git add templates/dashboard/editor.html static/js/editor.js
git commit -m "feat(editor): Design tab with global typography controls"
```

---

### Task 11: Full regression + sample template annotation

**Files:**
- Modify: `samples/restaurant.html` (add a `data-style="off"` example comment — optional demonstration)

- [ ] **Step 1: Run the whole test suite**

Run: `python manage.py test core dashboard -v 1`
Expected: PASS, no regressions. Fix any failures before proceeding.

- [ ] **Step 2: End-to-end smoke (per CLAUDE.md first-run test)**

Run: `python manage.py runserver`. Create/open a tenant from `samples/restaurant.html`, then:
1. Style a heading (color/size/font/weight/italic/align) — live preview updates.
2. Set global Design typography — live preview updates site-wide.
3. Publish, visit the public URL, view source: inline styles on the element, `<style data-cms-global>` in head, one `fonts.googleapis.com/css2` `<link>` with `data-cookieconsent="ignore"`.
4. Hide/show still works; existing text/image/color/link editing unaffected.

- [ ] **Step 3: Rebuild static assets if the project bundles CSS**

If a Tailwind/CSS build step exists (check `package.json`), rebuild so `editor.css` additions ship. Otherwise skip. Verify the Style panel styling via a hard refresh / incognito (static is cached — see the cms_platform static-cache note).

- [ ] **Step 4: Commit any sample tweak**

```bash
git add samples/restaurant.html
git commit -m "docs(sample): note data-style opt-out in restaurant template"
```

---

## Self-Review

**Spec coverage:**
- Per-element color/size/family/weight/italic/align → Tasks 1, 8, 9. ✅
- Global defaults ("set once", overridable) → Tasks 2, 10; per-element inline wins over global block by specificity. ✅
- Any font + any size → free-text family (Task 8), numeric px (Task 8/9), sanitized font injection (Task 3). ✅
- Auto-on for all text/richtext, opt-out via `data-style="off"` → Task 5. ✅
- Live preview parity → Task 7 bridge handlers + Tasks 9/10 push. ✅
- Font loading + Cookiebot ignore → Task 3 (public) + Task 7 (preview). ✅
- Backward compatibility (no content migration) → `_`-prefixed pass-through in `merge_with_defaults`; namespaces absent = no-op (Task 4 test). ✅
- Server hardening → Task 6 normalization. ✅
- Tests-first → every Python task leads with a failing test; JS/template tasks use render-level assertions (Task 7) + manual smoke (documented, since there's no JS test harness in this repo). ✅

**Placeholder scan:** No TBD/TODO; all code blocks complete. Manual-verification steps are explicit because the repo has no JS/browser test harness (only Django tests) — this is a stated limitation, not a vague step.

**Type consistency:** `_apply_element_styles`, `_apply_styles`, `_apply_global_styles`, `_collect_font_families`, `_inject_font_links`, `_sanitize_font_family`, `_normalize_styles` names match across definition and call sites. Style keys (`color`, `fontSize`, `fontFamily`, `fontWeight`, `italic`, `align`) are identical in renderer, save normalization, bridge, field.html, and editor.js. Global keys (`fontFamily`, `baseSize`, `headingFamily`, `textColor`) identical across renderer, save, bridge, template, editor.js.

**Known v1 limitations (documented, out of scope):** template rules using `!important` beat inline styles; `text-align` on inline elements has no visible effect; the Design tab only appears on `data-tokens` templates.
