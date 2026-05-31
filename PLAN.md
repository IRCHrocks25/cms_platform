# Blog Feature — Phase 2 Plan

Builds on AUDIT.md. Reuses tenant/auth scoping, `MediaAsset`/`_save_upload`
image storage, and the Site Settings head-injection. No new pip deps.

---

## Data model

### `core.models.BlogPost`

| field            | type                                   | notes |
|------------------|----------------------------------------|-------|
| `tenant`         | FK Tenant, `related_name="blog_posts"`, CASCADE | site scope |
| `title`          | CharField(200)                         | required |
| `slug`           | SlugField(200, blank)                  | auto from title, editable; **unique per tenant** |
| `cover_image`    | CharField(500, blank)                  | URL (from `MediaAsset`, like `content` stores image URLs) |
| `excerpt`        | TextField(blank)                       | auto-derived from body if empty |
| `body`           | TextField(blank)                       | **sanitized** rich HTML |
| `author`         | CharField(120, blank)                  | display name (free text) |
| `status`         | CharField(16, choices)                 | `draft` / `published`, default `draft` |
| `publish_date`   | DateTimeField(null, blank)             | set on first publish; drives ordering + display |
| `seo_title`      | CharField(200, blank)                  | falls back to `title` |
| `seo_description`| CharField(500, blank)                  | falls back to `excerpt` |
| `og_image_url`   | CharField(500, blank)                  | falls back to `cover_image` |
| `template`       | CharField(20, blank, choices)          | per-post style override; blank = inherit site default |
| `featured`       | BooleanField(default=False)            | show in homepage strip |
| `featured_order` | PositiveIntegerField(default=0)        | manual order within the strip |
| `created_at`/`updated_at` | DateTime auto                 | |

- `Meta`: `unique_together = [("tenant", "slug")]`,
  `ordering = ["-publish_date", "-created_at"]`,
  index on `(tenant, status)`.
- `save()`: ensure slug via `_unique_blog_slug(tenant, base, instance)`
  (slugify title, suffix `-2`, `-3`… on per-tenant collision).
- Helpers: `is_published` property (`status == published and publish_date set`),
  `effective_template(site_default)`, `display_excerpt()` (excerpt or first
  ~160 chars of stripped body), `resolved_seo(site_settings)` → dict for
  head injection.

### Site-level blog config: `Tenant.blog_settings` (new JSONField)

Mirrors `site_settings`. Keys:
- `template` — chosen style id (`minimal` | `magazine` | `cards`), default `minimal`.
- `title` — blog section title (default "Blog").
- `strip_enabled` — bool (default True).
- `strip_count` — int 1..6 (default 3).
- `strip_heading` — homepage strip heading (default "From the blog").

Single migration: `BlogPost` table + `Tenant.blog_settings`.

---

## Blog template styles (built-in, fixed set of 3)

Separate from the annotated-HTML `Template` system — these are plain Django
templates that render posts. One chosen per site; applies to index +
detail + strip for consistency (per-post `template` may override index/detail
for a single post).

```
templates/blog/
  _doc.html              # shared full-page skeleton (head, css var hooks)
  minimal/{index,detail,strip}.html
  magazine/{index,detail,strip}.html
  cards/{index,detail,strip}.html
```

1. **`minimal` — "Minimal Reading".** Single narrow column, generous
   whitespace, large readable serif/sans body. *Index:* stacked entries
   (date · title · excerpt). *Detail:* centered ~680px column, cover on top.
   *Strip:* simple 3-up text-forward row.
   Chosen because it's the safe default for any client and best for actual
   reading.
2. **`magazine` — "Magazine".** Editorial: bold oversized headlines, a
   lead/hero treatment. *Index:* large lead post + compact list below.
   *Detail:* full-bleed cover, strong title, comfortable measure.
   *Strip:* horizontal image cards with overlay titles.
   Chosen for content-heavy brands that want personality.
3. **`cards` — "Card Grid".** *Index:* responsive grid of cover-thumbnail
   cards. *Detail:* cover + standard column. *Strip:* 3-up card grid that
   visually matches a typical marketing homepage.
   Chosen because card grids are the most common "blog on a homepage" look.

