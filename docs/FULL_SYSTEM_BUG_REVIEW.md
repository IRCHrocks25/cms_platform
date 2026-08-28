# Full-system bug review — agency admin, client, and editor

**Date:** 2026-08-27  
**Repo:** `cms_platform-2` (local clone, uncommitted block-builder work present)  
**Reviewer mode:** three personas on the same product — agency operator, client site owner, and the split-view editor (used by both).  
**Goal:** list every real defect found, how it was found, and how to replicate it.

---

## Status (2026-08-27 implementation pass)

Filed items **A1–A17, C1–C4, C6, E1–E14, X1** are **fixed** in this pass. See the plan’s design notes: preview `code` is inert only when `preview=True`; public sites still render raw HTML by product decision; shared-shell Edit HTML is refused (no clone-on-write); site unpublish gates public page render without bulk-flipping `Page.is_published`; undo aborts an in-flight save rather than flushing first.

**C5 remains an accepted residual.** Live still writes the canonical content plane — there is no unpublished draft snapshot. The Live pill already says changes go public immediately. A real draft/publish split is a new product, not a missed flag.

---

This is a **bug report**, not a redesign brief. UX/accessibility issues are included when they cause data loss, lockout, broken tasks, or security holes. Cosmetic polish from the older `docs/UI_AUDIT.md` is summarized at the end rather than re-filed as new P0s.

---

## 1. How this review was done

Browser MCP was **not** available in this session, so this is not a click-through of every screen in Chrome. The review used three layers so each finding can be re-run:

### 1.1 Architecture + source read (all three roles)

Walked the live product loop from `CLAUDE.md`, `docs/HOW_THE_SYSTEM_WORKS.md`, `docs/LOCAL_SETUP_AND_BLOCK_EDITOR.md`, `docs/WEEK_PLAN.md`, and `docs/SESSION_REPORT_2026-08-25.md`, then read the actual handlers:

| Surface | Primary files |
|---------|----------------|
| Agency dashboard | `dashboard/views.py`, `dashboard/urls.py`, `templates/dashboard/*.html` |
| Auth / hosts | `core/middleware.py`, `core/permissions.py`, `core/auth_views.py` |
| Editor | `templates/dashboard/editor.html`, `static/js/editor.js`, `templates/dashboard/components/field.html` |
| Render / public | `core/views.py`, `core/renderer.py`, `core/parser.py`, `core/services/blocks.py` |
| Data | `core/models.py`, `core/admin.py`, `core/services/content_versions.py`, `core/services/accounts.py` |

### 1.2 Automated reproduction (Django Test Client + renderer)

A throwaway script exercised the public render path, `json.dumps` editor bootstrap, copy-page clone logic, CSS/link/color/code injection, CASCADE, diagnostic headers, and save-path sanitizers against a throwaway test database.

**Proved on 2026-08-27** (all `PROVED`):

| # | Finding | Evidence |
|---|---------|----------|
| 1 | Unpublished site still serves a published inner page | `GET /site/acme/` → **404**; `GET /site/acme/about/` → **200** |
| 2 | Editor JSON can break out of `<script>` | `json.dumps({"hero.title": "</script><script>alert(1)</script>"})` keeps a literal `</script>` |
| 3 | Copy-home / copy-page drops nested `children` | Source keys include `children`; cloned dict is only `id`/`type`/`fields` |
| 4 | `_global` CSS breakout | Rendered CSS: `body{color: red;} body{display:none !important;}` |
| 5 | Brand token CSS breakout | `--primary: red;}body{opacity:0}/*;` |
| 6 | `javascript:` links render | `href` written with no scheme allowlist |
| 7 | Color field CSS injection | `background-image` smuggled into `style` |
| 8 | Code block raw `<script>` in preview HTML | `window.__pwned=1` present in `preview=True` output |
| 9 | Deleting the owner user CASCADE-deletes the site | `Tenant.owner` `on_delete=CASCADE` |
| 10 | Diagnostic headers on every response | `X-Diag-Debug: True`, CSRF SameSite/Secure, iframe-embed, GHL auto-login |
| 11 | New client sites go live immediately | `create_tenant_account(..., is_published=True)` default |
| 12 | `fontSize` style smuggling | `position:fixed` survives into inline style |
| 13 | Save path does not reject CSS breakout | `_normalize_styles` keeps `_global.textColor = "red;} body{display:none !important"` |

### 1.3 What was **not** done

- No live Chrome/Firefox click-through of Sonia 2 / `sample-website.localhost`.
- No real OpenAI annotation job (needs API key + time).
- No Traefik / Let's Encrypt / custom-domain DNS against production.
- No GHL OAuth reconnect.

Those gaps are called out on individual items. Everything else below is either **proved** (script) or **code-proven** (the exact branch is in the current source; the steps are what a human would click).

### 1.4 Roles used in this document

| Persona | Host | How they log in | What they can do |
|---------|------|-----------------|------------------|
| **Agency admin / operator** | Agency host (`localhost:8000`, no tenant subdomain). Staff or superuser. | `/login/` as staff | Templates, sites, users, page HTML, domains, GHL, Django `/admin/` |
| **Client** | Tenant host (`<sub>.localhost:8000`). `TenantMembership` owner/editor. | `/login/` on that host | Edit content, publish, pages (on block shells), blog, team, gallery |
| **Editor** | Same split-view used by **both** agency (`/dashboard/sites/<id>/edit/`) and client (`/dashboard/` on tenant host) | After login | Sidebar / form / live preview / autosave / undo / Quick Add |

A staff user opening a client site in the agency editor is the **highest-privilege victim** of several XSS bugs below.

---

## 2. Severity legend

| Sev | Meaning |
|-----|---------|
| **P0** | Data loss, site-wide corruption, or authenticated XSS / session takeover |
| **P1** | Security hole, lockout, public leak, or a primary task that silently fails |
| **P2** | Real bug with a workaround, or a race that is timing-dependent |
| **P3** | Confusing UX, inconsistent validation, info leak, or documented sharp edge that still bites operators |

---

## 3. Summary table

