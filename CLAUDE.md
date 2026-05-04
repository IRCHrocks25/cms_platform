# CLAUDE.md

Guide for AI assistants working in this repo. Read this first.

---

## What this project is

A multi-tenant Django CMS where the agency pastes annotated HTML and a
client-friendly editor dashboard auto-generates from it. Clients can edit
text, images, colors, and links inside pre-defined slots. They **cannot**
add or remove sections — the structure is intentionally locked.

Positioning: "Squarespace ease, agency-built quality, locked-structure
safety." The selling point to clients is that they literally cannot break
their site.

This is a **Phase 1 MVP**. Many ideas from the brainstorm (section library,
AI auto-annotation, agency white-label, real custom domain wiring) are out
of scope until the core loop is solid.

---

## Architecture in one diagram

```
  ┌──────────────────┐    paste annotated HTML     ┌────────────────┐
  │  Agency operator │ ─────────────────────────▶  │  Template      │
  │  app.host/dash.  │                             │  schema = JSON │
  └──────────────────┘                             └───────┬────────┘
        agency dashboard                                   │ derives
        (stat cards, sites table,                          │ at save time
         users, new-client flow)                  ┌────────▼───────┐
                                                  │  Tenant        │
  ┌──────────────────┐                            │  content = JSON│
  │  Client editor   │ ◀── auto-generated UI ──── │  custom_domain │
  │  sub.host/dash.  │       from schema          │  is_published  │
  └────────┬─────────┘                            └────────┬───────┘
           │ live preview iframe + postMessage bridge      │
           ▼                                               │ rendered
  ┌──────────────────┐                            ┌────────▼───────┐
  │  Public visitor  │ ◀─────── rendered HTML ──  │  Renderer      │
  │  sub.host        │     (subdomain middleware) │                │
  └──────────────────┘                            └────────────────┘
```

There are **two dashboards** at `/dashboard/`, separated by host:

- `app.host/dashboard/` (no tenant on host) → **agency operator dashboard**
  (staff only). Sites table, users, stat cards, new-client flow.
- `sub.host/dashboard/` (tenant on host) → **client editor** (members or
  staff). Three-column split-view with live preview.

Same view module, two surfaces. Enforced by middleware + decorators —
see "Two dashboards" below.

---

## Annotation spec (the DSL)

This is the core of the product. Templates are HTML files marked up with
`data-*` attributes that the parser reads.

```html
<section data-section="hero"           <!-- id, unique per template -->
         data-label="Welcome banner"   <!-- friendly name in dashboard -->
         data-icon="star"              <!-- optional, lucide-style hint -->
         data-group="Home">            <!-- sidebar grouping -->

  <h1 data-edit="hero.title"           <!-- dotted: <section>.<field> -->
      data-type="text"                 <!-- text|richtext|image|color|link -->
      data-label="Headline">Welcome</h1>

  <img data-edit="hero.image" data-type="image" data-label="Photo" src="...">
  <a   data-edit="hero.cta"   data-type="link"  data-label="Button" href="...">

</section>

<style data-tokens>
  :root {
    --primary: #b91c1c;   /* auto-becomes a Brand → Primary color picker */
    --bg: #fffaf3;
  }
</style>
```

Field types and what they bind to:
| type      | reads / writes                                 |
|-----------|------------------------------------------------|
| `text`    | element text content                           |
| `richtext`| element inner HTML (contenteditable in editor) |
| `image`   | `src` attribute                                |
| `color`   | inline `background-color` (or `color` on span) |
| `link`    | `href` attribute                               |

The parser is in `core/parser.py`. The renderer is in `core/renderer.py`.
**These two files are the heart of the system — read them before changing
anything that touches schema or rendering.**

A complete example template lives at `samples/restaurant.html`.

---

## File map

