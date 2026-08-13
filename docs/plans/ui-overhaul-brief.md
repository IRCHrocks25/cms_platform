# Brief: CMS platform UI/UX review and overhaul

You are the implementer. This file is your complete brief — read it in full before
touching anything, and re-read it if you lose context. Everything here was verified
against the repo on 2026-08-14; where a line number is cited, check it still matches
before relying on it.

Your working root is a git worktree on branch `feat/cms-ui-overhaul`. Push to that
branch. It is already wired to a live staging deploy (see **Staging**), so every push
becomes something a human can click.

---

## 1. What the user asked for, in their words

1. An **impeccable review and overhaul of the CMS platform's UI/UX**.
2. "We need to use better shell/coding/more efficient/standard loading for the editors."
3. "Following better industry patterns, using component libraries? (shadcn?)"
4. "Can we have a staging environment to test/see this UI without affecting the main
   deployment" — **already done, by the orchestrator, before you started.** Don't rebuild it.
5. "Adding a consolidated view to the sites/client page for the pages view link."

Read (2) as four separate complaints, because it is one: the editor **shell** (its
chrome and layout), the **code**-editing surface, perceived **efficiency**, and the
absence of **standard loading** affordances. All four are in scope.

## 2. Decisions already made — do not relitigate

| Question | Answer | Consequence |
|---|---|---|
| Component library | **Tailwind CSS + Basecoat UI** | shadcn's visual language, server-rendered, no React. Adds a Node build step to a repo that has none. |
| Scope | **Audit, then implement** | Write the audit first, then build. Both on this branch. |
| Staging | **Provisioned already** | `https://staging.sites.katek.app`, tracking this branch. |

The user considered and rejected two alternatives: a no-dependency vanilla token layer,
and a React island for the editor. Do not drift back toward either. In particular, **do
not introduce React** — Basecoat is plain HTML/CSS with optional Alpine for interactive
components, and that is the point of choosing it.

## 3. The stack you are working in

Django 5.1.2, server-rendered templates, hand-written CSS and vanilla JS. No bundler,
no `package.json`, no `node_modules` — you are the one adding that.

- **Python is hard-pinned to 3.12.** `.python-version`, the `FROM python:3.12-slim` in
  `Dockerfile`, and `REQUIRED_PYTHON` in `cms_platform/python_version.py` must agree;
  `cms_platform/tests/test_python_version.py` fails if they don't. On 3.14 you get ~102
  phantom test errors from a Django incompatibility, not a clear failure. If you see the
  WRONG PYTHON banner, delete `.venv` and rebuild.
- **Static files:** `cms_platform/settings.py:234` uses whitenoise
  `CompressedManifestStaticFilesStorage` — hashing and gzip are already handled by
  `collectstatic`. Your Tailwind build emits *into* `static/`; it does not replace
  `collectstatic`, and it must run **before** it.
- **Read `CLAUDE.md` in full.** It is 33 KB and it is accurate. The sections that matter
  most to you: "Two dashboards: agency vs tenant", "Design tokens", "How the editor
  works", "Common tasks", "Constraints / non-goals", "Known sharp edges".

### The surfaces in scope

| File | Lines | What it is |
|---|---|---|
| `templates/base.html` | 178 | App shell: sidebar, mobile bar, nav |
| `templates/dashboard/editor.html` | 570 | The three-column editor — the main event |
| `templates/dashboard/_html_source_editor.html` | 470 | Raw HTML source editing (agency only) |
| `templates/dashboard/tenant_detail.html` | 352 | The client/site page — target of ask (5) |
| `templates/dashboard/page_list.html` | 213 | Inner pages list — to be consolidated |
| `templates/dashboard/tenant_list.html` | 110 | Sites index |
| `static/css/base.css` | — | All app chrome. Already has `.data-table`, `.stat-card`, `.badge-*`, `.filter-pill` |
| `static/css/editor.css` | — | Editor layout + adaptive breakpoints |
| `static/js/editor.js` | — | Field binding, autosave (`scheduleSave`), preview bridge |
| `static/js/blog_editor.js` | — | `contenteditable` rich text with a formatting toolbar |
| `dashboard/views.py` | 3754 | Every dashboard view. `_render_editor` computes the adaptive layout class |