Each template is self-contained (own scoped CSS) so blog pages render
standalone and the strip fragment injects safely into any homepage. An
accent color is pulled from the tenant's brand `--primary` token (when
present) and exposed as a CSS variable, so blog pages feel on-brand without
coupling to the homepage layout.

---

## Surfaces & routing

Public views in `core/views.py`, resolving `request.tenant` like
`root_redirect`. Shared helpers `_blog_index`/`_blog_detail`.

| URL (tenant host)        | URL (agency fallback)                 | view |
|--------------------------|---------------------------------------|------|
| `/blog/`                 | `/site/<subdomain>/blog/`             | `blog_index` |
| `/blog/<slug>/`          | `/site/<subdomain>/blog/<slug>/`      | `blog_detail` |

- **Index:** published posts only, newest first, Django `Paginator`
  (page size per style: cards 9, others 8). Empty state when no posts.
- **Detail:** single published post; **404 if draft** unless
  `tenant.user_can_edit(user)` (lets operators/owners preview). Per-post SEO
  injected into `<head>` via the reused Site-Settings mechanism.
- **Draft safety:** querysets filter `status=published, publish_date<=now`.
  Drafts never appear in index, strip, or detail for the public.
- **Unpublished site:** same gate as the homepage (`is_published` or
  `user_can_edit`) — blog 404s publicly for an unpublished site.

### Homepage strip injection

In `core/views.py::_render_tenant`, after `render_site(...)`:
`core/services/blog_render.inject_strip(html, tenant, request)`:
- If `blog_settings.strip_enabled` and there are featured published posts:
  render the chosen style's `strip.html` with the featured posts (ordered by
  `featured_order`, then `-publish_date`, capped at `strip_count`).
- Inject: if the homepage HTML contains `[data-blog-strip]`, replace its
  inner content; **else** insert the strip just before the footer
  (`[data-section="footer"]`/`<footer>`) or before `</body>`.
- No featured posts → inject nothing (no empty strip on the public site).

---

## Dashboard (two surfaces, mirrors existing split)

Shared helpers; pk-based agency wrappers (`agency_operator_required`) +
`…_self` tenant wrappers (`tenant_member_required`).

### Nav
- **Tenant host:** add **"Blog"** link (`dashboard:blog_list_self`).
- **Agency host:** Blog is per-site → add a "Blog" button on the editor bar
  and a "Manage blog" link on `tenant_detail`
  (`dashboard:blog_list` with pk).

### Post list (`templates/dashboard/blog_list.html`)
- Table: Title (link to edit) · Status badge · Publish date (`naturaltime`)
  · Featured star toggle · Actions (Edit / View ↗ / Delete).
- Filter pills All / Published / Draft; "+ New post" CTA; empty states.
- **Homepage strip panel:** template-style picker (3 options), strip
  on/off, strip count, strip heading; list of featured posts with
  **drag-to-reorder** (vanilla JS, saves via `blog_reorder`).

### Create/edit (`templates/dashboard/blog_form.html`)
- Title, slug (auto + editable, with availability hint), cover image
  (reuses `_save_upload`), excerpt, **rich-text body** (`blog_editor.js`),
  author, status + publish date, SEO (title/description/og image),
  per-post template override (Inherit + 3 styles), featured toggle.
- Body editor: `contenteditable` + toolbar (H2/H3, bold, italic, link,
  bullet/ordered list, blockquote, inline image upload). Stores `innerHTML`
  into a hidden input; **sanitized server-side on save**.

### Endpoints (`dashboard/views.py` + `dashboard/urls.py`)
Agency (pk): `sites/<pk>/blog/`, `…/new/`, `…/<post_pk>/`,
`…/<post_pk>/delete/`, `…/<post_pk>/featured/`, `…/reorder/`,
`…/settings/`.
Tenant (self): `blog/`, `blog/new/`, `blog/<post_pk>/`,
`blog/<post_pk>/delete/`, `blog/<post_pk>/featured/`, `blog/reorder/`,
`blog/settings/`.
Inline-image + cover uploads **reuse** existing `tenant_upload` /
`tenant_upload_self`.

