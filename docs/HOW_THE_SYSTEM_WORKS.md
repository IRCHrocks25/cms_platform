# How the CMS works (current system)

Operator guide for the full loop: **paste/fetch HTML → annotate → save template → create client site → edit content → add pages & blog → publish live**.

For deep architecture reuse (parser internals, Traefik TLS, portable patterns), see [`ANNOTATED_CMS_REUSE_GUIDE.md`](./ANNOTATED_CMS_REUSE_GUIDE.md).

---

## What this product is

A multi-tenant CMS where the agency pastes (or fetches) HTML, marks editable slots, and the client gets a structure-safe editor. Clients can change text, images, colors, links, etc. inside those slots.

- **Classic templates** — section list is locked. Clients cannot add or remove sections.
- **Block-shell templates** — clients may add / reorder / remove blocks from a curated **Quick Add** drawer: agency-designed sections *and* builder primitives (rows, headline, paragraph, image, button, form), including nesting blocks inside row columns. They still cannot paste raw HTML or invent CSS — every block is one the agency ships.

| Role | Host | What they do |
|------|------|----------------|
| Agency operator | Agency host (`sites.katek.app`, no tenant subdomain) | Templates, new clients, page HTML, users, custom domains |
| Client | Tenant host (`client.sites.katek.app`) | Edit content, publish home/pages, manage blog posts |

Same `/dashboard/` path — which surface you see depends on the **Host** header (middleware resolves the tenant from the subdomain).

---

## Big picture

```
  Agency pastes / fetches HTML
            │
            ▼
  Optional AI Annotate  ──▶  data-section / data-edit markup
            │
            ▼
  Save Template  ──▶  parser builds schema (JSON)
            │
            ▼
  Create client site (Tenant + User + Membership)
            │
            ├─ Home: Tenant.template + Tenant.content  →  /
            ├─ Pages: Page rows (own template each)    →  /services/, /about/, …
            └─ Blog:  BlogPost rows                    →  /blog/, /blog/<slug>/
            │
            ▼
  Client edits in split-view editor (live preview)
            │
            ▼
  Publish each asset  ──▶  public visitors see it
```

---

## 1. Upload / ingest HTML

### Option A — Template library

1. Agency → **Templates** → **New template** (`/dashboard/templates/new/`).
2. Provide HTML one of three ways:
   - **Paste** annotated (or raw) HTML into the textarea.
   - **Fetch from URL** — pulls the page; if it’s a JS SPA shell, can headless-render and inline assets.
   - Start from the built-in **starter** skeleton (hero + brand tokens).
3. Optionally run **AI Annotate** (see §2).
4. **Save** — Django stores `html_source` and always rebuilds `schema` via `build_schema()` in `core/parser.py`.

### Option B — Inline on new client

When creating a site (`/dashboard/sites/new/`), you can paste/fetch/annotate HTML **in the form** instead of picking a library template. That HTML becomes a tenant-owned template.

### What “schema” means

The schema is a JSON description of every editable section/field derived from the HTML. It is **never hand-edited** — saving the template regenerates it. The editor UI is generated from this schema.

---

## 2. AI annotation

Raw marketing HTML usually has no `data-*` slots. **Annotate** adds them so the editor can bind fields.

### Operator steps

1. On the template form (or new-client inline HTML), click **AI Annotate**.
2. The server creates an async **AnnotationJob** and returns a job id (avoids proxy timeouts on large pages).
3. The UI polls until the job finishes.
4. A **side-by-side compare overlay** opens: left = your input, right = AI output (editable).
5. You **Apply** or **Discard**. Annotated HTML never silently overwrites the textarea.

### What the pipeline does (high level)

1. Strip `<style>` / `<script>` (and large data URIs) so the model doesn’t burn tokens on CSS.
2. Chunk the HTML, call OpenAI, merge annotations back by element refs.
3. Restore styles/scripts; auto-add `data-tokens` on `:root` CSS variables when appropriate.
4. Validate with `build_schema()` — if no real editable sections appear, the job errors with diagnostics.

Model: `OPENAI_ANNOTATE_MODEL` (env). Larger/complex pages often need a stronger model.

---

## 3. Annotation DSL (the markup the system reads)