Plus the other ~20 templates under `templates/dashboard/`. The overhaul is not done if
the editor looks new and the rest of the app doesn't.

### How the editor actually works — read before you refactor it

Three columns: a sidebar auto-generated from `schema.sections` grouped by `data-group`,
a form panel rendering one section per `data-section` via
`templates/dashboard/components/field.html`, and a preview iframe.

The layout class adapts to section count, computed in `dashboard/views.py::_render_editor`:
`compact` (≤6 sections, sidebar hidden), `standard` (7–15), `dense` (16+, adds search).

**The postMessage bridge is load-bearing.** Parent and iframe agree on these:

| Direction | type | payload |
|---|---|---|
| iframe → parent | `ready` | `{}` |
| iframe → parent | `focus-field` | `{ id: "hero.title" }` |
| parent → iframe | `apply-content` | `{ "hero.title": "...", ... }` |
| parent → iframe | `highlight-field` | `{ id: "hero.title" }` |

Every message carries `source: "cms-editor"` (parent) or `"cms-preview"` (iframe).
**Changing either string, or any type name, breaks both sides silently.** If you must
change one, change both in the same commit and test the round trip.

**Two different previews exist. Do not conflate them.** The editor preview is
server-rendered through `render_site(preview=True)` with the bridge injected. The
template-author preview on `template_form.html` is pure client-side
`iframe.srcdoc = textarea.value` with no server round trip and no bridge — it
deliberately shows raw pasted HTML, and its header says so.

## 4. Hard rules

### Product constraints that still stand (from `CLAUDE.md`)

These are the product's promises, not style preferences. Breaking one is a defect no
matter how good it looks.

- **No section add/remove for clients.** This is the defining promise of the product.
  Never add UI that lets a client insert a new section.
- **No raw HTML editing for clients.** Rich text via `contenteditable` is the only way
  they touch markup. (`_html_source_editor.html` is agency-side — that one is fair game.)
- **Schema is derived, not stored.** `Template.save()` always rebuilds it from
  `build_schema(html_source)`.
- **`Tenant.content` is canonical.** Never compute a display value that bypasses
  `merge_with_defaults()` — that helper is what makes an empty field fall back to the
  template default.
- **Generated passwords are never persisted in plaintext**, never in URLs, logs, or
  audit rows.
- **No per-tenant user models.** All users are Django `User` rows; access is
  `TenantMembership`.

### The one constraint that was lifted

`CLAUDE.md:498` says "No new dependencies casually… Don't add jQuery, htmx, alpine, etc.
without discussing first." That discussion happened and the user chose Tailwind +
Basecoat. **Update that section** to record what was decided and why, so the next
person doesn't read a rule the codebase no longer follows. Keep the spirit: Tailwind,
Basecoat, and Alpine are in; a fourth dependency still needs a reason.

### Production hazards — these will cause an outage if you get them wrong

Staging and production run on the **same Docker host**.

1. **`Dockerfile` is shared with production.** Your Node build stage lands in the image
   production builds from. Make it multi-stage (node builder → copy the emitted CSS into
   the Python stage), keep the final stage `python:3.12-slim`, and **verify the
   production compose still builds** before you call the work done. If the Node stage
   breaks, production's next deploy breaks.
2. **Never change `docker-compose.yml`** (production) as part of this work. If you think
   it needs a change, stop and report instead.
3. **Never weaken the three protections in `docker-compose.staging.yml`.** They are
   documented at the top of that file and in `deploy/STAGING.md`: a distinct image tag,
   `cmsstg-`-prefixed Traefik names, and no `route-syncer`. The third one matters most —
   `core/services/traefik_routes.py:40` writes a fixed filename into a shared Traefik
   directory, so a second syncer would delete every client's custom domain.