```
cms_platform/
├── manage.py
├── requirements.txt
├── db.sqlite3                   # local dev DB
│
├── cms_platform/                # Django project config
│   ├── settings.py              # SQLite, app list, TENANT_BASE_DOMAIN, reserved subs
│   ├── urls.py                  # login (TenantAwareLoginView), dashboard, public
│   └── wsgi.py / asgi.py
│
├── core/                        # data + transforms + auth
│   ├── models.py                # Template, Tenant, TenantMembership, MediaAsset, ContentVersion
│   ├── parser.py                # ★ annotated HTML → schema
│   ├── renderer.py              # ★ template + content → HTML (+ preview bridge)
│   ├── middleware.py            # subdomain → request.tenant
│   ├── permissions.py           # ★ tenant_member_required, agency_operator_required
│   ├── auth_views.py            # TenantAwareLoginView (host-aware login routing)
│   ├── views.py                 # public_render, root_redirect
│   ├── admin.py
│   ├── migrations/              # 0001_initial, 0002_tenantmembership, 0003_tenant_custom_domain
│   └── tests/
│       ├── test_middleware.py
│       ├── test_tenant_dashboard.py
│       └── test_agency_admin.py
│
├── dashboard/                   # the editor + agency app
│   ├── views.py                 # ★ both surfaces — agency views + tenant editor
│   └── urls.py                  # namespace = "dashboard"
│
├── templates/
│   ├── base.html                # shell w/ Roboto + nav (agency vs tenant branches)
│   ├── auth/login.html
│   └── dashboard/
│       ├── home.html            # agency overview: stat cards + activity + quick actions
│       ├── editor.html          # ★ split-view: sidebar | form | preview
│       ├── tenant_list.html     # agency sites table (search + status filter)
│       ├── tenant_form.html     # ★ new-client flow (User + Tenant + Membership)
│       ├── tenant_detail.html   # site detail: members, settings, danger zone
│       ├── credentials.html     # one-time password display (new client + reset)
│       ├── user_list.html       # agency user management
│       ├── user_detail.html
│       ├── template_list.html / template_form.html
│       ├── no_access.html       # tenant-host 403 for non-members
│       └── components/field.html  # renders one field by type
│
├── static/
│   ├── css/base.css             # design tokens, components, data-table, stat cards
│   ├── css/editor.css           # split-view + field types + preview pane
│   └── js/editor.js             # ★ form ↔ preview bridge, autosave
│
└── samples/
    └── restaurant.html          # demo annotated template (6 sections)
```

`★` = files most likely to need edits when extending the system.

---

## Two dashboards: agency vs tenant

The single biggest thing to know before touching `dashboard/views.py` or
URL routing.

### How the split works

`core/middleware.py::TenantResolverMiddleware` runs on every request and
sets `request.tenant`:

- Host's leftmost label is looked up as a `Tenant.subdomain`. Base domain
  is `settings.TENANT_BASE_DOMAIN` (`localhost` in dev, env var in prod).
- Reserved subdomains (`settings.TENANT_RESERVED_SUBDOMAINS` — `www`,
  `app`, `api`, `admin`, `dashboard`, `static`, `media`, `mail`),
  bare base domain, and multi-label subdomains all set
  `request.tenant = None`.

`/dashboard/` then dispatches via `dashboard/views.py::dashboard_root`:
`tenant is None` → agency dashboard (staff only); otherwise → tenant
editor (members or staff). Each branch view applies its own decorator;
the dispatcher itself is unauthenticated.

### Decorators (`core/permissions.py`)

- `@agency_operator_required` — `request.tenant` must be `None`,
  user must be `is_staff` or `is_superuser`. Otherwise 403, redirect, or
  bounce to the tenant home depending on the failure mode.
- `@tenant_member_required` — `request.tenant` must be set, user must
  be a member (via `TenantMembership`) OR `is_staff`/`is_superuser`.
  Non-members get `templates/dashboard/no_access.html` at status 403.

Use one of these on every new dashboard view. There is no "either side"
view — pick the surface explicitly.

### Login (`core/auth_views.py::TenantAwareLoginView`)

Wired in `cms_platform/urls.py` as `/login/`. Branches on `request.tenant`:

- Tenant host: log in if member or staff, otherwise refuse with a flash.
- Agency host: log in if staff/superuser, otherwise refuse.
- Honors `?next=` (validated via `url_has_allowed_host_and_scheme`).

### URL inventory

Names match `dashboard/urls.py` exactly. Use `{% url 'dashboard:<name>' %}` —
do not hardcode paths.