```html
<section data-section="hero"
         data-label="Welcome banner"
         data-group="Home">

  <h1 data-edit="hero.title"
      data-type="text"
      data-label="Headline">Welcome</h1>

  <div data-edit="hero.body" data-type="richtext">…</div>
  <img data-edit="hero.image" data-type="image" src="…">
  <a data-edit="hero.cta" data-type="link" href="…">Book now</a>
</section>

<style data-tokens>
  :root {
    --primary: #b91c1c;  /* becomes a Brand color field */
    --bg: #fffaf3;
  }
</style>
```

| Attribute | Purpose |
|-----------|---------|
| `data-section` | Section id (unique in the template) |
| `data-label` / `data-group` | Friendly name / sidebar grouping in the editor |
| `data-edit` | Field id: `section.field` |
| `data-type` | `text` \| `richtext` \| `image` \| `color` \| `link` \| `video` \| `ghl-embed` |
| `data-tokens` | Expose CSS variables as Brand fields |
| `data-blog-strip` | Optional mount point for the homepage blog strip |

**Product rule:** clients only edit these slots. On a classic template the section list stays locked. On a **block shell** they may compose the page from allowlisted sections and builder primitives, and nest blocks inside row columns (see §6a), but still cannot author raw HTML.

---

## 4. Create a client site

1. Agency → **+ New client** (`/dashboard/sites/new/`).
2. Fill site name, subdomain (auto-derived if blank), client username/email.
3. Choose a **library template** or paste **inline HTML** (annotate first if needed).
4. Submit — atomically creates:
   - Django **User** (client)
   - **Tenant** (subdomain, template, content seeded from template defaults)
   - **TenantMembership** (`owner`)
5. You get a **one-time password** screen (session-stashed, ~10 minutes). Copy it — it’s not stored in plaintext in the DB.

**Live URL:** `https://<subdomain>.sites.katek.app/`

**Note:** New sites are typically **published on create** for the homepage. Extra pages are **not** (see §6).

---

## 5. Editing content (home)

### Where

| Who | URL |
|-----|-----|
| Client | `https://<sub>.sites.katek.app/dashboard/` (editor) |
| Agency | `/dashboard/sites/<id>/edit/` |

### Layout

Three columns (adaptive by section count):

1. **Sidebar** — sections from schema (grouped).
2. **Form** — fields by type (`text`, `richtext`, image upload, color, link, …).
3. **Live preview** — iframe of the rendered site; form ↔ preview sync via `postMessage`.

### Behavior

- **Autosave** ~600ms after edits.
- Clicking a field in the preview focuses the matching form control (and vice versa).
- **Publish** on the home editor toggles `Tenant.is_published`.
- **Media** uploads go to Iceberg CDN (when configured); gallery is available on both surfaces.
- Clients can only edit when the template is in **editable** mode with a real schema (raw-only HTML is agency-side).

Tabs typically include Content / Brand / Navigation / Design depending on what the schema exposes.

---

## 6. Pages (extra URLs like `/services/`)

The **homepage** is special: it lives on the Tenant (`template` + `content` + `is_published`).

Any other URL is a **Page** row:

| | Home | Inner page |
|--|------|------------|
| Storage | `Tenant` | `Page` (+ its own `Template`) |
| Public URL | `/` | `/<slug>/` e.g. `/services/` |
| Default publish | Usually published at site create | **Draft (`False`) — must Publish** |

### Create a page (agency)

1. Site detail → **Pages** → **New page**.
2. Title, **slug** (this becomes the URL path), paste annotated HTML.
3. Save → creates a tenant-owned Template + Page.
4. Click **Publish** on the pages list (or the page will 404 for the public).

Agency can also **Edit HTML** on a page (structure/annotation). On a classic (non-shell) site, clients edit **content** only and cannot add/remove pages. On a **block-shell** site, clients may create extra pages that share the shell and compose them from the palette (no HTML paste).

### Import siblings

Agency can **import sibling pages** from a source site URL. Discovery currently finds same-origin links that end in **`.html` / `.htm` only** — clean paths like `/services/` are not auto-imported. Imported pages still start **unpublished**.

### Public URLs

- Tenant host: `https://client.sites.katek.app/services/`
- Agency fallback: `https://sites.katek.app/site/client/services/`

Unpublished pages → **404** for anonymous visitors. Logged-in editors/staff can still preview them.

---

## 6a. Block editor (curated sections + builder primitives)

The block editor is the same split-view editor with a **Quick Add** drawer of insertable blocks. There are now two families of block:

- **Agency sections** (category **Sections**) — whole designed sections, the classic curated palette.
- **Builder primitives** (categories **Rows / Text / Media / Elements / Form**) — a GHL-lite set clients can compose with: layout rows (`row-1`…`row-6`), Headline, Sub-headline, Paragraph, Bullet list, Rich text, Image, Button, and a GHL Form embed. Seed them with `python manage.py seed_builder_blocks`.

Clients still **cannot** paste raw HTML or invent CSS — every insertable piece is a `BlockType` the agency ships and allowlists on the template. What changed vs. the old rule: on a block shell they may now *compose* a page from primitives and nest blocks inside row columns.

A template is a **block shell** when its HTML contains a `data-region` slot (usually `data-region="main"`). Body sections become `BlockType` rows; the shell keeps nav/footer chrome. Detection: `Template.is_block_shell`.

### Nested layout (rows + columns)

Layout blocks (rows) carry nested `data-region` column slots (`col1`…`col6`). A block instance may therefore hold `children`:

```json
{ "id": "blk_row1", "type": "row-2", "fields": {},
  "children": { "col1": [ {"id": "blk_h1", "type": "headline", "fields": {"text": "Hi"}} ],
                "col2": [ {"id": "blk_p1", "type": "paragraph", "fields": {}} ] } }
```

- The renderer (`core/renderer.py::_assemble_instance`) walks the tree, filling each row's columns recursively.
- Save-time validation (`dashboard/views.py::_normalize_regions`) recurses too: it rejects block types not on the allowlist, drops unknown fields, remints duplicate ids, counts **every** nested instance toward `MAX_BLOCKS_PER_PAGE` (40), and caps nesting at `blocks.MAX_BLOCK_DEPTH` (2) so pages cannot nest rows forever.
- Existing converted sites (no `children`) render exactly as before.

### Seed + allowlist the builder primitives

```bash
python manage.py seed_builder_blocks                       # create/refresh primitives
python manage.py seed_builder_blocks --attach <tpl>        # allowlist on one template (pk/slug/name)
python manage.py seed_builder_blocks --attach-all-shells   # allowlist on every block shell
```

### Convert a classic template

Dry-run, then apply:

```bash
python manage.py migrate_template_to_blocks <template-pk-or-name>
python manage.py migrate_template_to_blocks <template-pk-or-name> --apply
```

The command refuses to apply if a page’s new render is not byte-identical unless you pass `--force`. Original content is stored under `_classic` on each converted row.

### In the UI