| ID | Sev | Persona(s) | Title | Status |
|----|-----|------------|-------|--------|
| A1 | P0 | Admin | “Edit HTML” on a shared block shell mutates home + every page | Code-proven |
| A2 | P0 | Admin / Django admin | Deleting a user who owns a site CASCADE-deletes the whole tenant | **Proved** |
| A3 | P1 | Admin | Agency can remove the site owner’s membership (client lockout) | Code-proven |
| A4 | P1 | Admin | Any staff can deactivate or reset a superuser | Code-proven |
| A5 | P1 | Admin / ops | `DEBUG` + hardcoded `SECRET_KEY` if env missing | Code-proven |
| A6 | P2 | Admin | New client sites are created already published | **Proved** |
| A7 | P2 | Admin | Template **create** ignores “Client editing = Raw” | Code-proven |
| A8 | P2 | Admin | Sibling-import annotation can overwrite later HTML edits | Code-proven |
| A9 | P2 | Admin | Credentials token is not bound to the site/user in the URL | Code-proven |
| A10 | P2 | Admin | Force-verify custom domain skips DNS proof | Code-proven |
| A11 | P2 | Admin | Django admin shows live GHL OAuth tokens | Code-proven |
| A12 | P2 | Admin | Block palette attach runs outside the create transaction | Code-proven |
| A13 | P3 | Admin | `check_subdomain` does not lowercase; create does | Code-proven |
| A14 | P3 | Admin | Page rename 500s if `page_pk` missing | Code-proven |
| A15 | P3 | Admin | Public-suffix DNS hint wrong for `example.co.uk` | Code-proven |
| A16 | P3 | Admin | Classic HTML page create / sibling import ignore the 20-page cap | Code-proven |
| A17 | P3 | Admin | `Tenant.custom_domain` vs `CustomDomain` can drift | Code-proven |
| C1 | P1 | Client / public | Unpublishing the **site** still leaves published inner pages public | **Proved** |
| C2 | P1 | Client | Copy page / copy home drops nested row children | **Proved** |
| C3 | P1 | Client | Gallery delete scrubs live content with no undo snapshot | Code-proven |
| C4 | P1 | Client | Password reset does not invalidate existing sessions | Documented + still true |
| C5 | P3 | Client | Live page: autosave is instantly public (easy to miss) | Product architecture |
| C6 | P3 | Client | Django admin `SlugField` allows `_` in subdomains; browsers reject the host | Documented locally |
| E1 | P0 | Editor / admin | Same-origin unsandboxed preview + raw `code` block = staff XSS | **Proved** (payload in HTML) |
| E2 | P0 | Editor | Stored XSS via `content_json\|safe` in editor `<script>` | **Proved** (`json.dumps`) |
| E3 | P0 | Editor | `text-update` writes unsanitized HTML into the parent editor | Code-proven |
| E4 | P0 | Editor | Undo races with in-flight autosave (undo can be overwritten) | Code-proven |
| E5 | P1 | Editor | Undo with dirty debounce discards keystrokes | Code-proven |
| E6 | P1 | Public / editor | `_global` / brand / color / `fontSize` CSS injection | **Proved** |
| E7 | P1 | Public / editor | `javascript:` (and other) URLs on link / image / video / embed | **Proved** (link) |
| E8 | P1 | Editor | Duplicate/add cap ignores subtree size → failed save | Code-proven |
| E9 | P2 | Editor | postMessage trusts `source` string only (`*` origin) | Code-proven |
| E10 | P2 | Editor | Failed structural save leaves `structuralReload` stuck | Code-proven |
| E11 | P2 | Editor | Depth cap silently strips nested children on save | Code-proven |
| E12 | P2 | Editor | Undo version list is not filtered to `source=dashboard` | Code-proven |
| E13 | P2 | Editor | Restore / admin JSON bypasses 40-block and depth caps | Code-proven |
| E14 | P2 | Editor | Instance IDs not sanitized (selector / attribute hazards) | Code-proven |
| X1 | P3 | Anyone | `X-Diag-*` headers leak runtime security config | **Proved** |

---

## 4. Persona walkthroughs (what each role hits)

### 4.1 As an agency admin

Typical loop: log in on `http://localhost:8000/login/` as staff → Sites → open a client → Edit, or Templates / Users / Custom domains.

**What goes wrong in that loop:**

1. **Shared-shell “Edit HTML”** looks like a per-page tool. On block-builder sites it edits the **one** Template that home and every inner page share. Saving header/footer HTML rewrites the whole site. The subtitle on the page still says the opposite.
2. **Unpublish site** on site detail / home editor 404s `/` and `/blog/`, but a published `/about/` stays live. Operators reasonably think the site is offline.
3. **New client** credentials screen appears, and the public subdomain is already live with starter content (`is_published=True`).
4. **Users → Deactivate / Reset password** works on a superuser if you are merely staff.
5. **Users → Django admin → delete user** (or any CASCADE of `Tenant.owner`) wipes the tenant, pages, media, versions.
6. **Members → Remove** on the owner’s membership locks the client out while `Tenant.owner` still points at them.
7. Opening a client editor as staff is enough to run **E1 / E2** if that client saved a hostile field or Code block.

### 4.2 As a client (tenant host)

Typical loop: `http://<sub>.localhost:8000/login/` as the membership user → editor → Pages / Blog / Team / Gallery / Publish.

**What goes wrong in that loop:**

1. **Copy home** when creating a page copies the row shells but **empties the columns** (nested headlines, images, buttons gone).
2. **Unpublish** on the home editor does not take inner pages offline (same as admin C1).
3. **Delete an image in Gallery** that is used on a Live page instantly blanks those fields; Undo cannot restore them.
4. **Code block / custom link / Design colors** can ship XSS or CSS overlays to visitors (and to staff who later open the editor).
5. **Team** cannot remove the owner (good); the agency side can (bad — A3).
6. After the agency **resets the password**, any already-open client session stays valid until natural expiry (~2 weeks).

### 4.3 As an editor (the split-view itself)

Typical loop: three columns (or mobile switcher under 1100px) → edit text → preview updates → autosave → Publish.

**What goes wrong in that loop:**

1. Preview iframe has **no `sandbox`** and is **same-origin** with `/dashboard/`. A Code block script runs with the editor user’s cookies.
2. `window.CMS.content` is injected with `|safe`. A text field containing `</script>…` breaks out of the bootstrap script on the next load.
3. Inline canvas edits send `text-update` into the parent; the parent does `innerHTML = p.html` **without** the richtext scrubber.
4. **Publish** flushes pending saves first (fixed 2026-08-25). **Undo does not.** Click Undo while “Saving…” can race. Click Undo during the 600ms debounce and the last keystrokes vanish.
5. Duplicate a fat row near the 40-block cap: UI allows it, server 400s, editor state can diverge.
6. On a **Live** pill, every successful save is already the public page. There is no draft content plane.

---