| URL                                             | Host    | Decorator                  | Name                       |
|-------------------------------------------------|---------|----------------------------|----------------------------|
| `/login/`                                       | any     | (none)                     | `login`                    |
| `/logout/`                                      | any     | (none)                     | `logout`                   |
| `/dashboard/`                                   | tenant  | `tenant_member_required`   | `dashboard:tenant_home`    |
| `/dashboard/editor/preview/`                    | tenant  | `tenant_member_required`   | `dashboard:tenant_preview_self` |
| `/dashboard/editor/save/`                       | tenant  | `tenant_member_required`   | `dashboard:tenant_save_self`    |
| `/dashboard/editor/publish/`                    | tenant  | `tenant_member_required`   | `dashboard:tenant_publish_self` |
| `/dashboard/editor/upload/`                     | tenant  | `tenant_member_required`   | `dashboard:tenant_upload_self`  |
| `/dashboard/`                                   | agency  | `agency_operator_required` | `dashboard:root`           |
| `/dashboard/sites/`                             | agency  | `agency_operator_required` | `dashboard:tenant_list`    |
| `/dashboard/sites/new/`                         | agency  | `agency_operator_required` | `dashboard:tenant_create`  |
| `/dashboard/sites/check-subdomain/`             | agency  | `agency_operator_required` | `dashboard:check_subdomain`|
| `/dashboard/sites/<pk>/`                        | agency  | `agency_operator_required` | `dashboard:tenant_detail`  |
| `/dashboard/sites/<pk>/credentials/`            | agency  | `agency_operator_required` | `dashboard:site_credentials` |
| `/dashboard/sites/<pk>/settings/`               | agency  | `agency_operator_required` | `dashboard:tenant_settings_update` |
| `/dashboard/sites/<pk>/delete/`                 | agency  | `agency_operator_required` | `dashboard:tenant_delete`  |
| `/dashboard/sites/<pk>/members/add/`            | agency  | `agency_operator_required` | `dashboard:tenant_member_add` |
| `/dashboard/sites/<pk>/members/<id>/remove/`    | agency  | `agency_operator_required` | `dashboard:tenant_member_remove` |
| `/dashboard/sites/<pk>/members/<id>/role/`      | agency  | `agency_operator_required` | `dashboard:tenant_member_role` |
| `/dashboard/sites/<pk>/edit/`                   | agency  | `agency_operator_required` | `dashboard:tenant_editor`  |
| `/dashboard/sites/<pk>/{preview,save,publish,upload}/` | agency | `agency_operator_required` | `dashboard:tenant_{preview,save,publish,upload}` |
| `/dashboard/users/`                             | agency  | `agency_operator_required` | `dashboard:user_list`      |
| `/dashboard/users/<pk>/`                        | agency  | `agency_operator_required` | `dashboard:user_detail`    |
| `/dashboard/users/<pk>/credentials/`            | agency  | `agency_operator_required` | `dashboard:user_credentials` |
| `/dashboard/users/<pk>/reset-password/`         | agency  | `agency_operator_required` | `dashboard:user_reset_password` |
| `/dashboard/users/<pk>/deactivate/` `/activate/`| agency  | `agency_operator_required` | `dashboard:user_deactivate` / `user_activate` |
| `/dashboard/users/<pk>/make-staff/`             | agency  | `agency_operator_required` (+ superuser) | `dashboard:user_make_staff` |
| `/dashboard/users/<pk>/memberships/<id>/remove/`| agency  | `agency_operator_required` | `dashboard:user_remove_membership` |
| `/dashboard/templates/...`                      | agency  | `agency_operator_required` | `dashboard:template_*`     |
| `/site/<subdomain>/`                            | any     | (none, public)             | `public_render`            |

---

## Auth model

- **User** is Django's default — no custom user model. Don't introduce one.
- **`is_staff=True`** means agency operator. Staff can access the agency
  dashboard and edit any tenant (`Tenant.user_can_edit()` short-circuits
  on staff/superuser).
- **`TenantMembership` (FK Tenant, FK User, role)** gives a non-staff user
  access to a specific tenant. Roles today: `owner`, `editor` — both have
  the same permissions, the distinction is currently descriptive only.
  Don't add a third role until there's an actual permission split for it.
- A staff user does NOT need a `TenantMembership` to edit a tenant.
- **Generated passwords** are produced by
  `dashboard/views.py::_generate_password` (16 chars, lookalikes excluded
  via `get_random_string` with a custom alphabet).