4. **Tailwind Preflight is a global reset, and client HTML renders inside dashboard
   pages.** `static/js/blog_editor.js` puts client markup in a `contenteditable` in the
   dashboard DOM — that is *not* iframe-isolated the way the site preview is. An
   unscoped Preflight restyles real client content. Scope it, or neutralize it inside
   the editing surface, and prove it with a before/after on a post that has headings,
   lists, and links.

## 5. Staging

| | |
|---|---|
| URL | https://staging.sites.katek.app |
| Login | `admin` / password is in the Dokploy env var `DJANGO_SUPERUSER_PASSWORD` on the `sites-staging` compose service |
| Tracks | this branch, `feat/cms-ui-overhaul`, autoDeploy **on** |
| Database | its own Postgres (`cms-staging-db`), empty at first boot, seeded by you |
| Dokploy | project `cms-dashboard` → environment `staging` → service `sites-staging` |

Pushing to this branch redeploys staging. Give it a couple of minutes, then check
`https://staging.sites.katek.app/healthz` returns `{"status": "ok"}`.

**What staging cannot do, by design:** custom-domain routing (no route-syncer) and
tenant public sites on their own subdomain (the Cloudflare Origin cert wildcard is
single-label, so `acme.staging.sites.katek.app` fails TLS). The dashboard, the editor,
and the editor's same-host preview iframe all work. Verify tenant public renders
locally instead — `manage.py runserver`, then `acme.localhost:8000`, which resolves per
RFC 6761 with no hosts-file edit.

To make staging useful you need data in it: sign in, create a Template from
`samples/restaurant.html`, then create a client site. Follow the first-run smoke test in
`CLAUDE.md`. **Never restore a production dump into staging** — it carries real client
content and real user rows.

## 6. Design standards

A full design skill is on this machine at `/home/bernardjr/.claude/skills/impeccable/`.
Read `SKILL.md`, then these three, and hold yourself to them:

- `reference/operate.md` — this app is **Operate** mode: the visitor is completing a
  task, so scanability, consistency and native expectations outrank expression. Brand
  lives in precise details, not in decoration.
- `reference/craft-floor.md` — the quality floor and the absolute bans. Load it
  immediately before you start editing UI, not during planning.
- `reference/audit.md` and `reference/critique.md` — for the audit phase.

Two things from that skill that bear directly on this job:

- **Refinement preserves; redesign replaces — never split the difference.** Decide which
  this is in the audit and say so. The default here is *redesign of the visual world,
  preservation of product truth*: same content, same functions, same affordances, same
  constraints; the old look is evidence and anti-reference, not a starting point.
- **Verify in bounded passes, not a loop.** Build fully, inspect once with a batched
  round covering desktop and mobile together, fix everything it surfaces in one batch,
  confirm with at most one more round, then stop. Open-ended self-QA burns money.

### Existing visual identity

Roboto (300/400/500/700), white `#fff`, black `#0a0a14`, blue `#2563eb`, navy `#1e3a8a`
(stored in `--color-purple*` tokens — the names are back-compat for `.btn-purple` /
`.badge-purple`, which is itself worth cleaning up). Radii 8/12/16/24. Spacing scale
`--space-1` … `--space-8`, 4–64px.

Default to **keeping the palette and typeface** and replacing the structural and
interaction language. If the audit makes a real case for changing them, make it
explicitly and get it into the audit doc rather than doing it silently.

## 7. Phases and deliverables

### Phase 0 — Audit → `docs/UI_AUDIT.md`

Read every template under `templates/dashboard/`, `static/css/*`, `static/js/*`, and
enough of `dashboard/views.py` to know what renders what. Then write the audit:

- Findings ranked by severity, each with `file:line` and a concrete failure scenario.
  "Inconsistent" is not a finding; "the save button on `tenant_form.html:88` gives no
  feedback for the 400ms the POST takes, so users double-submit" is.
- Cover: information architecture, visual hierarchy, cognitive load, the four editor
  complaints, loading and async states, error and empty states, keyboard and focus
  behavior, colour contrast against WCAG AA, responsive behavior at 375 / 768 / 1280 /
  1920, and UX copy.
- A phased implementation plan with an explicit statement of what you are **not** doing.

