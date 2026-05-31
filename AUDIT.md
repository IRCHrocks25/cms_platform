# Blog Feature — Phase 1 Investigation

Audited: 2026-05-28
Goal: add client-publishable blog posts (structured, no raw HTML) using
pre-built templates, fitting existing CMS patterns. This document records
what already exists and what the blog feature should reuse.

---

## 1. How sites/pages are stored and served

There is no `Page` model. A tenant site is **one page**: the homepage,
rendered from its `Template`.

- **`core.models.Template`** — `html_source` (annotated HTML) + derived
  `schema` (JSON, rebuilt on every `save()` via `build_schema()`).
- **`core.models.Tenant`** — `subdomain` (unique), `template` (FK),
  `owner` (FK User), `content` (JSON blob keyed by `section.field`),
  `site_settings` (JSON, SEO/analytics), `is_published` (bool),
  `custom_domain`.
- **Rendering** (`core/views.py`):
  - `public_render(request, subdomain)` → `/site/<subdomain>/` on any host.
  - `root_redirect(request)` → `/` on a tenant host (subdomain resolved by
    middleware into `request.tenant`).
  - Both call `_render_tenant(tenant)` which does:
    `merge_with_defaults(template.schema, tenant.content)` →
    `render_site(template.html_source, content, preview=False, site_settings=…)`
    → `HttpResponse(html)`.
  - Draft gating: unpublished sites 404 publicly but render for
    `tenant.user_can_edit(user)` (staff/owner/member).
- **`core/renderer.py::render_site`** parses `html_source` with
  BeautifulSoup(lxml), substitutes content into `[data-edit]` elements,
  applies brand tokens, injects site settings into `<head>` (non-preview),
  and in preview mode appends the postMessage bridge script.
- **`core/middleware.py::TenantResolverMiddleware`** sets `request.tenant`
  from the host's leftmost label (or a verified `CustomDomain`).

**Reuse for blog:** subdomain/tenant resolution is already done by the
middleware — blog public routes can read `request.tenant` exactly like
`root_redirect` does. Public URL routing lives in `cms_platform/urls.py`
(`/site/<subdomain>/` and `/` via root_redirect). Blog index/detail are
**standalone pages**, not annotated-HTML surfaces — they should be plain
Django templates rendered server-side, not run through the parser/renderer
content-substitution machinery. The blog **homepage strip**, however, must
appear inside the rendered homepage, so it has to be injected by
`render_site` (or a post-process step) into the homepage HTML.

---

## 2. Head-tag / meta injection (Site Settings)

Site Settings is built and tested (uncommitted: `0009_tenant_site_settings`,
`core/tests/test_site_settings.py`).

- **Storage:** `Tenant.site_settings` (JSON). Keys: `page_title`,
  `meta_description`, `og_image_url`, `ga_measurement_id`,
  `custom_head_script`.
- **Injection:** `core/renderer.py::_inject_site_settings(soup, settings)`
  — sets/overwrites `<title>`, `meta[name=description]`, OG/Twitter
  title/description/image, a validated GA snippet (`GA_ID_RE`), and an
  arbitrary `custom_head_script`. Called only in non-preview render.
- **Validation:** `dashboard/views.py::_validate_site_settings` (length
  caps, GA-ID regex, URL scheme check). GA injection re-validates with
  `GA_ID_RE` and `escape()`s the id.
- **Endpoints:** `tenant_site_settings` (agency, by pk) and
  `tenant_site_settings_self` (tenant host) → shared
  `_get_or_save_site_settings`. GET returns JSON, POST validates+saves.
- **UI:** modal in `editor.html`, wired in `editor.js` (`openSettings`,
  `saveSettings`).

**Reuse for blog:** per-post SEO needs `title` / `description` / `og_image`
injected into the blog detail `<head>`. The cleanest reuse is to make the
`<head>`-injection logic callable for an arbitrary settings dict. Plan:
refactor `_inject_site_settings` into a public, reusable
`inject_head(html_or_soup, settings_dict)` and call it for blog pages with a
**merged** dict = site-level `site_settings` (so GA + custom script carry
over to blog pages) **overridden** by the post's per-post SEO
(title/description/og). This guarantees blog pages and the main site never
diverge on analytics/meta behavior.