---

## Security / sanitization

- `core/services/sanitizer.py::sanitize_html(html)` — BeautifulSoup
  allowlist (html.parser):
  - **tags:** `p, br, hr, h2, h3, h4, strong, b, em, i, u, s, a, ul, ol, li,
    blockquote, pre, code, figure, figcaption, img`.
  - **attrs:** `a`→`href,title` (+ force `rel="noopener noreferrer"` and
    `target="_blank"` on external links); `img`→`src,alt,width,height`.
    All other attributes (incl. `style`, `class`, `on*`) stripped.
  - **url schemes:** href ∈ {http, https, mailto, relative `/`, `#…`};
    img src ∈ {http, https, relative}. `javascript:`/`data:`/etc. dropped.
  - Disallowed dangerous tags (`script, style, iframe, object, embed, form,
    input, …`) removed **with contents**; other unknown tags unwrapped
    (keep text).
- Sanitize on **save** (store clean) and again defensively at **render**.
- Drafts excluded from all public querysets.

---

## Files

**New:** `core/services/sanitizer.py`, `core/services/blog_render.py`,
`static/js/blog_editor.js`, `static/css/blog.css` (dashboard editor +
shared public blog tokens, or inline per template), `templates/blog/**`,
`templates/dashboard/blog_list.html`, `templates/dashboard/blog_form.html`,
`core/migrations/0010_blogpost_tenant_blog_settings.py`,
`core/tests/test_blog.py`.

**Edited:** `core/models.py` (BlogPost + `Tenant.blog_settings`),
`core/views.py` (public blog + strip injection), `core/renderer.py`
(expose reusable head injection), `core/admin.py` (register BlogPost),
`dashboard/views.py` (blog CRUD/featured/reorder/settings),
`dashboard/urls.py` (routes), `cms_platform/urls.py` (public blog routes),
`templates/base.html` (nav), `templates/dashboard/editor.html` +
`tenant_detail.html` (entry points).

---

## Test plan (`core/tests/test_blog.py`)
1. Model: slug auto-gen; per-tenant uniqueness; two tenants share a slug.
2. Sanitizer: drops `<script>`, `onerror=`, `javascript:` href; keeps
   allowed tags/links; forces rel on external `<a>`.
3. Public index: only published, newest first, pagination; cross-tenant
   isolation; unpublished site → 404.
4. Public detail: draft 404 for anon, 200 for editor; SEO meta injected.
5. Dashboard auth: outsider 403, member 200, staff 200 (both surfaces).
6. CRUD: create/edit/delete; featured toggle; reorder persists order;
   settings save validates `strip_count`/template id.
7. Strip: injected at marker + fallback; respects order + count; absent
   when no featured posts.

---

# Live Blog Preview — Phase 2 Decision & Plan

Builds on AUDIT.md (Live Blog Preview section). No new pip deps.

## Decision: Option B — iframe preview through the real render route

Chosen **Option B (iframe)**, not Option A (in-app components). Reasoning:

- The blog "templates" are **full HTML documents** with their own `<head>`,
  Google-Fonts links, and a global `<style>` that resets `html, body`. They
  are Django templates, not transplantable React components — there is
  nothing to import into a component tree, and their global CSS would
  collide with the dashboard's own `base.css`.
- The brief's hard constraint is *"preview must match the public render."*
  The only way to guarantee that — same fonts, same scoped CSS, same chrome,
  same wrapper — is to render through the **exact same code path**
  (`blog_render.render_detail`) the public detail page uses. The preview
  view already does this (`_blog_preview` → `render_detail(…,
  preview_bridge=True)`); Option B keeps a single source of truth.
- The brief itself says: "If the public site has global styles/layout that
  won't easily transplant, B." That is exactly the situation.

**Smoothness without reloads:** a full iframe reload per keystroke would
flicker. So the iframe is **server-rendered once** (real route, real CSS),
then patched **in place** via the existing `postMessage` bridge as the user
types. A reload is used only when the *structure* changes — i.e. the style
picker switches to a different template. This is the same server-renders /
client-patches pattern the main site editor uses (CLAUDE.md).