## 5. Detailed bugs

For each bug: **what**, **how it was found**, **how to replicate**, **expected vs actual**, **why it matters**, **where**.

---

### A1 — “Edit HTML” on a shared block shell mutates the whole site

| | |
|---|---|
| **Severity** | P0 |
| **Persona** | Agency admin |
| **Where** | `dashboard/views.py` `page_edit_html` (~2474–2520); `templates/dashboard/page_edit_html.html` 24–28; `_page_create_shared` 2073–2147 (`template=shell`) |

**How it was found**  
Read `page_edit_html`’s docstring (“Each page owns its own template”) against `_page_create_shared`, which sets `template=shell` (the **same** `Tenant.template` as home). The page HTML UI copy still claims isolation. Tests in `core/tests/test_page_html_editing.py` only cover pages that **do** have a dedicated Template (classic paste-HTML create). No test covers Edit HTML on a shell page.

**How to replicate**

1. Use a **block-shell** site (local: convert with `migrate_template_to_blocks`, or create a “block site” template).
2. As staff, open **Pages** and add an inner page (shared shell). Confirm in Django admin / shell that `page.template_id == tenant.template_id`.
3. Open that page in the agency editor → **Edit HTML source** (or `/dashboard/sites/<pk>/pages/<page_pk>/edit-html/`).
4. Note the subtitle: *“This page has its own template, so changes here don't affect any other page.”*
5. Change something in the shared chrome (header brand, footer, CSS) and Save.
6. Open **Home** and any other inner page (editor + public).

**Expected:** Only that page’s structure changes, **or** the action is blocked with “this page shares the site shell — edit the template instead.”  
**Actual:** `save_template_version(page.template, …)` writes the shared Template. Home + every sibling page re-render with the new HTML.

**Why it matters:** One agency click can corrupt every page on a client site. The UI actively lies.

---

### A2 — Deleting a User who is `Tenant.owner` CASCADE-deletes the site

| | |
|---|---|
| **Severity** | P0 |
| **Persona** | Agency admin (Django `/admin/`; any code path that deletes the User) |
| **Where** | `core/models.py` 280–284; default Django `User` admin (`core/admin.py` does not override User) |

**How it was found**  
Model read. Repro script checked `Tenant._meta.get_field("owner").remote_field.on_delete.__name__ == "CASCADE"`.

**How to replicate**

1. Create a client site (dashboard **+ New client**). Note the owner username.
2. Open `/admin/` as superuser → **Users** → that owner → **Delete**.
3. Confirm the confirmation page lists `Tenant` (and related pages/media) as cascade objects.
4. Confirm delete.

**Expected:** `PROTECT` / block with “reassign owner first,” or SET_NULL with a required reassignment flow.  
**Actual:** The tenant row and dependent data are deleted with the user.

**Why it matters:** Routine “clean up this login” in Django admin is silent catastrophic data loss. Dashboard user deactivate does **not** delete the user, so the dangerous path is specifically **User delete**.

---

### A3 — Agency can remove the site owner’s membership (client lockout)

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Agency admin (client is the victim) |
| **Where** | `dashboard/views.py` `tenant_member_remove` 1491–1498, `user_remove_membership` 1675–1685 vs client `team_member_remove_self` 1893–1898; `Tenant.user_can_edit` 311–316 |

**How it was found**  
Compared tenant self-serve (explicitly refuses `tenant.owner_id`) with agency remove (unconditional `membership.delete()`). `user_can_edit` requires membership **or** staff — the owner FK alone is not enough.

**How to replicate**

1. Create a site; the owner has a `TenantMembership`.
2. Agency → **Sites → that site → Members → Remove** on the owner.
3. In a private window, log in as the owner on `http://<sub>.localhost:8000/login/`.
4. Observe 403 / no-access (non-staff, no membership).
5. Confirm `Tenant.owner` still points at that user.

**Expected:** Same protection as Team self-serve, or a forced ownership transfer.  
**Actual:** Client cannot open their editor; staff still can. Ownership and access desync.

**Why it matters:** Accidental lockout during “remove extra logins.” Support has to recreate a membership by hand.

---

### A4 — Any staff can deactivate or reset a superuser

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Agency admin (staff) vs superuser |
| **Where** | `user_deactivate` / `user_reset_password` 1627–1648 vs `user_make_staff` 1663–1665 (superuser-only) |

**How it was found**  
`user_make_staff` correctly requires `is_superuser`. Deactivate only blocks **self**. Reset has no privilege check at all.

**How to replicate**

1. Have a superuser (`admin`) and a second user with `is_staff=True`, `is_superuser=False`.
2. Log in as the staff user on the agency host.
3. **Users → admin → Deactivate** (or **Reset password**).
4. Superuser can no longer log in (deactivate) or the staff user now holds the new password (reset). Existing superuser sessions are also not rotated (see C4).

**Expected:** Superuser (and ideally other staff) protected; only superusers manage superusers.  
**Actual:** Least-privileged agency operator can lock out or take over the highest account.

---

### A5 — Insecure defaults if production env is missing

| | |
|---|---|
| **Severity** | P1 (deploy footgun) |
| **Persona** | Ops / anyone who can hit a mis-deployed instance |
| **Where** | `cms_platform/settings.py` 10–15, 33–49 (`ALLOWED_HOSTS=["*"]` is documented as edge-enforced) |

**How it was found**  
Settings read. `DJANGO_DEBUG` defaults to `"1"`; `SECRET_KEY` falls back to `dev-only-secret-change-me-in-production`.

**How to replicate**

1. Start the app with `DJANGO_DEBUG` and `DJANGO_SECRET_KEY` unset.
2. Confirm `DEBUG is True` and the secret is the repo default.
3. Hit any URL and read `X-Diag-Debug` (X1).

**Expected:** Fail closed (refuse to boot without a secret; DEBUG off).  
**Actual:** Debug toolbar-level exposure + a known signing key if someone deploys from the clone without env.

---

### A6 — New client sites are created already published

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin (public visitors see it) |
| **Where** | `core/services/accounts.py` 44–60, 128; dashboard `tenant_create` does not pass `is_published=False` |

**How it was found**  
Docs/smoke test talk about Publish as a later step. Account service docstring says dashboard defaults to True; MCP `create_client` passes False. Repro script confirmed the default is `True`. `core/tests/test_account_services.py` asserts `self.assertTrue(tenant.is_published)`.

**How to replicate**

