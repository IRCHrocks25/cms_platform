# Annotated Multi-Tenant CMS — Architecture Reuse Guide

A deep dive into how this platform turns **pasted HTML** into a locked-structure
client editor, creates **subdomains instantly**, and connects **custom domains**
with real TLS — so you can rebuild the same patterns in other projects.

This is implementation truth from the codebase, not marketing copy.

---

## Table of contents

1. [The product thesis (why this works)](#1-the-product-thesis-why-this-works)
2. [End-to-end lifecycle](#2-end-to-end-lifecycle)
3. [The annotation DSL](#3-the-annotation-dsl)
4. [Parser → schema (derived, never hand-edited)](#4-parser--schema-derived-never-hand-edited)
5. [Content blob + renderer](#5-content-blob--renderer)
6. [AI annotation pipeline (paste raw HTML → annotated)](#6-ai-annotation-pipeline-paste-raw-html--annotated)
7. [Instant subdomain tenancy](#7-instant-subdomain-tenancy)
8. [Custom domain connection + TLS](#8-custom-domain-connection--tls)
9. [Two dashboards, one URL path](#9-two-dashboards-one-url-path)
10. [New-client onboard (atomic create)](#10-new-client-onboard-atomic-create)
11. [Live editor bridge](#11-live-editor-bridge)
12. [Auth, memberships, one-time credentials](#12-auth-memberships-one-time-credentials)
13. [Portable patterns checklist](#13-portable-patterns-checklist)
14. [Gotchas to copy knowingly](#14-gotchas-to-copy-knowingly)
15. [Minimal rebuild map (other stacks)](#15-minimal-rebuild-map-other-stacks)

---

## 1. The product thesis (why this works)

Most “client CMS” products fail in one of two ways:

- **Too free** — clients break layout (WordPress page builders, raw HTML editors).
- **Too rigid** — agency rebuilds every change in code.

This system sits in the middle:

> Agency pastes (or AI-annotates) HTML with `data-*` slots.  
> Schema is **derived** from those slots.  
> Client edits **only** those slots.  
> Structure is locked. Subdomain goes live on create. Custom domain is optional later.

**Positioning:** Squarespace ease + agency-built quality + locked-structure safety.

**Non-goals that protect the product promise:**

| Do not add | Why |
|------------|-----|
| Client section add/remove | Breaks the “can’t break the site” promise |
| Client raw HTML editing | Richtext is the only markup clients touch |
| Hand-edited schema rows | Schema must equal `build_schema(html)` |
| Per-field DB columns | Templates have variable shapes → JSON blob |
| Casual new frontend frameworks | Keep the stack tiny and predictable |

---

## 2. End-to-end lifecycle

```
┌─────────────────┐   paste / fetch / AI-annotate   ┌──────────────────┐
│ Agency operator │ ───────────────────────────────▶│ Template         │
│ (agency host)   │                                 │ html_source      │
└────────┬────────┘                                 │ schema = JSON    │
         │                                          └────────┬─────────┘
         │ create client (User + Tenant + Membership)        │
         ▼                                                   │ defaults seed
┌─────────────────┐                                         │
│ Tenant          │◀────────────────────────────────────────┘
│ subdomain       │
│ content = JSON  │
│ is_published    │
└────────┬────────┘
         │
    ┌────┴─────┬──────────────────────┐
    ▼          ▼                      ▼
 Client     Public visitor         Custom domain
 editor     on sub.host            (verified A → origin)
```

**Key files in this repo:**

| Concern | Path |
|---------|------|
| Annotation → schema | `core/parser.py` |
| Schema + content → HTML | `core/renderer.py` |
| AI annotate | `core/services/annotator.py` |
| Host → tenant | `core/middleware.py` |
| Models | `core/models.py` |
| Agency + client UI | `dashboard/views.py` |
| Custom domain Traefik sync | `core/services/traefik_routes.py` |
| Deploy / TLS topology | `deploy/DOKPLOY.md` |
| Sample annotated HTML | `samples/restaurant.html` |

---

## 3. The annotation DSL

HTML is the source of truth. Attributes are a tiny DSL the parser understands.

### 3.1 Section wrapper

```html
<section
  data-section="hero"           <!-- unique id per template -->
  data-label="Welcome banner"   <!-- friendly name in editor -->
  data-icon="star"              <!-- optional icon hint -->
  data-group="Home">            <!-- sidebar grouping -->

  <!-- editable fields live INSIDE this wrapper -->

</section>
```

### 3.2 Editable fields

```html
<h1 data-edit="hero.title"
    data-type="text"
    data-label="Headline">Cooking from the heart of Sicily</h1>

<img data-edit="hero.image" data-type="image" data-label="Hero photo" src="...">

<a data-edit="hero.cta" data-type="link" data-label="Button" href="#menu">Reserve</a>
```

**ID format is load-bearing:** always `<section>.<field>` (dotted). Same string
appears in schema, content JSON, form names, and `postMessage` payloads.

### 3.3 Field types → what they read/write

| `data-type` | Reads / writes |
|-------------|----------------|
| `text` | Element text content |
| `richtext` | Inner HTML (sanitized; contenteditable in editor) |
| `image` | `src` (also clears `srcset` / picture sources on apply) |
| `color` | Inline `background-color` (or `color` on `<span>`) |
| `link` | `href` |
| `video` | `src` or first `<source src>` |

Optional: `data-style="off"` disables per-element style UI for text/richtext/link.

### 3.4 Brand tokens

```html
<style data-tokens>
  :root {
    --primary: #b91c1c;
    --bg: #fffaf3;
    --text: #1f2937;
  }
</style>
```

Any `--name: value;` inside a `data-tokens` style becomes a Brand field
(`brand.primary`, etc.). Colors (`#…` / `rgb…`) get type `color`; everything else
`text`. Clients change brand colors without touching CSS structure.

### 3.5 Mental model

```
Annotated HTML  ──build_schema()──▶  schema JSON
                                      │
Tenant.content  ──merge_with_defaults()──▶  merged map
                                      │
                    render_site()  ──▶  public / preview HTML
```

---

## 4. Parser → schema (derived, never hand-edited)

**File:** `core/parser.py`  
**Entry:** `build_schema(html: str) -> dict`

### 4.1 Output shape

```json
{
  "sections": [
    {
      "id": "hero",
      "label": "Welcome banner",
      "icon": "star",
      "group": "Home",
      "fields": [
        {
          "id": "hero.title",
          "label": "Headline",
          "type": "text",
          "default": "Cooking from the heart of Sicily",
          "style_editable": true
        }
      ]
    }
  ],
  "defaults": {
    "hero": { "title": "Cooking from the heart of Sicily", "image": "https://..." }
  },
  "link_targets": [{ "value": "#menu", "label": "Menu" }],
  "theme_tokens": [{ "name": "primary", "label": "Primary", "value": "#b91c1c" }]
}
```

### 4.2 Algorithm (reuse this order)

1. Parse with BeautifulSoup + lxml.
2. Extract **Brand** section from `<style data-tokens>` via regex
   `--([a-zA-Z0-9_-]+)\s*:\s*([^;]+);`.
3. Walk every `[data-section]`; collect nested `[data-edit]` whose id prefix
   matches the section (`hero.title` inside `data-section="hero"`).
4. Unknown `data-type` → fall back to `"text"`.
5. Build `defaults[section][field]` from current DOM values.
6. Collect in-page `#anchor` link targets for editor dropdowns.
7. Detect theme CSS variables separately (Design tab recolor via `content["_tokens"]`).

### 4.3 Critical invariant

```python
# core/models.py — Template.save()
self.schema = build_schema(self.html_source)
```

**Never** let `Template.schema` drift from HTML. If you need caching, cache inside
the parser — do not hand-edit schema rows in admin.

### 4.4 Why this ports well

Any language can implement the same contract:

- Input: HTML string  
- Output: `{ sections, defaults, ... }`  
- UI and renderer both consume that JSON  
- No per-template code

---

## 5. Content blob + renderer

### 5.1 Content is a JSON document on the tenant

```python
# Tenant.content example
{
  "hero": { "title": "New headline", "image": "/media/..." },
  "nav": { "brand": "Acme Co" },
  "_hidden": ["menu"],           # meta: hide sections/fields
  "_styles": { "hero.title": { "color": "#111", "fontSize": "48px" } },
  "_global": { "fontFamily": "Georgia", "pageBg": "#fff" },
  "_tokens": { "primary": "#0ea5e9" }   # theme CSS var overrides
}
```

**Convention:** keys starting with `_` are editor meta, not content sections.
`merge_with_defaults()` copies them verbatim and merges real sections with
template defaults (`setdefault` then `update`).

### 5.2 Always merge before render

```python
merged = merge_with_defaults(schema, tenant.content)
html = render_site(template.html_source, merged, preview=False, site_settings=...)
```

Empty client fields fall back to template defaults. Never compute display values
that bypass this helper.

### 5.3 `render_site` order (copy this pipeline)

1. Parse template HTML.
2. Apply `content["brand"]` → rewrite CSS vars in `data-tokens` styles.
3. For each `[data-edit]` (skip `brand.*`): look up value → `_apply_field`.
4. Inject overlays from `_styles` / `_global` / `_tokens`.
5. Apply `_hidden` (public: `display:none`; preview: dim only).
6. Public: inject site settings (title, meta, OG, analytics).
7. Preview: inject JS bridge into `<body>`.

### 5.4 Field apply rules worth stealing

| Type | Detail |
|------|--------|
| Image | Clear `srcset`, `data-srcset`, and `<picture><source>` or the swap is invisible |
| Richtext | Sanitize; flatten block tags when host is phrasing (`span`, `a`, `h1`…) |
| Color | `span` → `color`; else `background-color` |
| No-op | Skip write if value equals current (avoid dirty DOM / flicker) |

### 5.5 Two preview modes — do not conflate

| Mode | Where | How |
|------|-------|-----|
| **Editor preview** | Client/agency editor | Server `render_site(preview=True)` + postMessage bridge |
| **Author preview** | Template form | Client `iframe.srcdoc = textarea.value` only — no substitution |

---

## 6. AI annotation pipeline (paste raw HTML → annotated)

**File:** `core/services/annotator.py`  
**Problem:** Sending a full real-world page to an LLM fails — CSS/scripts blow
the token budget and outputs truncate.

### 6.1 Strip → annotate-by-ref → restore

Current pipeline (JSON-by-ref, not “echo the whole HTML”):

```
raw HTML
  │
  ├─ strip <style>/<script> → <!--__BLOCK_n__--> placeholders
  ├─ strip data: URIs → __DATAURI_n__ placeholders
  ├─ reject if still too large (ANNOTATE_MAX_INPUT_CHARS)
  ├─ stamp every element with data-cms-ref="N"
  ├─ chunk top-level blocks (~40k chars)
  ├─ parallel OpenAI calls → JSON { sections, fields } keyed by ref
  ├─ merge chunks (rename colliding section ids)
  ├─ apply annotations onto DOM; drop refs
  ├─ backfill missed headings/paragraphs
  ├─ restore data URIs, then style/script blocks
  ├─ auto-add data-tokens on <style> that has :root { --var: }
  └─ validate with build_schema() — must have real sections
```

**Why JSON-by-ref beats “rewrite the document”:**

- Model output size ∝ number of annotations, not page size.
- Styles/scripts never enter the model context.
- Placeholders survive if the system prompt forbids touching them.

### 6.2 Async jobs (production reality)

Large pages time out if annotation runs inside the HTTP request. Pattern used:

1. `POST` creates `AnnotationJob` (UUID, status machine).
2. Daemon / worker thread runs `annotate_html`.
3. Client polls status endpoint.
4. Stale jobs expire after a TTL (~300s).
5. UI shows a **side-by-side compare overlay** — annotated HTML never silently
   overwrites the operator’s textarea.

### 6.3 Reuse rules

- Keep placeholder format and system prompt in lockstep.
- Restore order: data URIs **before** style/script blocks.
- Do **not** ask the model to emit `data-tokens` — restore adds it.
- On validation failure, return diagnostics: model name, `finish_reason`,
  output length, first ~500 chars of response (otherwise you cannot debug LLMs).

---

## 7. Instant subdomain tenancy

This is the “site exists the second you click Create” trick.

### 7.1 Data model

```python
class Tenant(models.Model):
    name = ...
    subdomain = models.SlugField(unique=True)  # e.g. "acme"
    template = models.ForeignKey(Template, on_delete=PROTECT)
    owner = models.ForeignKey(User, ...)       # CLIENT user, not creating staff
    content = models.JSONField(default=dict)
    is_published = models.BooleanField(default=False)
    # custom_domain CharField may still exist as a vestigial helper —
    # resolution uses the CustomDomain table (see §8).
```

On create:

```python
Tenant.objects.create(
    ...,
    content=template.schema.get("defaults", {}) or {},
    is_published=True,  # live immediately on the public host
)
```

Subdomain is reserved, validated, and unique **before** insert. The public URL
is just DNS + middleware — no extra provisioning API for the subdomain itself.

### 7.2 Host resolution middleware

**File:** `core/middleware.py` → `TenantResolverMiddleware`

Every request gets `request.tenant`:

```
1. Normalize host (strip port, lowercase)
2. Prefer first X-Forwarded-Host when present (proxies rewrite Host)
3. Build base list: TENANT_BASE_DOMAIN (+ additional + DEBUG localhost/lvh.me)
4. Bare base → agency (tenant = None)
5. Exact pattern <sub>.<base> with no extra dots:
     - reserved sub → agency (None), NEVER fall through to custom domain
     - else Tenant.objects.filter(subdomain=sub).first()
6. Else CustomDomain where domain=lookup_host AND is_verified=True
7. Else None
```

**Reserved subdomains** (agency / infra — keep as a settings set):

```python
TENANT_RESERVED_SUBDOMAINS = {
    "www", "app", "api", "admin", "dashboard", "static", "media", "mail",
}
```

### 7.3 Dev vs prod DNS

| Env | Base | How `acme.<base>` resolves |
|-----|------|----------------------------|
| Local | `localhost` | Browsers resolve `*.localhost` (RFC 6761) |
| Local fallback | `lvh.me` | Public DNS → `127.0.0.1` |
| Prod | e.g. `sites.example.com` | Wildcard DNS `*.sites.example.com` → origin |

**Settings that matter:**

| Setting | Role |
|---------|------|
| `TENANT_BASE_DOMAIN` | Primary base (comma-list: first = primary) |
| `TENANT_ADDITIONAL_BASE_DOMAINS` | Extra bases |
| `TENANT_DEV_BASE_DOMAIN` | DEBUG-only (`lvh.me`) |
| `USE_X_FORWARDED_HOST` | Trust proxy host |
| `ALLOWED_HOSTS=["*"]` | Host allowlist owned at the edge (Traefik), not Django |

### 7.4 Edge routing for agency + tenants (Traefik example)

Agency apex + tenant wildcard are **compose labels**, not per-tenant rows:

| Router | Rule idea | Priority |
|--------|-----------|----------|
| Apex | exact agency host | high (100) |
| Tenants | single-label subdomain of base | lower (10) |

On older Traefik (< 3.7), use `HostRegexp` instead of `Host(\`*.base\`)`.
Reserved labels still match the wildcard router but Django maps them to agency.

**Important:** do **not** use a catch-all `HostRegexp(.+)` on a shared host —
you would steal traffic from other apps. Custom domains get per-domain routers (§8).

### 7.5 Why “instant”

Creating a `Tenant` row with a unique `subdomain` is enough:

1. Middleware can resolve it on the next request.
2. Wildcard DNS already points `*.base` at the app.
3. Wildcard / origin cert already covers `*.base`.
4. No Cloudflare hostname API, no per-tenant cert for subdomains.

Custom domains are the slow path; subdomains are the fast path.

---

## 8. Custom domain connection + TLS

Clients eventually want `www.clientbrand.com`. That is a **separate** system from
subdomains.

### 8.1 Model

```python
class CustomDomain(models.Model):
    tenant = models.ForeignKey(Tenant, related_name="custom_domains", ...)
    domain = models.CharField(unique=True)  # globally unique
    is_verified = models.BooleanField(default=False)
    # Multiple domains per tenant (apex + www as separate rows)
```

### 8.2 Operator lifecycle

```
Add domain (normalize, validate regex, unique)
        │
        ▼
Pending — show DNS instructions:
  A record @ or leftmost label → CUSTOM_DOMAIN_TARGET_IP
        │
        ▼
Verify (socket.getaddrinfo A records contain target IP)
        │
        ▼
is_verified=True
        │
        ▼
route-syncer (≤20s loop) regenerates Traefik dynamic file
        │
        ▼
Traefik Host(`domain`) + certResolver=letsencrypt
  → HTTP-01 ACME → real public cert
```

**Force-verify** exists for ops escape hatches; normal path is DNS check.

### 8.3 Two TLS models (do not mix them up)

| Host class | DNS | TLS at origin |
|------------|-----|---------------|
| Agency + `*.TENANT_BASE_DOMAIN` | Often Cloudflare | Origin cert / CF Full; **avoid** LE ACME on these routers |
| Client custom domain | Client A → origin IP | Traefik `certResolver=letsencrypt` (HTTP-01) |

Agency routers may deliberately use `HostRegexp` so Traefik **cannot** extract a
domain for ACME and falls back to the default-store origin cert. Custom-domain
routers use exact `Host(\`domain\`)` so ACME **does** fire.

### 8.4 Route syncer pattern (shared-host safe)

**File:** `core/services/traefik_routes.py`

```
sync_custom_domain_routes():
  if TRAEFIK_DYNAMIC_DIR unset → no-op (web/dev)
  load all CustomDomain where is_verified=True
  skip PROTECTED_HOSTS and anything under TENANT_BASE_DOMAIN
  build routers:
    cms-cd-<pk>        → websecure + tls.certResolver=letsencrypt
    cms-cd-<pk>-web    → web + redirect-to-https middleware
  write custom-domains.yml atomically (tempfile + os.replace)
  skip write if content unchanged (no Traefik reload thrash)
```

**Security / ops properties worth copying:**

1. **No catch-all** on a shared box — only claim domains you verified.
2. **Web container has no Traefik mount** — only an isolated `route-syncer` writes.
3. **File must be `.yml`** — Traefik ignores `.json` by extension; content can
   still be JSON (JSON ⊂ YAML) to avoid a YAML dependency.
4. **Atomic replace** in the watched directory.
5. **Denylist** infrastructure hosts even if a bad row is verified.

### 8.5 Cookies on custom domains

If session/CSRF cookies are scoped to `.TENANT_BASE_DOMAIN` for subdomain SSO /
embeds, browsers **reject** that Domain attribute on `clientbrand.com`.

Fix used here (`PartitionedCookieMiddleware._scope_cookie_domain`):

- On parent / `*.parent` hosts → keep `Domain=.parent`.
- On any other host (custom domains) → clear Domain → host-only cookies.

Without this, custom-domain login CSRF-fails mysteriously.

### 8.6 CSRF trusted origins

Verified custom domains must be allowed for HTTPS POSTs. Pattern: lazy-extend
`CSRF_TRUSTED_ORIGINS` from the `CustomDomain` table (cache per worker; recycle
workers after new verifications if cross-origin trust is required).

---

## 9. Two dashboards, one URL path

Same path `/dashboard/` — different surfaces by host.

| Host | `request.tenant` | Surface | Who |
|------|------------------|---------|-----|
| `app` / bare base / reserved | `None` | Agency operator dashboard | staff / superuser |
| `acme.base` or verified custom domain | Tenant | Client editor | membership **or** staff |

### 9.1 Dispatch

```python
def dashboard_root(request):  # undecorated
    if request.tenant is not None:
        return tenant_home(request)   # @tenant_member_required
    return agency_home(request)       # @agency_operator_required
```

### 9.2 Decorators (always pick exactly one)

| Decorator | Requires | Failure modes |
|-----------|----------|---------------|
| `agency_operator_required` | tenant is None + staff | redirect / 403 |
| `tenant_member_required` | tenant set + member or staff | `no_access.html` 403 |

There is no “either side” view. New dashboard endpoints must choose a surface.

### 9.3 Login is host-aware

`TenantAwareLoginView`:

- Tenant host → allow member or staff; refuse others.
- Agency host → allow staff; non-staff members bounce to their tenant editor.

---

## 10. New-client onboard (atomic create)

**File:** `dashboard/views.py` → `tenant_create`

### 10.1 What one form creates

Inside `transaction.atomic()`:

1. Optional inline `Template` (`html_source` pasted / annotated) — schema rebuilt on save.
2. `User` (client, `is_staff=False`) with generated password.
3. `Tenant` with unique subdomain, seeded `content` from schema defaults, published.
4. `TenantMembership(role=owner)`.

If anything fails, **nothing** leaks (no orphan users).

### 10.2 Subdomain validation

```python
SUBDOMAIN_RE = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
# reject: invalid | reserved | taken
```

Live AJAX: `GET /dashboard/sites/check-subdomain/?value=...`  
Submit path: `.lower()` then validate (keep AJAX and submit rules aligned).

If subdomain left blank, derive from site name and uniquify with a suffix.

### 10.3 One-time credentials (never store plaintext)

```
generate password (16 chars, no lookalikes 0/O/1/l/I)
  → stash {user, password, expires} in session under opaque token
  → redirect /credentials/?token=<opaque>
  → pop once (TTL ~10 minutes) and render
  → wipe
```

**Never** put the password in the URL, DB, logs, or email (until real email
infra exists).

---

## 11. Live editor bridge

### 11.1 Layout

Three columns derived from schema:

1. **Sidebar** — sections grouped by `data-group` (layout adapts by section count).
2. **Form** — one field UI per schema field (`field.html` by type).
3. **Preview iframe** — server-rendered HTML with bridge script.

### 11.2 postMessage protocol

Envelope:

```js
{ source: "cms-editor" | "cms-preview", type: "<string>", payload: {...} }
```

| Direction | type | Meaning |
|-----------|------|---------|
| iframe → parent | `ready` | Push full content/styles/tokens |
| iframe → parent | `focus-field` | Click in preview focuses form field |
| parent → iframe | `apply-content` | Live text/image/link patches |
| parent → iframe | `highlight-field` | Visual focus ring |
| parent → iframe | `apply-styles` / `apply-global` / `apply-tokens` | Design overlays |
| parent → iframe | `toggle-visibility` | Hide section/field |

### 11.3 Autosave

Debounced POST (~600ms) of `{ content }` with `X-CSRFToken`. Server normalizes
styles, writes `Tenant.content`, and keeps a rolling `ContentVersion` snapshot
(e.g. last 10) for the home page.

**Architecture:** server renders the first paint (SEO + JS-off); the bridge only
patches afterward.

---

## 12. Auth, memberships, one-time credentials

| Concept | Rule |
|---------|------|
| User model | Django default `User` — no custom user model |
| Staff | Agency operator; can edit any tenant without membership |
| `TenantMembership` | Non-staff access to one tenant (`owner` / `editor` — same perms today) |
| `Tenant.owner` | The **client** user, not the staff who created the site |
| Passwords | Generated with lookalike-free alphabet; one-time session display only |

---

## 13. Portable patterns checklist

Copy these into any stack (Django, Rails, Node, Go…):

### Core CMS

- [ ] HTML annotation DSL with dotted field ids
- [ ] `build_schema(html)` as the only schema source
- [ ] JSON content blob + `merge_with_defaults`
- [ ] Deterministic `render_site(html, content)`
- [ ] Schema-derived editor UI (no hardcoded section names)
- [ ] Locked structure (no client section CRUD)

### Multi-tenancy

- [ ] `Tenant.subdomain` unique slug
- [ ] Host middleware: subdomain → tenant, else verified custom domain
- [ ] Reserved subdomain denylist
- [ ] Wildcard DNS + wildcard/origin cert for `*.base`
- [ ] Instant publish on create (subdomain is enough)

### Custom domains

- [ ] Separate `CustomDomain` rows (many per tenant)
- [ ] DNS A-record verification against a known origin IP
- [ ] Edge emits **per-domain** routers (no catch-all on shared hosts)
- [ ] LE HTTP-01 for client domains; different TLS path for agency hosts
- [ ] Host-aware cookie Domain scoping
- [ ] Isolated writer process for edge config (don’t mount Traefik into the web app)

### Onboarding / auth

- [ ] Atomic User + Tenant + Membership create
- [ ] One-time credential stash (opaque token, TTL, single view)
- [ ] Host-aware login + dual dashboard decorators

### AI (optional)

- [ ] Strip styles/scripts/data-URIs before LLM
- [ ] Annotate by element ref / JSON, not full HTML rewrite
- [ ] Restore + validate with the same parser
- [ ] Async job + compare UI

---

## 14. Gotchas to copy knowingly

1. **`Tenant.custom_domain` CharField vs `CustomDomain` table** — resolution uses
   the table; don’t let helpers diverge.
2. **Subdomain AJAX vs submit case** — normalize `.lower()` in both paths.
3. **Image srcset** — must clear responsive sources or swaps look broken.
4. **Richtext in phrasing hosts** — flatten `<p>` wrappers or the browser splits nodes.
5. **Annotator placeholders** — change format in prompt and restore together.
6. **Cookie Domain on custom hosts** — parent Domain attribute will be rejected.
7. **Partitioned cookies for iframes** — middleware must run outermost or
   `Partitioned` never attaches.
8. **Traefik file extension** — `.yml` required even if content is JSON.
9. **No catch-all HostRegexp** on shared infrastructure.
10. **`form_data` seed dicts** — Django templates evaluate missing filter args;
    seed every key on GET.
11. **Multi-part public suffixes** (`co.uk`) — naive “leftmost label vs `@`”
    DNS helpers are wrong; use a PSL library if you need them.
12. **Version history** — rolling snapshots without restore UI is fine for MVP;
    don’t pretend it’s Time Machine until views exist.

---

## 15. Minimal rebuild map (other stacks)

If you rebuild elsewhere, implement these modules in order:

| # | Module | Responsibility | Replaces |
|---|--------|----------------|----------|
| 1 | `parser` | HTML → schema JSON | `core/parser.py` |
| 2 | `renderer` | HTML + content → HTML | `core/renderer.py` |
| 3 | `models` | Template, Tenant, Membership, CustomDomain | `core/models.py` |
| 4 | `tenant_middleware` | Host → tenant | `core/middleware.py` |
| 5 | `permissions` | Agency vs tenant gates | `core/permissions.py` |
| 6 | `onboard` | Atomic create + credentials | `tenant_create` |
| 7 | `editor_api` | preview / save / publish / upload | dashboard editor views |
| 8 | `editor_ui` | schema form + iframe + bridge | `editor.html` + `editor.js` |
| 9 | `annotator` (optional) | strip / chunk / LLM / restore | `annotator.py` |
| 10 | `edge_sync` | verified domains → routers/certs | `traefik_routes.py` |

### Suggested first milestone (other project)

1. Hardcode one annotated HTML sample.
2. Parse → schema → form UI.
3. Save content JSON → render public page.
4. Add subdomain middleware + wildcard DNS locally (`*.localhost`).
5. Only then add AI annotate and custom domains.

Do not start with custom domains or Traefik — the subdomain path alone proves the
product.

---

## Appendix A — Sample annotated fragment

From `samples/restaurant.html`:

```html
<style data-tokens>
  :root {
    --primary: #b91c1c;
    --bg: #fffaf3;
    --text: #1f2937;
  }
</style>

<section data-section="hero" data-label="Welcome banner" data-icon="star" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">
    Cooking from the heart of Sicily
  </h1>
  <p data-edit="hero.subtitle" data-type="richtext" data-label="Subtitle">...</p>
  <img data-edit="hero.image" data-type="image" data-label="Hero photo" src="...">
</section>
```

### Appendix B — Env vars cheat sheet

| Variable | Purpose |
|----------|---------|
| `TENANT_BASE_DOMAIN` | Subdomain parent (e.g. `sites.example.com`) |
| `CUSTOM_DOMAIN_TARGET_IP` | A-record target for verification |
| `TRAEFIK_DYNAMIC_DIR` | Only on route-syncer; empty elsewhere |
| `OPENAI_API_KEY` / `OPENAI_ANNOTATE_MODEL` | AI annotate |
| `ANNOTATE_MAX_INPUT_CHARS` / chunk settings | Annotator limits |
| `COOKIE_PARENT_DOMAIN` / `IFRAME_EMBED` | Cross-subdomain / iframe cookies |

### Appendix C — Related docs in this repo

| Doc | Focus |
|-----|-------|
| `CLAUDE.md` | Day-to-day assistant guide for this codebase |
| `deploy/DOKPLOY.md` | Traefik / Dokploy / LE / origin cert topology |
| `docs/superpowers/specs/2026-07-15-multi-custom-domains-design.md` | Multi-domain UI |
| `samples/restaurant.html` | Canonical annotated template |

---

*Generated from the working architecture of this CMS platform. Treat the source
files marked ★ in `CLAUDE.md` as the source of truth if this guide and the code
ever disagree.*