---

## 3. Dashboard nav + where "Blog" should live

Nav is in `templates/base.html`, branches on `request.tenant`:

- **Agency host** (`request.tenant is None`, staff only):
  `Overview | Sites | Templates | Domains | Users | + New client | Sign out`.
- **Tenant host** (client editor): `Editor | Preview site ↗ | Sign out`.

Two surfaces, one `dashboard/views.py`, separated by host + decorators
(`agency_operator_required` vs `tenant_member_required`). URL names in
`dashboard/urls.py` under namespace `dashboard`. Agency tenant-scoped pages
take `<pk>`; tenant-host pages are `…_self` and read `request.tenant`.

**Where Blog lives:**
- **Tenant host (client):** the natural home. The client editor today only
  has "Editor". Add a **"Blog"** nav link → blog post list / editor for
  *their* site (`request.tenant`), `…_self` style, `tenant_member_required`.
- **Agency host (operator):** operators edit any site. Blog management is
  per-site, so it belongs **under a site** (a "Blog" tab/section reachable
  from `tenant_detail` and the editor bar), keyed by `<pk>` and guarded by
  `agency_operator_required` — mirroring the existing
  `tenant_editor`/`tenant_save` (pk) vs `tenant_*_self` split.

This mirrors the established two-surface pattern exactly: shared helper
functions, two thin wrappers (pk-based agency + self-based tenant).

---

## 4. Rich-text / editor components

- **No React, no TipTap, no rich-text library.** Stack is intentionally
  tiny: Django + BeautifulSoup + lxml + Pillow + vanilla JS. CLAUDE.md:
  "No new dependencies casually… Don't add jQuery, htmx, alpine, etc."
- Existing richtext field type is a **`contenteditable` div**
  (`components/field.html`, bound in `editor.js`): it stores `innerHTML`
  and live-patches the preview. There is **no toolbar** — formatting relies
  on the browser default (no bold/heading buttons today).

**Recommendation (no new dependency):** build a small `contenteditable`
editor with a formatting toolbar in vanilla JS (`blog_editor.js`),
consistent with the existing richtext field. Toolbar: H2/H3, bold, italic,
link, bullet/ordered list, blockquote, inline image. This matches the stack
and the "structured content, no raw HTML" constraint. Adding TipTap is
explicitly **not** appropriate — it's a React library and this is a
server-rendered Django + vanilla-JS app.

**XSS:** `contenteditable` `innerHTML` is attacker-controllable, so blog
body **must be sanitized server-side before public render**. There is no
sanitizer dependency (`bleach`/`nh3` are absent), and adding one conflicts
with the "no new deps" rule. Plan: a BeautifulSoup allowlist sanitizer in
`core/services/sanitizer.py` (reuses the existing parsing stack). Sanitize
on **save** and again defensively on **render**.

---

## 5. Image upload / storage

- **`core.models.MediaAsset`** — `tenant` (FK), `file`
  (`ImageField(upload_to="tenants/%Y/%m/")`), `original_name`,
  `uploaded_at`.
- **Upload endpoints:** `tenant_upload` (agency, pk) /
  `tenant_upload_self` (tenant host) → shared `_save_upload(request,
  tenant)`, which creates a `MediaAsset` and returns
  `{ok, url, id}`. Client side: `editor.js` posts `FormData` with the
  `X-CSRFToken` header.
- Storage backend: `FileSystemStorage` (`MEDIA_ROOT`), served by Django in
  DEBUG. Pillow installed but not used for processing. No CDN.

**Reuse for blog:** cover image and inline body images use the **same**
`_save_upload` flow and `MediaAsset` model — scoped to the tenant. The blog
editor's inline-image button and cover-image picker POST to the existing
upload endpoint and get back a URL. No new storage code needed.

---

## 6. Reusable components to build on