1. Open the site editor (`/dashboard/sites/<id>/edit/` or the tenant `/dashboard/editor/`).
2. Left **Sections** sidebar → **Add section** opens the **Quick Add** drawer (only when `block_mode` and the template is client-editable).
3. The drawer has a category rail (Quick Add / Rows / Text / Media / Elements / Form / Sections), a search box, and an **Add to** dropdown that picks the destination — the page bottom or any row column already on the page.
4. Click a card to insert (or drag it onto a form section: dropping on a row targets its first column, dropping on a leaf inserts into that block's container). Reorder within a container with the ▲▼ controls or by dragging a section's header; nested blocks are indented in the form list.
5. **Tools → Pages** — on a shell site, clients can add inner pages that share the shell.

Palette labels come from each block’s `data-label` (and grouping from `data-category` or `data-group`). If cards show raw ids like `caseStudy`, re-save the `BlockType` rows so `BlockType.save()` copies the human label from the HTML.

Local Windows clone notes (venv, SQLite, demo conversion): [`LOCAL_SETUP_AND_BLOCK_EDITOR.md`](./LOCAL_SETUP_AND_BLOCK_EDITOR.md).

---

## 7. Blog

Blog is separate from annotated page templates. Posts are `BlogPost` rows; the public index/detail use Django blog templates wrapped in the site’s chrome (nav/footer from the home HTML).

### Agency / client

From the site’s **Blog** section:

- Create / edit / delete posts
- Draft vs **Published** (publishing stamps `publish_date`)
- Feature posts + reorder for the homepage strip
- Blog settings: index style, strip style/count/heading, blog title

### Public URLs

| | Index | Post |
|--|-------|------|
| Tenant | `/blog/` | `/blog/<slug>/` |
| Agency fallback | `/site/<sub>/blog/` | `/site/<sub>/blog/<slug>/` |

The **site** must be published (or you’re an editor) for blog routes to show. Draft posts are editor-only.

### Homepage blog strip

If featured published posts exist, a strip is injected into the home (and pages) — into `[data-blog-strip]` if present, otherwise before the footer.

---

## 8. How the public site is shown

1. Request hits Traefik → Django.
2. **TenantResolverMiddleware** sets `request.tenant` from:
   - Subdomain of `TENANT_BASE_DOMAIN` (e.g. `ramesh-gogineni.sites.katek.app`), or
   - A verified **custom domain**.
3. Routing:
   - `/` → home (`Tenant`)
   - `/<slug>/` → `Page` with that slug
   - `/blog/…` → blog index/detail
4. Renderer: classic pages merge content with template defaults (`merge_with_defaults`) then `render_site`. Block shells render instances from `content.regions` via `render_page_from_blocks` (`core/services/blocks.py` + `core/renderer.py`).
5. Blog strip may be injected last.

### Visibility cheat sheet

| Asset | Public visitor | Editor / staff |
|-------|----------------|----------------|
| Home unpublished | Hidden / 404 | Can view |
| Page unpublished | **404** | Can view |
| Blog when site unpublished | 404 | Can view |
| Draft blog post | 404 | Can view |

---

## 9. Custom domains (brief)

1. Agency adds a domain on the site.
2. Client points DNS (A record) at the configured origin IP.
3. After verification, Traefik gets a per-domain route + Let’s Encrypt cert.
4. Same Django app; middleware resolves the tenant by hostname.

Details: [`deploy/DOKPLOY.md`](../deploy/DOKPLOY.md).

---

## 10. Typical agency checklist

1. **Ingest HTML** — paste or fetch; AI-annotate; save template (or do this inline on new client).
2. **Create site** — new client; copy one-time password; confirm subdomain loads.
3. **Add pages** — new page or import `.html` siblings; set correct slugs; **Publish each page**.
4. **Hand off** — client logs in on `sub.sites.katek.app`, edits content, publishes.
5. **Blog** (optional) — settings + posts; feature for homepage strip; publish posts.
6. **Custom domain** (optional) — add, verify DNS, wait for cert.

---

## Sharp edges (read once)

- **Publishing home ≠ publishing pages.** Each page has its own Publish button; drafts 404 publicly.
- **Sibling import only finds `*.html` / `*.htm` links**, not extensionless `/about/` paths.
- **Classic sites: clients cannot add/remove pages or sections.** Block-shell sites: clients may stack allowlisted sections and add pages that share the shell — still no raw HTML.
- **Annotate is async**; always Apply/Discard in the compare overlay.
- **Schema follows HTML** — don’t edit `Template.schema` in the DB by hand.
- **Media needs Iceberg** env config or uploads fail.
- **AJAX subdomain check** doesn’t lowercase; the create form does — prefer lowercase subdomains in the UI.

---

## Key files (for developers)

| Area | Location |
|------|----------|
| Parse annotated HTML → schema | `core/parser.py` |
| Schema + content → HTML | `core/renderer.py` |
| AI annotate | `core/services/annotator.py` |
| Subdomain / custom domain host | `core/middleware.py` |
| Public home / pages / blog | `core/views.py`, `cms_platform/urls.py` |
| Agency + client dashboards | `dashboard/views.py`, `dashboard/urls.py` |
| Editor UI + bridge | `templates/dashboard/editor.html`, `static/js/editor.js` |
| Models (Template, Tenant, Page, BlogPost, BlockType) | `core/models.py` |
| Curated block palette / shell convert | `core/services/blocks.py`, `core/management/commands/migrate_template_to_blocks.py` |
| Builder primitives (rows/text/image/button/form) | `core/management/commands/seed_builder_blocks.py`; nested render in `core/renderer.py::_assemble_instance`; validation in `dashboard/views.py::_normalize_regions`; Quick Add drawer in `static/js/editor.js` + `templates/dashboard/editor.html` |
| URL fetch / sibling discovery | `core/services/url_fetch.py` |
| Blog strip / chrome | `core/services/blog_render.py` |

---

## Related docs

- [`LOCAL_SETUP_AND_BLOCK_EDITOR.md`](./LOCAL_SETUP_AND_BLOCK_EDITOR.md) — this clone: Python 3.12 venv, SQLite, demo block-shell conversion
- [`ANNOTATED_CMS_REUSE_GUIDE.md`](./ANNOTATED_CMS_REUSE_GUIDE.md) — architecture deep dive for rebuilding patterns elsewhere
- [`../CLAUDE.md`](../CLAUDE.md) — AI assistant project guide (may lag newer features slightly)
- [`../deploy/DOKPLOY.md`](../deploy/DOKPLOY.md) — Traefik / deploy / TLS