- **One-time credential display** uses `_stash_credentials_in_session` /
  `_pop_credentials_from_session` (also in `dashboard/views.py`). The
  plaintext password lives only in the session, keyed by a single-use
  token, with a 10-minute TTL. Rendered once via
  `templates/dashboard/credentials.html`, then wiped. **Never stored in
  DB plaintext, never logged, never put in a URL query string** (the
  token in the URL is opaque, not the password).
- The same one-time-credentials pattern is used for both the new-client
  flow (`tenant_create`) and password reset (`user_reset_password`).

---

## Design tokens (UI rules)

- **Font:** Roboto (Google Fonts CDN, weights 300/400/500/700)
- **Palette:** white `#fff`, black `#0a0a14`, blue `#2563eb`, purple `#7c3aed`
- **Radii:** 8 / 12 / 16 / 24 px
- **Spacing scale:** 4–64 px, defined as `--space-1` through `--space-8`
- **Generous whitespace** — `.page` uses 48px+ vertical padding intentionally
- **Avoid colors outside the palette.** If a new state needs a hue (success
  green / danger red), use the existing `--color-success` / `--color-danger`
  variables in `base.css`. Don't introduce new ones casually.
- New table/stat-card/badge styles already live in `static/css/base.css`
  (`.data-table`, `.stat-card`, `.badge-*`, `.filter-pill`). Reuse them.

---

## How the editor works (mental model)

The editor is **three columns**:

1. **Sidebar** — auto-generated from `schema.sections`, grouped by
   `data-group`. Layout class adapts based on section count:
   - `compact` (≤6) — sidebar hidden, single scrollable form
   - `standard` (7–15) — sidebar visible
   - `dense` (16+) — sidebar + search bar
2. **Form panel** — one form section per `data-section`, fields rendered
   by `templates/dashboard/components/field.html`
3. **Preview iframe** — loads `/dashboard/sites/<id>/preview/` (agency) or
   `/dashboard/editor/preview/` (tenant), which is the renderer in
   `preview=True` mode. The renderer injects a JS bridge that
   `postMessage`s the dashboard.

**Bridge protocol** (between dashboard parent + preview iframe):

| direction        | type             | payload                          |
|------------------|------------------|----------------------------------|
| iframe → parent  | `ready`          | `{}`                             |
| iframe → parent  | `focus-field`    | `{ id: "hero.title" }`           |
| parent → iframe  | `apply-content`  | `{ "hero.title": "...", ... }`   |
| parent → iframe  | `highlight-field`| `{ id: "hero.title" }`           |

All postMessages have `source: "cms-editor"` (parent) or `"cms-preview"`
(iframe). Don't change these strings without updating both sides.

> **Two iframes, two preview modes — don't conflate them.**
>
> 1. **Editor preview**: server-rendered via `render_site(preview=True)`,
>    bridge script injected, full content substitution. Lives on
>    `editor.html`.
> 2. **Template-author preview**: on `template_form.html`. Pure
>    client-side `iframe.srcdoc = textarea.value`, no server roundtrip,
>    no bridge — shows the *raw pasted HTML* so the agency can sanity-
>    check layout before saving. The header strip explicitly labels it
>    "no content substitution."
>
> If you need brand-token / default substitution at authoring time,
> upgrade #2 to a `POST /dashboard/templates/preview/` endpoint that
> calls `render_site(html, schema.defaults)`.

---

## Common tasks — where to make changes