CSS design system in `static/css/base.css` (+ `editor.css`):
- **Buttons:** `.btn`, `.btn-primary/.btn-purple/.btn-secondary/.btn-ghost/.btn-danger`, `.btn-sm`, `.btn-block`.
- **Tables:** `.data-table` (+ `.data-table-flush`), `.cell-strong/.cell-muted/.cell-link`, `.row-actions`.
- **Badges:** `.badge-success/.badge-soft/.badge-blue/.badge-purple/.badge-danger` (use for draft/published, featured).
- **Filter bar:** `.filter-bar`, `.filter-pills`, `.filter-pill.active` (reuse for status filter on post list).
- **Cards/layout:** `.card`, `.card-flush`, `.page`, `.page-head`, `.breadcrumb`, `.stack-*`, `.row/.row-between/.row-end`, `.empty` (empty states).
- **Forms:** `.field`, `.field-label`, `.field-hint`, `.input/.textarea/.select`, `.field-status`.
- **Modal:** `.modal-overlay`, `.modal`, `.modal-header/body/footer`, `.modal-close` (Site Settings modal — pattern for any blog modal).
- **Editor shell:** `.editor-bar`, `.cms-field-richtext` (richtext look),
  `.cms-field-image` (image picker look), `.status-dot` (autosave state).

**Patterns to mirror in code:**
- Two-surface split: pk-based agency views + `…_self` tenant views sharing
  a helper (see `_render_editor`, `_save_content`, `_get_or_save_site_settings`).
- Decorate every dashboard view with exactly one of
  `agency_operator_required` / `tenant_member_required`.
- JSON `POST` endpoints with `X-CSRFToken`; `require_POST`/`require_GET`.
- Slug auto-generation with `slugify` + numeric-suffix uniqueness loop
  (see `_generate_unique_subdomain_from_name`).
- List pages: search + status filter pills + empty states (`tenant_list`).
- `humanize` (`naturaltime`) for dates; already installed.

---

## Key design decisions feeding Phase 2

1. **Blog templates are a fixed set of built-in Django templates** (chosen
   per site), *separate* from the annotated-HTML `Template` system. They are
   not parsed; they render posts directly.
2. **Blog index/detail are standalone pages** routed on the tenant host
   (`/blog/`, `/blog/<slug>/`) and via the `/site/<subdomain>/…` fallback.
3. **Homepage strip is injected into the homepage render** (marker-aware,
   with a safe fallback) — the only blog surface that touches `render_site`.
4. **Reuse:** `MediaAsset`/`_save_upload` for images; `site_settings`
   head-injection (refactored to be reusable) for per-post SEO; tenant/auth
   scoping via `request.tenant` + the two decorators.
5. **New, self-contained additions:** `BlogPost` model + migration, a
   BeautifulSoup sanitizer service, a vanilla-JS rich-text editor, blog
   public templates, and blog dashboard views/templates. No new pip deps.

---

# Live Blog Preview — Phase 1 Investigation

Audited: 2026-05-29
Goal: while editing a blog post, the client sees a live preview rendered
**exactly** as the post will appear publicly — their chosen blog style,
fonts, layout, chrome — updating as they type, without saving or
publishing. Bonus: a live homepage-strip preview on the blog list screen.

## 1. How blog templates render on the public site

- **Where the logic lives:** `core/services/blog_render.py`. The public
  detail page is produced by `render_detail(tenant, post, …)`, which calls
  Django's `render_to_string(template_path(style, "detail"), {...})`.
- **The components:** plain Django templates under `templates/blog/<style>/`
  (`minimal`, `magazine`, `cards`), each `{% extends "blog/_doc.html" %}`.
  `_doc.html` is the shared full-page skeleton — it carries `<head>`, the
  Google-Fonts links (Fraunces + Roboto), all the base blog CSS in a single
  `<style>` block, brand-accent CSS var (`--blog-accent` from the tenant's
  `brand.primary` token), the top bar, footer, and a **draft-preview
  banner**. Each style template adds its own scoped `{% block styles %}`.
- **The styles are self-contained.** There is no external stylesheet and no
  dependency on the dashboard's `base.css`/`editor.css`. A blog page renders
  standalone from `_doc.html` + the style's inline CSS. The strip templates
  (`strip.html`) are likewise self-contained `<section>` fragments with
  their own inline `<style>`.
