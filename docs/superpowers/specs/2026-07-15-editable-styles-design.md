# Per-Element Editable Styles (colors, font sizes, font styles)

**Date:** 2026-07-15
**Status:** Approved design — pending implementation plan

## Goal

Turn the CMS into a "full CMS" where clients can style content, not just edit
text. Every editable text element gets its own **color, font size, font family,
font weight, italic, and text alignment** controls, plus a site-wide **global
default** that any element can override. Fonts and sizes are unrestricted (any
CSS family name, any px value).

## Background — current architecture

- Content is a flat JSON blob on `Tenant.content`, e.g. `{"hero": {"title": "Welcome"}}`,
  with a `_hidden` list for hidden sections/fields.
- Templates are annotated HTML: elements carry `data-edit="section.field"` +
  `data-type`. Valid types today: `text, richtext, image, color, link, video`.
- `core/parser.py::build_schema` derives a schema from the HTML; the dashboard
  renders form fields from that schema.
- `core/renderer.py::render_site` substitutes content into the template. It
  already rewrites global CSS-variable "brand" colors in a `<style data-tokens>`
  block, and `_apply_field` mutates each `data-edit` element by type.
- Live preview uses a `postMessage` bridge (`PREVIEW_BRIDGE_SCRIPT`) that applies
  `apply-content` patches to the preview iframe in-place. Autosave is a 600ms
  debounced `POST` of the full content blob.

Colors are therefore already editable **globally** via the Brand tab. Missing:
font sizes, font families/weights, and **per-element** control.

## Design

### 1. Data model

Add two parallel namespaces to the existing `Tenant.content` blob. No DB
migration of content is required; absent keys mean "no override."

```json
{
  "hero": { "title": "Welcome" },
  "_styles": {
    "hero.title": {
      "color": "#b91c1c",
      "fontSize": "56px",
      "fontFamily": "Poppins",
      "fontWeight": "700",
      "italic": true,
      "align": "center"
    }
  },
  "_global": {
    "fontFamily": "Inter",
    "baseSize": "16px",
    "headingFamily": "Poppins",
    "textColor": "#1f2937"
  }
}
```

- `_styles[<elementId>]` — per-element overrides, keyed by the same dotted id as
  `data-edit`. Only set properties are stored (sparse).
- `_global` — site-wide defaults (the "set once" layer).
- Mirrors the existing `_hidden` convention. Existing `content` and `brand`
  remain untouched and keep working with zero migration.

### 2. Which elements are style-editable

Auto-enabled for **every** `data-edit` element of type `text` or `richtext`.
No extra template markup needed — this delivers "everything editable" by default.
A template author may opt a specific element out with `data-style="off"`.

### 3. Rendering (`core/renderer.py`)

- New `_apply_styles(el, style)` runs alongside `_apply_field`. It appends to the
  element's inline `style` attribute:
  - `color` → `color`
  - `fontSize` → `font-size`
  - `fontFamily` → `font-family`
  - `fontWeight` → `font-weight`
  - `italic: true` → `font-style: italic`
  - `align` → `text-align`
- Inline styles win over template CSS via specificity. Template rules using
  `!important` will still beat inline styles — documented limitation, not
  handled in v1.
- **Global layer:** `_global` values are written as CSS custom properties / base
  rules in the rendered output (extending the existing `<style data-tokens>`
  mechanism) so untouched elements inherit them; per-element inline styles
  override.
- **Font loading:** the renderer collects every `fontFamily` used across
  `_styles` and `_global`, dedupes, sanitizes the family names (strip quotes,
  restrict to a safe charset to prevent URL injection), and injects a **single**
  Google Fonts `<link href="https://fonts.googleapis.com/css2?family=...&display=swap">`.
  Unknown families fall back to system fonts. The injected `<link>` and its
  preconnect are marked `data-cookieconsent="ignore"` so Cookiebot auto-blocking
  does not strip the font CDN.

### 4. Live preview bridge

`PREVIEW_BRIDGE_SCRIPT` gains an `apply-styles` message handler mirroring
`apply-content`: it applies the six style properties to the matching
`[data-edit]` element(s) in-place, and (for font families) injects the Google
Fonts `<link>` into the preview document on demand. No server roundtrip.

### 5. Edit UI

- **Per-element:** `templates/dashboard/components/field.html` renders a
  collapsible **"Style"** panel beneath each text/richtext field:
  color picker, size (number, px), font family (free text), weight (select),
  italic (toggle), alignment (left/center/right).
- **Global:** the existing "Brand" tab is extended into a **"Design"** tab that
  also exposes the `_global` typography slots (body font family, base size,
  heading family, default text color) alongside the existing brand colors.
- `static/js/editor.js` binds these controls into `_styles` / `_global`, pushes
  `apply-styles` patches to the preview, and rides the existing 600ms autosave.
  No new save endpoint; the save payload simply carries the extra namespaces.

### 6. Parser (`core/parser.py`)

- `build_schema` marks which `data-edit` elements are style-editable (type
  `text`/`richtext` and not `data-style="off"`) so the dashboard knows where to
  render Style panels.
- Recognizes/validates the `_global` typography token slots.

## Testing (TDD — tests first)

- **Parser:** schema marks the correct elements as style-editable; respects
  `data-style="off"`.
- **Renderer:** `_apply_styles` produces the correct inline CSS for each property
  and combination; italic maps to `font-style`; global tokens render; font-family
  collection dedupes and sanitizes; a single Google Fonts `<link>` is injected
  with `data-cookieconsent="ignore"`.
- **Round trip:** a saved `_styles` + `_global` blob renders to the expected
  inline styles and font links on the public page.
- Follows the existing `core/tests/` Django test style.

## Out of scope (v1)

- Overriding template rules that use `!important`.
- Non-text style controls (spacing, borders, backgrounds beyond existing color).
- A font *picker* with previews — v1 uses free-text family entry per the chosen
  "any font" freedom level.