| Task                                        | Files to touch                              |
|---------------------------------------------|---------------------------------------------|
| Add a new field type (e.g. `select`, `date`)| `parser.py` (VALID_FIELD_TYPES + extract), `renderer.py` (_apply_field), `field.html` (UI), `editor.js` (binding), `dashboard/views.py` (`STARTER_TEMPLATE_HTML` if you want it in the new-template skeleton) |
| Change autosave debounce / behavior         | `static/js/editor.js` (`scheduleSave`)      |
| Tweak adaptive layout breakpoints           | `dashboard/views.py` (`_render_editor`), `static/css/editor.css` |
| Add a new dashboard page                    | `dashboard/views.py` + `dashboard/urls.py` + `templates/dashboard/...` (decorate with `agency_operator_required` OR `tenant_member_required` — pick one) |
| Change publish/render flow                  | `core/views.py::public_render` + `core/middleware.py` |
| New annotation attribute                    | `core/parser.py` (read), `core/renderer.py` (apply) |
| Add a permission role (e.g. `viewer`)       | `core/models.py` (`TenantMembership.ROLE_CHOICES`), `core/permissions.py` if you need role-aware checks, plus wherever role is rendered/edited in templates |
| Add a reserved subdomain                    | `cms_platform/settings.py::TENANT_RESERVED_SUBDOMAINS` |
| Change password generation rules            | `dashboard/views.py::_generate_password` (alphabet/length) |
| Add a stat to the home dashboard            | `dashboard/views.py::agency_home` (compute), `templates/dashboard/home.html` (render) |
| Add a column to the sites table             | `dashboard/views.py::tenant_list` (annotate the queryset), `templates/dashboard/tenant_list.html` |
| Add a field to the new-client form          | `dashboard/views.py::tenant_create` (POST handler **and** the GET seed dict — see the `form_data` warning under "sharp edges"), `templates/dashboard/tenant_form.html` |

---

## Running locally

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Migration files **are** committed (`0001_initial`,
`0002_tenantmembership`, `0003_tenant_custom_domain`). A fresh clone
should `migrate` cleanly without needing `makemigrations` first.

Then go to `http://localhost:8000/login/` and sign in as the superuser.

### Subdomain access in dev

`TENANT_BASE_DOMAIN` defaults to `localhost`. Modern Chrome/Firefox
resolve `*.localhost` automatically (RFC 6761), so
`http://acme.localhost:8000/dashboard/` will hit the Acme tenant with no
hosts-file edits.

Some corporate DNS / older browsers don't honor that. In those cases set
`TENANT_BASE_DOMAIN=lvh.me` and use `acme.lvh.me:8000` (lvh.me is a
public DNS service that resolves all subdomains to `127.0.0.1`).

### First-run smoke test

1. Sign in as the superuser at `localhost:8000/login/`.
2. Go to `/dashboard/templates/new/` and create a Template (paste
   `samples/restaurant.html`).
3. Click **+ New client** in the nav. Fill the form (site name,
   subdomain `acme`, the template, a client username). Submit → you land
   on `/dashboard/sites/<id>/credentials/?token=...` with the generated
   password shown ONCE.
4. Open `http://acme.localhost:8000/login/` in a private window, log in
   as the new client user with that password. You should see the editor.
5. Click a heading in the preview — form should focus that field. Edit
   text in the form — preview should update live within ~50ms.
6. Click **Publish** in the editor, then visit
   `http://localhost:8000/site/acme/` (or `acme.localhost:8000/`) to see
   the public render.

---

## Constraints / non-goals (do not violate without asking)

- **No section add/remove for clients.** This is the product's defining
  promise. Don't add UI that lets clients insert new sections.
- **No raw HTML editing for clients.** Richtext is the only way they touch
  markup, and it's intentionally limited to what `contenteditable` allows.
- **Schema is derived, not stored.** Don't let a `Template.schema` drift
  from what `build_schema(html_source)` returns. `Template.save()` always
  rebuilds it. If you need to cache differently, do it in the parser, not
  by manually editing schema rows.
- **`Tenant.content` is canonical.** Never compute display values that
  bypass `merge_with_defaults()` — that helper is what makes empty fields
  fall back to template defaults.
- **No new dependencies casually.** The stack is deliberately tiny: Django
  + BeautifulSoup + lxml + Pillow (+ `django.contrib.humanize`, ships
  with Django). Don't add jQuery, htmx, alpine, etc. without discussing
  first.
- **Generated passwords are never persisted in plaintext.** If you need
  to expose a password, use `_stash_credentials_in_session` and the
  `credentials.html` template. Don't email passwords (out of scope until
  email infra exists). Don't put plaintext in URLs, logs, or audit rows.
- **Don't add per-tenant user models.** All users are Django `User` rows;
  access is via `TenantMembership`. No `ClientUser`, no `AgencyUser`.
- **Reserved subdomains live in `settings.TENANT_RESERVED_SUBDOMAINS`.**
  If you need to reserve more (e.g. `cdn`, `assets`), add them there —
  don't hardcode in middleware.