- **Body safety:** the post body is run through `sanitize_html(post.body)`
  (`core/services/sanitizer.py`, BeautifulSoup allowlist) and injected as
  `{{ safe_body|safe }}`. Sanitization happens on **save** and again here on
  **render**.

## 2. Can these be rendered inside the dashboard? → Iframe.

The blog templates are **full HTML documents** (`<!doctype html>` … `<head>`
with its own fonts + a global `<style>` resetting `html,body`). They are
*not* clean, transplantable fragments — dropping one into the dashboard DOM
would leak its global CSS into the dashboard (and vice-versa) and fight the
dashboard's own Roboto/`base.css`. They are also Django templates, not React
components, so there is nothing to "import" into a client-side component
tree. **Conclusion: an iframe is the correct and only faithful container.**
The same `render_detail()` that serves the public page renders the preview —
single source of truth, guaranteed parity (fonts, layout, chrome, scoped
CSS). This is "Option B" from the brief, and it's not a fallback here — it's
the right call given the templates are document-level with global styles.

## 3. How the rich-text editor outputs content

- It is a **vanilla-JS `contenteditable`** (`static/js/blog_editor.js`),
  *not* TipTap/ProseMirror/React. Output is an **HTML string** (the
  editor's `innerHTML`), mirrored into a hidden `<textarea name="body">` on
  every `input`/`blur` and on submit.
- Implication for preview: the preview consumes an HTML string. Because that
  string is attacker-controllable (`contenteditable` lets a user paste
  arbitrary markup / an `<img onerror=…>`), it **must be sanitized the same
  way the public render sanitizes it** before it reaches the preview — both
  for security (self-XSS) and for fidelity (the public page strips
  `<script>`, `style=`, `class=`, unknown tags and rewrites links; an
  unsanitized preview would *not match*). See PLAN.md for how this is done
  without forking the sanitizer into JS.

## 4. Existing blog-editor layout (and what was already scaffolded)

`templates/dashboard/blog_form.html` already lays out a **two-column
split**: `.editor-shell.blog-edit` = `[form | .editor-preview]`, with the
preview pane containing a "Live preview" label, a desktop/tablet/mobile
**viewport toggle**, and an `<iframe id="blog-preview-frame">` whose `src`
is a `preview_url`. `blog_editor.js` already implements a **postMessage
bridge** (`apply-content` → patches `[data-blog-edit]` nodes in the iframe;
`focus-field` ↔ form) and reloads the iframe on a style-picker change.
`blog_preview.js` is the in-iframe half of that bridge, injected by
`_doc.html` when `preview_bridge=True`. The preview view
(`dashboard/views.py::_blog_preview`) renders `render_detail(…,
preview_bridge=True)`.

### The blocker found in audit

**The preview was scaffolded but never wired up — every blog dashboard page
currently 500s.** `_blog_nav_urls`/`_blog_post_urls` call
`reverse("dashboard:blog_preview_new"|"blog_preview"|…)`, but those four URL
patterns are **absent from `dashboard/urls.py`**. Result: `NoReverseMatch`
on the blog list *and* the editor, for both surfaces. Baseline test run:
`9 errors` in `core/tests/test_blog.py`, all the missing `blog_preview*`
reverses. So step one of Phase 3 is wiring the routes; everything else
builds on top.

### Gap analysis vs. the brief

| Brief requirement | Status before this work |
|---|---|
| Iframe preview through real render route | ✅ built (but routes unwired → 500) |
| Split view, editor left / preview right | ✅ built |
| Device toggle desktop/tablet/mobile | ✅ built |
| Style switch → preview switches | ✅ built (iframe reload w/ `?style=`) |
| "Preview" label; unsaved draft state; draft+published alike | ✅ built |
| **Toggle: hide preview / expand full-width** | ❌ missing |
| **Responsive: tab/drawer on narrow screens** | ❌ only stacks vertically |
| **Show all fields (author, date)** | ⚠️ only title/body/cover patched; no Author input in form; author/date not live |
| **Sanitize body in preview like public** | ❌ bridge patched raw `innerHTML` |
| **Homepage strip live preview (bonus)** | ❌ missing |