1. Agency → **+ New client**. Pick a template, submit.
2. Without clicking Publish, open `http://<sub>.localhost:8000/` (or `/site/<sub>/`) **logged out**.
3. The homepage is live.

**Expected:** Draft until the operator/client publishes (matches the editor pill mental model).  
**Actual:** Starter/placeholder content is on the public internet as soon as the row exists.

**Note:** This is **intentional in the service**, so some tests will fail if you “fix” it without updating them. It is still a product bug for operators who think unpublished = not created yet.

---

### A7 — Template create ignores “Client editing” (`editing_mode`)

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin |
| **Where** | `dashboard/views.py` 268–286 (create) vs 354–376 (update) |

**How it was found**  
Create path forces `EDITING_EDITABLE` when the HTML has an editable schema (or blocks mode). It never reads `request.POST["editing_mode"]`. Detail/update does.

**How to replicate**

1. **Templates → New template**.
2. Paste annotated HTML (e.g. `samples/restaurant.html`).
3. Set **Client editing** to **Raw** (or whatever the form labels the non-editable mode).
4. Create.
5. Re-open the template: mode is **editable**; a client assigned this template can edit.

**Expected:** Stored mode matches the form.  
**Actual:** Create always releases editing when schema/blocks exist. Workaround: save again on the detail page.

---

### A8 — Background sibling annotation can overwrite concurrent HTML edits

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin |
| **Where** | `dashboard/views.py` `_annotate_template_in_background` 2252–2303, import 2446–2452 |

**How it was found**  
Daemon thread later calls `save_template_version(..., allow_field_loss=True)` with the **original fetched HTML**, with no “skip if `html_source` changed since import.”

**How to replicate**

1. Agency → import sibling pages from a URL (page import flow).
2. Immediately open **Edit HTML** on one of those pages and save a manual change.
3. Wait for the annotation job to finish.
4. Reload Edit HTML: manual save may be gone, replaced by annotated fetch output (and field loss is allowed).

**Expected:** If the operator already saved, skip or merge; never clobber blindly.  
**Actual:** Last writer is the background thread.

---

### A9 — Credentials token is not bound to the site/user in the URL

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin |
| **Where** | `_pop_credentials_from_session` 1163–1177; `site_created` 1182–1212; `user_credentials` 1221–1237 |

**How it was found**  
Pop only checks token TTL. The view loads `Tenant`/`User` from the URL `pk` and shows `payload` regardless of `payload["user_id"]`.

**How to replicate**

1. Create site A; keep the `?token=` URL (do not open it yet), or copy the token.
2. Open `/dashboard/sites/<B>/created/?token=<A's token>` (or `/dashboard/users/<other>/credentials/?token=...`).
3. The password for user A is shown next to site/user B’s identity and URLs.

**Expected:** 404 / “token does not match this site.”  
**Actual:** Wrong-context password reveal. Easy to copy the wrong login into a client email.

---

### A10 — Force-verify custom domain skips DNS proof

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin |
| **Where** | `custom_domain_force_verify` 3835–3843 vs normal verify in `core/services/custom_domains.py` |

**How it was found**  
Any `@agency_operator_required` staff POST sets `is_verified=True` with no A-record check.

**How to replicate**

1. Add a domain you do not control.
2. Custom domains → **Force verify**.
3. Route-syncer / Let’s Encrypt path can treat it as verified.

**Expected:** Superuser-only emergency override, still logged/audited; or still require DNS.  
**Actual:** Any operator can mark arbitrary domains verified.

---

### A11 — Django admin shows live GHL OAuth tokens

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin with `/admin/` |
| **Where** | `core/admin.py` 109–122 (`readonly_fields` includes `access_token`, `refresh_token`) |

**How it was found**  
Readonly still **renders** the values in the change form.

**How to replicate**

1. Connect a GHL install in an environment that has tokens.
2. `/admin/` → GhlInstall / GhlAgencyInstall → open a row.
3. Tokens are visible in full.

**Expected:** Masked (`••••` + last 4) or superuser-only.  
**Actual:** Staff with admin access get third-party credentials; stolen admin session = GHL account.

---

### A12 — Block palette attach runs outside the create transaction

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Agency admin / new client |
| **Where** | `dashboard/views.py` ~1032–1084 |

**How it was found**  
`create_tenant_account` commits first; then `seed_block_types()` + `allowed_block_types.add(...)` run outside that atomic block.

**How to replicate**

1. Create a **block-shell** site.
2. Kill the process (or inject a failure) after the tenant exists but before palette attach.
3. Client can log in; Quick Add is empty / “that block isn’t available.”

**Expected:** All-or-nothing create.  
**Actual:** Credentials already issued for a broken builder.

---

### A13 — `check_subdomain` does not lowercase (form submit does)

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Agency admin |
| **Where** | `check_subdomain` 1097–1105 vs create `.lower()`; `SUBDOMAIN_RE` is `[a-z0-9-]` only. Documented in `CLAUDE.md` sharp edges. |

**How to replicate**

1. **+ New client**, type subdomain `MySite` (mixed case).
2. Live AJAX check reports **invalid**.
3. Submit anyway: create lowercases to `mysite` and can succeed.

**Expected:** Same normalization as create.  
**Actual:** Operators may abandon a valid name.

---

### A14 — Agency page rename crashes if `page_pk` missing

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Agency admin (broken client / crafted POST) |
| **Where** | `page_rename` 2237–2243: `int(request.POST["page_pk"])` |

**How to replicate**

```http
POST /dashboard/sites/<pk>/pages/rename/
```

with CSRF but no `page_pk` (or a non-integer). Browser form is fine; a missing field is a **500**, not a 400 message.

---

### A15 — Public-suffix DNS hint wrong for `example.co.uk`

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Agency admin |
| **Where** | `_dns_name_for_domain` ~3694–3707 (documented TODO: ≤2 labels = apex) |

**How to replicate**

1. Add custom domain `example.co.uk`.
2. Copy the DNS **name** the panel suggests.
3. Hint is host `example` instead of `@`.

`foo.example.co.uk` is fine; apex-on-PSL is wrong.

---

### A16 — Classic HTML page create / sibling import ignore the 20-page cap

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Agency admin |
| **Where** | Cap in `_page_create_shared` 2093–2098 only; `_page_create` 2024–2070 and `page_import_siblings` do not check `MAX_PAGES_PER_TENANT` (20) |

**How to replicate**

1. On a shell site already at 20 pages, use **paste HTML** create or **import siblings**.
2. Pages are created past the cap clients hit in the simple “New page” card.

---

