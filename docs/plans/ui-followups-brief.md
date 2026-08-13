# Brief: three UI fixes found in review, plus a demo-data seeder

Branch `feat/cms-ui-followups`, cut from `main` at `11f207f`. The UI overhaul is already
merged (PR #30) and live in production — this is follow-up work found by the owner reviewing
the shipped result.

**Read `CLAUDE.md` first**, then `docs/UI_AUDIT.md` and `DESIGN.md`. The design system, tokens,
and component layer already exist; use them. Do not introduce a fourth styling approach, and
do not add dependencies beyond what `package.json` already carries (Tailwind, Basecoat,
Alpine CSP, CodeMirror, esbuild).

Everything in `docs/plans/ui-overhaul-brief.md` still binds: the product constraints, the
production hazards, and the fact that the `Dockerfile` is shared with production.

Four tasks. Commit each separately.

---

## 1. The GHL sub-account select overflows its table cell

`frontend/dashboard.css:745`:

```css
.integration-select { width: auto; min-width: 160px; }
```

No `max-width` and no truncation. These selects live inside table cells
(`templates/dashboard/integrations.html:102` and `:223`), and `width: auto` sizes a `<select>`
to its longest option — so one long client-site name pushes the cell, and the table, past the
viewport.

Fix the select so a long option cannot break the layout: bound the width, truncate the
displayed value, and make sure the full name is still discoverable (native option list,
`title`, or both). Confirm the containing table has a sane narrow-screen strategy — the audit
already flagged tables overflowing at 375px.

Reproduce it before and after with a genuinely long site name, and screenshot both at 375 and
1280.

## 2. Audit finding #9 was never implemented

`docs/UI_AUDIT.md:114` is your own finding:

> **Editor top bars expose too many equal-priority actions.** The site editor presents save
> state plus View, Pages, Blog, Team, History, Gallery, Settings, and Publish in one row. On
> smaller laptops this wraps or compresses, and users must scan nine competing choices before
> the primary publish action.
>
> **Recommendation:** Keep context, save status, preview mode, and publish visible; group
> secondary site tools in a labelled overflow/menu or contextual drawer.

`templates/dashboard/editor.html` currently has 20 button elements and zero `role="menu"`,
`aria-haspopup`, or popover markup, so nothing was built. Implement your own recommendation,
in both locations the finding names: `editor.html:33-57` and `blog_form.html:11-34`.

Requirements:

- **Stays visible:** site context, save status, preview-mode control, Publish.
- **Moves into the menu:** the secondary site tools. Give the trigger a real label, not a bare
  glyph — a three-dot button with an accessible name is fine, an unlabelled one is not.
- **Accessible, using the dialog/menu primitive you already built:** `aria-haspopup`,
  `aria-expanded`, keyboard open, arrow-key movement between items, Escape to close, focus
  returned to the trigger. A menu that only works with a mouse is a regression against
  Findings #2 and #3, which you did fix.
- Works at 375 / 768 / 1280 without wrapping.

## 3. The sidebar does not collapse on desktop

`templates/base.html:25` has only a mobile hamburger (`app-mobile-toggle`), which opens a
drawer. On desktop the sidebar is a fixed-width column with no way to reclaim the space — a
real cost in the editor, where horizontal room is the scarce resource.

Add a desktop collapse:

- Toggle between full and icon-only. Icon-only must keep accessible names (`aria-label` or
  visually-hidden text) and should surface the label on hover/focus.
- `aria-expanded` on the toggle, keyboard operable, visible focus.
- **Persist the choice** across navigations — this is a server-rendered app, so every click is
  a full page load and a non-persisted toggle is useless. `localStorage` read early enough to
  avoid a flash of the wrong state.
- **Do not break the mobile drawer.** The existing behaviour at `base.html:25` and the script
  at `:149-161` must keep working; the two mechanisms should not fight at the breakpoint.

## 4. A demo-data seeder so staging looks populated

Staging's database is nearly empty, which makes the UI impossible to judge — lists, tables,
pagination, and truncation all look fine with three rows. The owner explicitly wants **dummy
data, not a production copy**.

Build a management command, `python manage.py seed_demo_data`:

- Creates a realistic spread: several templates, several client sites, each with inner pages
  and blog posts, a few team members, and at least one site with enough pages to exercise the
  editor's `dense` layout mode (16+ sections) and any pagination.
- Include the awkward cases the UI has to survive: a very long site name (this is what breaks
  task 1), a long page title, a site with zero pages, a site with zero blog posts, an unpublished
  page, and a site whose content leaves optional fields empty so `merge_with_defaults()` fallback
  is visible.
- **Idempotent.** Re-running must not duplicate. Namespace what it creates so it is obviously
  demo data and removable — add a `--clear` flag that removes only what the seeder made.
- **It must refuse to run against production.** Guard on an explicit opt-in environment
  variable that only staging sets (e.g. `ALLOW_DEMO_SEED=1`); absent it, exit non-zero with a
  clear message. Do not guard on `DEBUG` alone — production runs `DJANGO_DEBUG=0` but so does
  staging, so that check would not distinguish them. Add the variable to
  `docker-compose.staging.yml` only, never to `docker-compose.yml`.
- Passwords for any demo users follow the existing rules: generated, never persisted in
  plaintext, never in logs or URLs.
- Tests covering: the guard refuses without the env var, the command is idempotent, and
  `--clear` removes exactly what it created.
- Document it in `deploy/STAGING.md` under first deploy.

---

## Staging

Staging has been repointed from `main` to **this branch** with autoDeploy on, so every push
lands at `https://staging.sites.katek.app` within a couple of minutes. It goes back to `main`
after this merges. Admin credentials are in Passbolt (`infra-devops` folder, resource
`84c2b7ec-303d-42d5-ab57-5d86b9c73eb6`) and in the Dokploy env var on `sites-staging`.

Run the seeder against staging once it exists, and screenshot the populated result.

## Verification

- `python manage.py test` must pass. It was 799 before your changes; report the new number and
  any test you changed with the reason.
- `npm run check:assets` must pass.
- `docker compose -f docker-compose.yml build` must still succeed — the `Dockerfile` is shared
  with production, which is now live on this code.
- Screenshots at 375 / 768 / 1280 into `docs/ui-audit/` for: the integrations table with a long
  name, the editor top bar with the menu open and closed, the sidebar collapsed and expanded,
  and one populated list view after seeding.
- Keyboard-only pass over the new menu and the sidebar toggle.

## Reporting

```bash
/home/bernardjr/.claude/skills/orchestrating-herdr-agents/scripts/herdr-say.sh \
  --state done --task CMS-UI-2 --why "<one concrete sentence>"
```

Use `--state blocked` if a product constraint blocks a task, if the production image stops
building, or if the suite fails for a reason you did not introduce. Do not open a pull request.