## Work items (Phase 3)

1. **Wire the routes (unblocks everything).** Add to `dashboard/urls.py`:
   `blog_preview` + `blog_preview_new` (agency, by pk) and
   `blog_preview_self` + `blog_preview_new_self` (tenant host). Views
   already exist.

2. **Body sanitize round-trip (security + fidelity, single source of
   truth).** The bridge patched raw `innerHTML`. Instead of forking the
   BeautifulSoup allowlist into JS (which would violate "single source of
   truth" and "no new deps"), add a tiny JSON endpoint
   `blog_sanitize`/`blog_sanitize_self` that returns
   `{"html": sanitize_html(body)}`. The editor debounces body edits through
   it (~300ms) and pushes the **sanitized** HTML to the iframe — so the
   preview body is byte-identical to what the public page would render.
   Plain-text/attribute fields (title, cover `src`, author text, formatted
   date) are patched directly via `textContent`/`setAttribute` (not
   `innerHTML`), so they need no round-trip and update instantly.

3. **All fields live.** Add the missing **Author** input to
   `blog_form.html` (model + save handler already support `author`). In the
   three `detail.html` templates, give the meta line `data-blog-edit`
   markers for `author` and `publish_date`, rendered hidden-when-empty with
   a toggleable separator dot. Extend `blog_preview.js` to patch author
   (`By …` + show/hide dot) and date (formatted client-side to match
   Django's `"F j, Y"`), and `blog_editor.js` to send them.

4. **Toggle + responsive.** Add **Hide preview** and **Expand** controls.
   CSS classes on `.editor-shell.blog-edit`: `.preview-collapsed`
   (form full-width) and `.preview-expanded` (preview full-width). Persist
   the choice in `localStorage`. On narrow screens (≤900px), replace the
   side-by-side split with a **Write / Preview tab bar** (the same toggle
   mechanics, surfaced as tabs) so the preview becomes a switchable pane
   rather than a stacked block.

5. **"Updating…" affordance + debounce.** Body round-trip debounced ~300ms;
   a subtle "Updating…" state on the preview status label while a sanitize
   request is in flight, returning to "Live preview" when applied.

6. **Homepage strip live preview (bonus).** Add
   `blog_strip_preview`/`_self` returning a minimal HTML doc that wraps the
   chosen style's `strip.html` fragment, honoring **unsaved** settings
   passed as query params (`style`, `count`, `heading`, `enabled`) and the
   current featured order. On `blog_list.html`, an iframe under the strip
   settings updates on settings `input` (debounced) and after a drag-reorder
   completes. Reuses `render_strip_fragment`'s building blocks; matches the
   public strip (published-featured posts only).

## Files

**Edit:** `dashboard/urls.py` (routes), `dashboard/views.py`
(`_blog_sanitize`, `_blog_strip_preview` + 4 thin wrappers each surface,
nav-url additions), `templates/dashboard/blog_form.html` (author input,
toggle controls, responsive tab bar), `templates/dashboard/blog_list.html`
(strip preview iframe), `templates/blog/{minimal,magazine,cards}/detail.html`
(author/date markers), `static/js/blog_editor.js` (sanitize round-trip,
author/date, toggle, tabs, strip-preview reload hook), `static/js/blog_preview.js`
(author/date patching), `static/js/blog_reorder.js` (fire strip refresh),
`static/css/blog.css` (toggle states, tab bar, strip-preview frame).

**New:** none (no new files needed — all additions slot into existing
modules/templates).

## Test additions (`core/tests/test_blog.py`)
- Preview routes resolve + render 200 with the bridge script, for saved and
  unsaved (`/new/`) posts, both surfaces; `?style=` forces the style.
- `blog_sanitize` strips `<script>`/`onerror`/`javascript:` and returns
  sanitized HTML (same output as `sanitize_html`); decorated per surface.
- `blog_strip_preview` renders featured published posts, honors override
  query params, and is auth-gated.