### A17 — `Tenant.custom_domain` vs `CustomDomain` can drift

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Agency admin |
| **Where** | `core/models.py` 266–274 (commented as vestigial); `accounts.py` create writes both, later UI only touches `CustomDomain` |

**How to replicate**

1. Create a site with `Custom.Example.COM.`
2. Compare `Tenant.custom_domain` (raw-ish) vs `CustomDomain.domain` (normalized).
3. Change domains only in the Custom Domain panel; the tenant char field can stay stale in headers/bundles.

---

### C1 — Unpublishing the site still leaves published inner pages public

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client, agency, **public visitor** |
| **Where** | `core/views.py`: home/blog use `tenant.is_published` (29–32, 90–92, 120–122); `_render_page` 68–73 only checks `page.is_published`. `page_render_public` 62–65 never gates the tenant. |

**How it was found**  
Code diff of the three public gates. **Proved:** unpublished tenant, published `about` → `/site/acme/` 404, `/site/acme/about/` 200.

**How to replicate**

1. Publish home **and** an inner page (e.g. About).
2. Unpublish the **site** / home (editor Unpublish on home, or site detail).
3. Logged out: open `/` or `/site/<sub>/` → 404 / login redirect. Good.
4. Logged out: open `/about/` or `/site/<sub>/about/` → **200**, full HTML.

**Expected:** Site unpublish takes **all** public surfaces offline (matching the Unpublish confirm: visitors get “not found”).  
**Actual:** Inner URLs stay live.

**Important:** `api/tests/test_mcp_publish_page.py` `test_page_publish_reachable_even_when_site_unpublished` currently **encodes the opposite** as a “design decision.” Dashboard copy and operator mental model still treat site unpublish as “site offline.” If you keep the MCP behavior, the dashboard Unpublish copy is wrong; if you keep the dashboard copy, the MCP test and `_render_page` must change.

---

### C2 — Copy page / copy home drops nested `children`

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client (and agency using the same create flow) |
| **Where** | `_page_create_shared` 2132–2141 |

**How it was found**  
Clone loop copies only `id` / `type` / `fields`. **Proved** against a row with two column children: cloned dict has no `children`.

**How to replicate**

1. On a block-shell home, add **Section → 2 Column**. Put a Headline (or Hero) in each column. Save.
2. **Pages → New page → Copy home** (or copy that page).
3. Open the new page editor.

**Expected:** Same structure with fresh instance ids (deep clone, like `cloneInstance` in `editor.js` 1519–1529).  
**Actual:** Empty row shells; column content gone. Looks like “copy worked” until you look at the columns.

---

### C3 — Gallery delete rewrites content without a version snapshot

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client / editor |
| **Where** | `_media_item_mutate` 3507–3521; snapshots only happen inside `save_editable_content` |

**How it was found**  
DELETE deletes the asset then `_scrub_url_from_content` + `tenant.save` / `page.save` directly.

**How to replicate**

1. Upload an image, use it on a **Live** page, confirm public render shows it.
2. Gallery → delete that upload.
3. Public page: image fields blanked immediately.
4. Editor **Undo**: cannot restore the URLs (no snapshot of the pre-scrub content).

**Expected:** Snapshot then scrub, or soft-delete + “remove from page?” confirm.  
**Actual:** Instant public breakage; undo useless.

---

### C4 — Password reset does not invalidate existing sessions

| | |
|---|---|
| **Severity** | P1 (security) / documented sharp edge |
| **Persona** | Client (victim); agency (thinks they kicked the session) |
| **Where** | `user_reset_password` 1627–1635 — `set_password` only. No `update_session_auth_hash`, no session flush. `CLAUDE.md` sharp edges. |

**How to replicate**

1. Client is logged in on the tenant host (keep that tab).
2. Agency resets that user’s password.
3. Old tab still works until cookie expiry (~2 weeks).
4. New password only matters for **new** logins.

**Expected:** Other sessions die (or at least `update_session_auth_hash` for the operator’s session + flush others).  
**Actual:** Stolen/old session survives a “we reset it” support action.

---

### C5 — Live = instantly public is easy to miss

| | |
|---|---|
| **Severity** | P3 (architecture + residual UX) |
| **Persona** | Client |
| **Where** | Session report 2026-08-25; `editor.html` status pill; no draft content column |

There is **no** draft-vs-published content split. `is_published` is a visibility gate. Autosave on a Live page **is** the public page.

**How to replicate**

1. Publish a page (pill = **Live**).
2. Typo a headline; wait ~600ms.
3. Open the public URL in a private window — typo is live.
4. Unpublish only hides the URL; it does not roll back content.

The pill/confirm help; a client editing “quietly” on Live still publishes mid-typo. Filed as residual risk, not a logic error.

---

### C6 — Underscore subdomains are valid in Django admin, invalid as Host

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Client / admin (local and any admin-created slug) |
| **Where** | `Tenant.subdomain` is `SlugField` (allows `_`); `SUBDOMAIN_RE` in the dashboard form does not. Documented in `docs/LOCAL_SETUP_AND_BLOCK_EDITOR.md`. |

**How to replicate**

1. Django admin → Tenant → subdomain `sample_website`.
2. Visit `http://sample_website.localhost:8000/` → `DisallowedHost`.
3. `/site/sample_website/` still works as the fallback path.

Dashboard new-client form already forbids `_`. Admin/ORM does not.

---

### E1 — Same-origin unsandboxed preview + raw Code block = staff XSS

| | |
|---|---|
| **Severity** | P0 |
| **Persona** | Client (author) → **Editor / agency staff** (victim). Public visitors also execute the script on the **client’s** site (opt-in raw HTML). |
| **Where** | `core/renderer.py` 994–999, 367–368; `templates/dashboard/editor.html` 385–387 (iframe, no `sandbox`); Week Plan still lists “Code/embed block safety review” as unfinished P0 |

**How it was found**  
Renderer comment: “Client-controlled raw HTML, rendered unsanitized by design.” Preview iframe is same-origin so the postMessage bridge works. **Proved:** `preview=True` HTML contains `<script>window.__pwned=1</script>` from a code field.

**How to replicate**

1. Block-shell site with the seeded **Code** primitive (`seed_builder_blocks`).
2. As client: Quick Add → Code. Paste:

   ```html
   <script>
   // Proof: parent is the dashboard. Do not run this against production.
   console.log('preview origin', location.origin);
   console.log('can see parent?', window.parent !== window);
   </script>
   ```