Push the audit and report before you start Phase 1. Do not wait for approval — but make
the audit reviewable on its own.

### Phase 1 — Build pipeline

`package.json`, Tailwind, Basecoat, a build that emits into `static/`, and a multi-stage
`Dockerfile`. Requirements:

- The Node stage must not appear in the final image. Final stage stays `python:3.12-slim`.
- Build order in the image: npm build → `collectstatic`. Confirm the manifest picks up
  the emitted CSS and that a hashed filename is served.
- A developer with no Node installed must still be able to run the test suite. Commit
  the built CSS, or make the Django side degrade cleanly — decide, document it in
  `CLAUDE.md`, don't leave it ambiguous.
- Pin versions. Commit the lockfile.
- **Then build the production compose locally and confirm it still works.**

### Phase 2 — Design system

Basecoat components mapped onto the existing tokens. Replace the ad-hoc classes in
`base.css` with a coherent layer. Every dashboard template moves onto it — not just the
editor. Delete what you replace; do not leave two systems side by side.

### Phase 3 — The editors

This is the user's headline complaint, so it gets the most care.

- **Shell:** the three-column layout, its adaptive `compact`/`standard`/`dense` modes,
  and how it behaves when the browser is narrow. Today the sidebar hides below a
  breakpoint computed from section count — check that this actually serves the user
  rather than the code.
- **Coding surface:** `_html_source_editor.html` is 470 lines of hand-rolled raw-HTML
  editing with no syntax highlighting, no bracket matching, no line numbers. Now that
  dependencies are permitted, **CodeMirror 6** is the standard answer. Evaluate it in
  the audit; if you adopt it, keep the bundle lean (it is modular — do not ship every
  language mode).
- **Loading and async:** this is the "standard loading" ask. Autosave (`scheduleSave` in
  `editor.js`) needs honest state — idle / saving / saved / failed, with a real recovery
  path on failure. The preview iframe needs a loading state instead of a blank frame.
  Long operations (AI annotation can run past 60s; gunicorn's worker timeout is 180s and
  `OPENAI_TIMEOUT` is 120s) need progress, not a spinner that lies. Skeletons where
  content is coming; never a layout that jumps when it arrives.
- **Efficiency:** measure before you optimize. If the editor is slow, say what is slow
  and by how much.

### Phase 4 — Consolidated pages view

Today `dashboard/urls.py:56` puts inner pages at `sites/<pk>/pages/`, a separate
destination from the client page at `sites/<pk>/`. The user wants them consolidated into
the client/site view so a page list is visible without navigating away.

Design it properly rather than transplanting the table: the client page already carries
settings, custom domains, members, blog and versions. Decide what the primary object of
that page is and make pages a first-class part of it. Keep the existing routes working —
deep links to `sites/<pk>/pages/` must not 404.

### Phase 5 — Verification

- `python manage.py test` — **the full suite must pass.** It was ~614 tests at last
  count. Report the actual number and any test you changed, with why.
- Screenshots at 375 / 768 / 1280 before and after, for the editor, the client page, and
  the sites list. Put them in `docs/ui-audit/`.
- Deploy to staging and confirm it works there, not just locally.
- Keyboard-only pass over the editor and the client page: every control reachable,
  focus always visible, no trap.
- Contrast check against WCAG AA on the new palette usage.

## 8. Reporting

Announce state changes so the orchestrator and the human can see them without reading
your pane:

```bash
/home/bernardjr/.claude/skills/orchestrating-herdr-agents/scripts/herdr-say.sh \
  --state blocked --task CMS-UI --why "<one concrete sentence>"
```

Use `--state blocked` when you genuinely need a human decision, and `--state done` when
the work is finished and pushed. Commit in logical units with real messages — explain
why, not what; the diff already says what.

**Stop and report rather than guessing if:** the production Dockerfile can't be made to
build, Preflight can't be scoped off client content without breaking the rich-text
editor, the test suite fails for a reason you did not introduce, or a product constraint
in §4 stands between you and something the user asked for.

Do not open a pull request. The human merges.