- **`Tenant.owner` is the client user, not the staff who created the
  site.** This changed in the agency-admin spec. If you need "which staff
  created this," that's not currently tracked — add a `created_by` field,
  don't repurpose `owner`.
- **`Tenant.custom_domain` is a stub.** The field exists and persists,
  but there's no DNS verification, no TLS, no Cloudflare integration. Don't
  pretend it's wired up. The next spec (Cloudflare for SaaS) turns it on.

---

## Known sharp edges

- **Multi-tenant subdomain routing is real, not a stub.** `TENANT_BASE_DOMAIN`
  + reserved-subdomain skip drives it (`core/middleware.py`). Production
  still needs wildcard DNS + a wildcard cert (Caddy/nginx/Cloudflare) — that
  ops work isn't done — but the request path through Django is correct.
- **No CDN / image processing.** Uploads land in `media/tenants/...` and
  are served directly by Django dev server. Pillow is installed but not
  used yet — image resizing/optimization is a TODO.
- **Version history is rolling 10.** No UI yet for browsing or restoring.
  The data is there (`ContentVersion`), the views aren't.
- **CSRF on file upload** is wired via `X-CSRFToken` header in fetch.
  If you add a new mutating endpoint, follow the same pattern in
  `editor.js`.
- **Brand tokens regex** in `parser.py` is permissive — it'll catch any
  `--name: value;` pair inside `<style data-tokens>`. If you need scoped
  variables (e.g. only inside `:root`), tighten the regex there.
- **`form_data` must contain every key the template references.** The form
  templates use `{{ template.x|default:form_data.x|default_if_none:'' }}`.
  Django's filter-argument resolution **always evaluates** `form_data.x`
  (even when `default` would short-circuit), and a missing dict key in a
  filter arg raises `VariableDoesNotExist` regardless of `string_if_invalid`.
  So GET handlers in `dashboard/views.py` (`template_create`,
  `template_detail`, `tenant_create`) seed `form_data` with empty-string
  defaults for every expected key. POST error paths can pass `request.POST`
  directly because the browser submits every named input. If you add a new
  field to one of these forms, add the key to the GET seed dict too.
- **Forced logout on password reset is not implemented.** Resetting a
  client's password sets the new password but doesn't invalidate existing
  sessions. If a session is already compromised, the attacker stays logged
  in until natural expiry (~2 weeks). Backlog item, not urgent.
- **Member-add candidate dropdown is capped at 200 active users**, server-
  rendered, no search. Fine at <50 clients, gets unwieldy past ~150 users.
  Replace with a typeahead (XHR against a small `users/search/` endpoint)
  when needed — don't bloat the dropdown further.
- **Credentials TTL is 10 minutes, single-view.** If the operator closes
  the tab before copying, they have to reset the password from the user
  detail page. Intentional (security > convenience), but worth knowing.
- **`check_subdomain` does not lowercase before validating** — `"UPPER"`
  reports `invalid`. The form-submit path (`tenant_create`) does
  `.lower()` first, so the form is more permissive than the live AJAX
  check. Don't silently diverge further; if users complain, normalize one
  direction or the other.

---

## When extending the system, prefer these patterns

- **Schema-derived UI**: anything client-facing should be generated from
  `Template.schema`, never from hardcoded section names.
- **JSON content blob**: don't add per-field DB columns. Variable shapes
  per template = JSON blob is the right tool.
- **Dotted field IDs**: `<section>.<field>` everywhere — in HTML, schema,
  content, and postMessage payloads. Don't invent new ID formats.
- **Server renders, client patches**: the preview iframe is server-rendered
  on initial load (so it survives JS-off and SEO). The bridge only patches
  in-place after that.
- **Decorate every dashboard view.** Pick `agency_operator_required` or
  `tenant_member_required` — no view should be reachable without one.
- **One-time credentials, never long-lived plaintext.** New flows that
  need to surface a generated secret reuse `_stash_credentials_in_session`
  / `_pop_credentials_from_session` / `credentials.html`. Don't roll your
  own.
- **Atomic creation in `tenant_create`.** When a flow creates multiple
  related rows (User + Tenant + Membership), wrap it in
  `transaction.atomic()` so a failure halfway through doesn't leak orphan
  users. Mirror this for any future multi-row creation flow.