3. Save; wait for preview to load. DevTools on the **iframe** shows the script ran.
4. Log in as **staff** on the agency host, open **Sites → that site → Edit**. Preview runs the same script **in the staff browsing context** (same origin as `/dashboard/`, CSRF token in `window.CMS`).

A hostile payload can read `parent.document`, `window.CMS.csrfToken`, and `fetch` dashboard save/publish/user URLs.

**Expected:** Preview sandboxed (`sandbox="allow-scripts allow-same-origin"` is **not** enough if same-origin — need an opaque origin **or** strip/neutralize `script` in `preview=True`). Public site may still allow raw HTML by product decision.  
**Actual:** Preview is a full same-origin agent of the dashboard.

**Product distinction:** Raw HTML on the **published client site** is an accepted (dangerous) feature. The bug is executing it **inside the CMS chrome**.

---

### E2 — Stored XSS via `content_json|safe` in the editor bootstrap

| | |
|---|---|
| **Severity** | P0 |
| **Persona** | Anyone who opens the editor (client or staff) |
| **Where** | `templates/dashboard/editor.html` 728–735; `dashboard/views.py` 2860 `json.dumps(content)` (same for `palette_json`, `block_defaults_json`) |

**How it was found**  
HTML parsers close `</script>` even inside a JS string. Python `json.dumps` does **not** emit `\u003c`. **Proved:** dumped JSON contains a literal `</script>`.

**How to replicate**

1. Open the editor. In any **text** field (or a field that round-trips into `content`), set the value to:

   ```text
   </script><script>alert(document.domain)</script>
   ```

2. Wait for autosave.
3. Hard-refresh the editor (or have a colleague open it).
4. The bootstrap `<script>` ends at the first `</script>`; the second tag runs on the dashboard origin. `csrfToken` is already in the same `window.CMS` object.

**Expected:** `json_script` filter, or `json.dumps(..., ).replace("<", "\\u003c")`, or a `type="application/json"` tag + `JSON.parse`.  
**Actual:** Classic stored XSS. A client can attack staff who open their site.

`palette_json` / `block_defaults_json` are the same class if a BlockType label ever contains `</script>` (agency-controlled, lower probability).

---

### E3 — `text-update` writes unsanitized HTML into the parent editor

| | |
|---|---|
| **Severity** | P0 |
| **Persona** | Editor (amplifies E1 / E9) |
| **Where** | `static/js/editor.js` `cmsOnTextUpdate` 877–889 |

**How it was found**  
Richtext hydrate uses `cmsScrub`. Canvas updates do `rich.innerHTML = p.html` with no scrub, then `scheduleSave()`.

**How to replicate**

1. Open the editor with a richtext field (id from `data-bind`, e.g. `hero.body`).
2. From the preview iframe console (or any same-tab script):

   ```js
   parent.postMessage({
     source: "cms-preview",
     type: "text-update",
     payload: {
       id: "<that-richtext-id>",
       html: "<img src=x onerror=alert(document.cookie)>"
     }
   }, "*");
   ```

3. The **parent** form’s contenteditable executes the handler; autosave may persist it.

**Expected:** Same scrub as initial hydrate; ignore messages not from `previewFrame.contentWindow` / not same origin.  
**Actual:** Parent DOM XSS + poisoned content.

---

### E4 — Undo races with in-flight autosave

| | |
|---|---|
| **Severity** | P0 (data loss) |
| **Persona** | Client / editor |
| **Where** | `initUndo` 1836–1871 (no `flushSaveThen`); `save()` 214–272; `restore_editable_content` 149–187 |

**How it was found**  
Publish was patched to `flushSaveThen` (2026-08-25). Undo still fires restore + `location.reload()` while `saveInFlight` can complete afterward with the **pre-undo** body.

**How to replicate** (timing-sensitive)

1. Make an edit that triggers autosave; watch status **Saving…**.
2. Immediately click **Undo** (toolbar), or `Ctrl/Cmd+Z` when focus is **not** in an input (keydown 1291–1301 skips undo while typing).
3. If the in-flight POST lands after restore, content snaps back to the edit you undid.

**Expected:** Undo waits for / aborts in-flight save (same as publish), then restores.  
**Actual:** Last HTTP writer wins; undo can silently vanish.

---

### E5 — Undo with dirty debounce discards keystrokes

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client / editor |
| **Where** | Debounce 600ms (`scheduleSave` 206–211); Undo does not flush; `beforeunload` 277–280 does not cover in-app Undo |

**How to replicate**

1. Type in a field.
2. Within ~600ms (status still dirty / not yet Saving), click **Undo**.
3. Typed text is gone; restore reloads last **saved** snapshot.

**Expected:** Flush or “Undo will discard unsaved typing — continue?”  
**Actual:** Same class of bug publish already fixed.

Native `Ctrl+Z` **inside** a field is browser undo (keydown returns early while typing). The trap is the **toolbar Undo** and shortcut when focus is on the canvas/chrome.

---

### E6 — CSS injection via `_global`, brand tokens, color fields, `fontSize`

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client → public visitors (and preview) |
| **Where** | `_apply_global_styles` 1203–1235; `_apply_brand_tokens` 1090–1103; `_apply_field` color 980–984; `_apply_element_styles` 1137–1153 (`fontSize` / `fontFamily` / `fontWeight` / `align` skip `_SIMPLE_STYLE_KEYS`); save `_normalize_styles` 2907–2943 only truncates to 120 chars. Contrast: `_apply_tokens` 1238–1254 **does** use `_safe_css_value`. |

**How it was found**  
Renderer vs `_safe_css_value`. **Proved** all four: global breakout CSS, brand `--primary` breakout, color `background-image`, `fontSize` `position:fixed`. Save path keeps the global breakout string.

**How to replicate (Design / global — easiest UI path)**

1. In the editor Design / page background / text color, if there is a **text** input (not only `<input type="color">`), set:

   ```text
   red;} body{display:none !important
   ```

2. If the UI only has a color picker, use DevTools / a crafted save POST with:

   ```json
   {"content": {"_global": {"textColor": "red;} body{display:none !important"}}}
   ```

   (CSRF + session as a logged-in editor.) `_normalize_styles` will **accept** it.

3. View public HTML: extra `body{display:none}` rule.

**Brand tokens**

1. Change a Brand color field to `red;}body{opacity:0}/*`
2. Public `<style data-tokens>` interpolates it.

**Color field**

1. Color text input: `red; background-image: url(https://evil.example/x)`
2. Element `style` contains the extra declaration.

**fontSize**

1. Style panel size, or `_styles["hero.title"].fontSize` = `16px; position:fixed; inset:0; background:red`
2. `lineHeight` is blocked (`test_unsafe_typography_value_is_skipped`); `fontSize` is not.

**Expected:** Same allowlist as `_tokens` / `lineHeight`.  
**Actual:** Inconsistent sanitizer; overlay phishing / defacement on the live site.

---

### E7 — `javascript:` and arbitrary URLs on link / image / video / embed

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client → public visitor |
| **Where** | `_apply_field` 960–993; preview bridge 367–390; `editor.js` `linkLooksValid` 2286–2350 (warns but **still saves**: “it’ll still save”); Test button `window.open(v)` |

**How it was found**  
Blog/template sanitizers strip `javascript:`; field types do not. **Proved** for link `href`.

**How to replicate**

1. Link field → Custom → `javascript:alert(document.domain)`.
2. UI may show a warning (URL has no `host`); save still happens.
3. Stronger bypass that **passes** `linkLooksValid` (`protocol && host`):

   ```text
   javascript://example.com/%0Aalert(document.domain)
   ```

   `new URL(...)` has protocol + host, so no warning.
4. Publish; visitor clicks the CTA → JS in their browser.
5. **Test** button may `window.open` the same URL in the editor user’s browser.

Repeat with **embed** `src` = `javascript:…` or `data:text/html,…` (iframe execution). Image/video `src` similarly unconstrained.

**Expected:** Allow `http(s)`, `/`, `#`, `mailto:`, `tel:` only.  
**Actual:** Click XSS without needing the Code block. Richtext links **are** sanitized; these fields are not.

---

### E8 — Duplicate / add cap ignores subtree size

| | |
|---|---|
| **Severity** | P1 |
| **Persona** | Client / editor |
| **Where** | `duplicateBlock` / `addBlock` 1498–1542: `countAllInstances() >= MAX` **before** clone; server `_normalize_regions` 3118–3121 counts the **resulting** tree |

**How to replicate**

1. Fill a page to **38 / 40** blocks (e.g. many paragraphs).
2. Add a 2-column row with 3 nested blocks (row + 2 children = 3, or more).
3. **Duplicate** that row. Client count check sees 38 &lt; 40 and proceeds.
4. Save 400s with the cap error. Local tree already has the clone; `structuralReload` may stay true (E10).

**Expected:** Pre-check `current + subtree_size <= 40`.  
**Actual:** Failed save + confusing editor state near the cap.

---

### E9 — postMessage bridge trusts `source` string only

| | |
|---|---|
| **Severity** | P2 (amplifier for E1/E3) |
| **Persona** | Editor |
| **Where** | `editor.js` 779–833; renderer preview script 356–358; `postMessage(..., "*")` |

Parent accepts any message with `data.source === "cms-preview"` — no `e.origin`, no `e.source === iframe.contentWindow`. Child accepts any `cms-editor`.

**How to replicate**

From **any** frame in the tab (or an extension):

```js
window.postMessage({
  source: "cms-preview",
  type: "block-action",
  payload: { id: "<real-instance-id>", action: "delete" }
}, "*");
```

If the id exists, the editor deletes a block (after confirm on `deleteBlock` — `duplicate` has **no** confirm).

**Expected:** `event.source === previewFrame.contentWindow` and explicit origin.  
**Actual:** Compromised preview (E1) or another iframe can drive duplicate/delete/add/save.

---

### E10 — Failed structural save leaves `structuralReload` stuck

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Editor |
| **Where** | `saveAndReload` 1319–1321 sets `structuralReload = true`; `save()` catch 260–272 clears `saveInFlight` but **not** `structuralReload` |

**How to replicate**

1. Trigger E8 (cap 400) or any structural save failure (network, 403).
2. Next **successful** field save may full-page reload unexpectedly (`structuralReload` still true on the success path 245–253).

---

### E11 — Depth cap silently strips nested children on save

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Client / editor |
| **Where** | `clean_instance` 3087–3103: at `depth >= MAX_BLOCK_DEPTH` (2), children omitted with **no** error. Tests: `core/tests/test_blocks.py` 414–432 (save path). Renderer also drops past depth (`renderer.py` ~1708). |

**How to replicate**

1. Nest row → column → row → column → leaf (deeper than 2).
2. Drag UI mostly prevents this; a crafted save JSON or older snapshot does not.
3. Save **200**; deepest children disappear with no toast.

**Expected:** 400 “too deep” like the 40-block cap.  
**Actual:** Quiet data loss.

---

### E12 — Undo version list is not filtered to `source=dashboard`

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Client / editor on sites also written via MCP |
| **Where** | `_versions_list` 3150–3156; `content_versions.py` keeps MCP vs dashboard **buckets** so AI bursts do not flush human undo — but the list endpoint returns mixed rows ordered by `saved_at`. Undo uses `versions[0]`. |

**How to replicate**

1. Edit in the dashboard (human snapshot).
2. MCP `patch_content` writes (MCP snapshot, newer).
3. Click Undo: may restore/pop an MCP snapshot, not the last human edit.

Retention is separate; **selection** is not.

---

### E13 — Restore / Django admin JSON bypass storage caps

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Editor / admin |
| **Where** | Caps enforced in dashboard `_normalize_regions` only. `restore_editable_content` writes `version.snapshot` with **no** normalize. Admin can edit `Tenant.content` / `Page.content` JSON. |

**How to replicate**

1. Save a page at 40 blocks.
2. Django admin: paste a 41st instance into `content.regions.main`.
3. Open public page — 41st block can render (count is not re-checked at render).
4. Or undo to a pre-cap snapshot that is deeper than `MAX_BLOCK_DEPTH`; renderer drops extras, dashboard restore does not reject.

---

### E14 — Instance IDs not sanitized (selector / attribute hazards)

| | |
|---|---|
| **Severity** | P2 |
| **Persona** | Client via crafted save / API |
| **Where** | `iid = str(inst.get("id") or "").strip()[:64]` (3070–3073) — no charset allowlist. Selectors: `editor.js` `querySelector('...[data-bind="' + p.id + '"]')`, renderer `querySelectorAll('[data-edit="' + fid + '"]')`. |

**How to replicate**

Save a block id containing `"]` or quotes. Form wiring / `apply-content` can throw or match the wrong nodes. Server will keep the string if unique.

**Expected:** `^blk_[a-z0-9]+$` (or whatever `new_instance_id` emits).  
**Actual:** Attribute/selector injection into editor + preview.

---

### X1 — Diagnostic headers leak runtime security config

| | |
|---|---|
| **Severity** | P3 |
| **Persona** | Anyone who can `curl` the app |
| **Where** | `DiagnosticHeaderMiddleware` `core/middleware.py` 19–33 |

**How it was found**  
**Proved** on `GET /login/`:

```
X-Diag-Debug: True
X-Diag-Iframe-Embed: True
X-Diag-Csrf-Samesite: None
X-Diag-Csrf-Secure: True
X-Diag-Ghl-Auto-Login: False
```

**How to replicate**

```powershell
curl.exe -sI http://localhost:8000/login/
```

**Expected:** DEBUG-only or internal/admin.  
**Actual:** Always on — helps fingerprint CSRF cookie flags and DEBUG in production.

---

## 6. Editor / client UX issues still in source (not re-tested in a browser)

These were originally logged in `docs/UI_AUDIT.md` (2026-08-14) and/or `docs/WEEK_PLAN.md`. Code still matches unless noted.

| Issue | Still looks true? | Notes |
|-------|-------------------|--------|
| Editor not usable as a three-column grid on phones | **Partially fixed** | `editor.css` ~1297+ has a mobile switcher under 1100px (`display:block` + tabs). Not a hard overflow bug anymore; drawer/Quick Add still dense (Week Plan empty-state audit still open). |
| Sidebar section links are `<div class="sidebar-link">` | Likely still | Keyboard/AT cannot treat them as buttons. |
| Settings / history / gallery modals lack `role="dialog"` / focus trap | Likely still | Week Plan P1 empty/error audit. |
| Autosave retries | **Improved** | Status machine + retry button exist; concurrent request sequencing is better (`saveInFlight` / queue) but undo/publish still special-cased. |
| Contrast of muted text `#8a8fa3` | Unknown without measuring | Audit calculated 3.21:1. |
| Duplicate is full-page reload | **By design this week** | Week Plan: live duplicate deferred; reload restores scroll. Not filed as a functional bug. |
| Drag-and-drop image onto image field | Week Plan P1, marked in progress | Not verified. |
| Unload warning | `beforeunload` exists if `hasUnsavedChanges` | Does not cover Undo (E5) or in-flight vs queued edge cases. |

Do **not** treat the 2026-08-14 “7/20 Poor” score as current without a new visual pass — the Tailwind/Basecoat overhaul and mobile switcher landed after that audit.

---

## 7. Things that look like bugs but are product decisions

| Topic | Why it is not filed as a defect |
|-------|----------------------------------|
| Clients cannot add pages on **classic** (non-shell) templates | `_user_can_manage_pages` — locked-structure promise. Shells are the exception. |
| Clients cannot paste raw HTML | Same promise; Code block is the intentional hole. |
| `is_published` is a visibility gate, not a draft snapshot | Documented 2026-08-25. C5 is UX residual, not a missed feature. |
| MCP: inner page live while site unpublished | Explicit test in `test_mcp_publish_page.py`. Still a **dashboard** bug (C1) until copy and implementation agree. |
| Raw HTML Code block on the **public** site | Opt-in; tests assert markup survives. Danger is editor preview (E1). |
| Live duplicate reloads the editor | Deferred on purpose. |
| Roles `owner` vs `editor` have the same permissions | `CLAUDE.md` — descriptive only. |
| Staff can edit any tenant without membership | `user_can_edit` short-circuit — by design. |

---

## 8. Suggested fix order

1. **E2** — HTML-escape JSON in the editor bootstrap (`json_script` / `\u003c`). Small, stops stored XSS on every editor load.  
2. **E1 + E3 + E9** — Sandbox or opaque-origin preview; neutralize `code` when `preview=True`; `cmsScrub` on `text-update`; check `event.source`.  
3. **A1** — Block `page_edit_html` when `page.template_id == tenant.template_id` (or clone-on-write). Fix the lying subtitle.  
4. **A2** — `on_delete=PROTECT` (or equivalent) on `Tenant.owner`.  
5. **C1** — Gate `_render_page` on `tenant.is_published` **or** change Unpublish copy + MCP tests so they match.  
6. **C2** — Deep-clone `children` with new ids in `_page_create_shared`.  
7. **E6 + E7** — Run `_safe_css_value` / scheme allowlists on save **and** render.  
8. **E4 + E5** — `flushSaveThen` (or abort in-flight) before Undo.  
9. **A3 + A4 + C4** — Protect owner membership, protect superuser, invalidate sessions on password reset.  
10. **C3, E8, E10–E13, A6–A12, X1** — as listed.

---

## 9. How to re-run the proved checks locally

Python 3.12 venv from `docs/LOCAL_SETUP_AND_BLOCK_EDITOR.md`. From the repo root:

```powershell
.\.venv\Scripts\python.exe -c "import json; print('</script>' in json.dumps({'x': '</script><script>alert(1)</script>'}))"
```

Must print `True` until E2 is fixed.

Manual public-page leak (C1), after `runserver` and a tenant with an unpublished site + published inner page:

```powershell
curl.exe -sI http://localhost:8000/site/<sub>/
curl.exe -sI http://localhost:8000/site/<sub>/<slug>/
```

Expect today’s bug: home **404**, inner page **200**.

Diagnostic headers (X1):

```powershell
curl.exe -sI http://localhost:8000/login/
```

Look for `X-Diag-Debug`.

Renderer injections (E6/E7/E1) can be re-checked with a Django shell calling `_apply_global_styles`, `_apply_brand_tokens`, `render_site`, and `render_page_from_blocks(..., preview=True)` the same way this review did on 2026-08-27.

---

## 10. Environment notes (not bugs, but they block replication)

| Trap | What happens |
|------|----------------|
| Wrong Python | `WRONG PYTHON` banner — use `.venv` 3.12, not conda 3.11. |
| `DATABASE_URL` on Railway internal host | `getaddrinfo failed` locally — keep it commented for SQLite. |
| Tests without `collectstatic` | Mass `Missing staticfiles manifest` failures. |
| Underscore subdomain | `DisallowedHost` before Django views run. |
| Opening tenant editor on agency host | You get the **agency** editor for that pk (`/dashboard/sites/<id>/edit/`), not the client shell. Client UX must be tested on `<sub>.localhost:8000`. |

---

*End of review. No production credentials are included. Local superuser setup remains in `docs/LOCAL_SETUP_AND_BLOCK_EDITOR.md`.*
