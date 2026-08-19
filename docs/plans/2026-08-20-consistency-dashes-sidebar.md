# Phase 13 Consistency, Copy, and Sidebar Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align the page-list creation actions, remove product-authored em dashes with a durable guard, and move the staff-only sidebar create action beneath the brand.

**Architecture:** Keep the existing server-rendered Django templates and shared dashboard tokens. Use externally associated card-header actions instead of restructuring or adding sticky UI, move the agency CTA outside primary navigation without changing its permission condition, and enforce copy policy with a source-scanning Django test whose exceptions identify exact external fixture or historical migration payload lines.

**Tech Stack:** Django 5.1 templates and tests, vanilla JavaScript, Tailwind CLI over the incumbent CSS source, esbuild, Chromium DevTools capture harness, Dokploy.

---

### Task 1: Add failing layout and copy-policy contracts

**Files:**
- Modify: `core/tests/test_page_html_editing.py`
- Modify: `core/tests/test_agency_admin.py`
- Create: `core/tests/test_product_copy_policy.py`

**Step 1: Replace the old page-create sticky-row assertion**

Assert that the rendered agency page list contains a stable creation form id, an `Add page` button associated through `form=`, a scoped nonsticky card action row, and no `source-form-actions` class. Assert that `Find and import pages` occupies the matching sibling-card heading row.

**Step 2: Add sidebar order and audience assertions**

For an authenticated staff request, assert that the `sidebar-cta` appears after `sidebar-brand` and before `app-sidebar-nav`, remains outside the `<nav>`, and keeps `data-sidebar-label="New client"`. Preserve or add a tenant-member assertion that the CTA is absent.

**Step 3: Add the em-dash guard**

Scan text files under `dashboard/`, `core/`, and `templates/`. Include Python, HTML, and text/email sources; exclude caches and generated binary artifacts. Record allowed exact lines in a mapping for:

- External-input test fixtures that must preserve the character.
- The historical choice-label payload in `core/migrations/0021_template_ownership_and_versions.py`.

Do not exempt whole files or directories. Report each unexplained path and line.

**Step 4: Run the focused tests and verify red**

Run:

```bash
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py test core.tests.test_page_html_editing core.tests.test_agency_admin core.tests.test_product_copy_policy
```

Expected: the new layout assertions fail against the old markup, and the copy guard reports the current product-owned em-dash occurrences.

### Task 2: Align the two page-list creation cards

**Files:**
- Modify: `templates/dashboard/page_list.html`
- Modify: `frontend/dashboard.css`
- Modify: `static/css/dashboard.css`
- Test: `core/tests/test_page_html_editing.py`

**Step 1: Load the Impeccable craft floor**

Read `reference/craft-floor.md` immediately before editing the UI and apply only its scoped requirements.

**Step 2: Associate the page-create action**

Give the form a stable id. Add a scoped card-heading row before it with `New page` on the left and the submit button on the right using `form=`. Remove the bottom `.source-form-actions` wrapper.

**Step 3: Align the sibling-import card**

Move the unchanged JavaScript-driven import button into an equivalent heading row beside `Import sibling pages from a URL`. Leave explanatory copy, URL input, asynchronous status, and event wiring in document order below it.

**Step 4: Add scoped responsive layout**

Use existing spacing tokens and a wrapping flex row. Keep desktop titles/actions on one line when space permits, preserve 44px mobile action targets, and avoid sticky positioning, new colors, radii, or elevation.

**Step 5: Run the page-list tests**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_page_html_editing core.tests.test_page_import_siblings
```

Expected: PASS.

### Task 3: Move the staff-only sidebar action

**Files:**
- Modify: `templates/base.html`
- Modify: `frontend/dashboard.css`
- Modify: `static/css/dashboard.css`
- Test: `core/tests/test_agency_admin.py`

**Step 1: Move without widening visibility**

Render the unchanged `New client` link immediately after the sidebar brand only when `request.tenant` is absent. Keep it outside `<nav>`, before the collapse control, and remove the old copy from the agency nav branch.

**Step 2: Replace positional margin with top-action rhythm**

Keep the existing blue CTA treatment and collapsed tooltip selectors. Scope spacing to its new role so expanded desktop, collapsed desktop, and the mobile drawer retain usable geometry.

**Step 3: Run sidebar-focused tests**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_agency_admin core.tests.test_tenant_dashboard
```

Expected: PASS.

### Task 4: Remove product-authored em dashes

**Files:**
- Modify: user-facing and authored source matched under `templates/`, `dashboard/`, and `core/`
- Modify: tests that assert changed copy under `core/tests/`
- Test: `core/tests/test_product_copy_policy.py`

**Step 1: Rewrite templates and email text**

Review every template occurrence in context. Use commas for continuations, periods between independent clauses, colons before explanations, and parentheses for genuine asides. Treat `templates/legal/privacy.html` and `templates/legal/terms.html` as punctuation-only edits. Replace em-dash-only missing-value glyphs with an existing plain textual fallback such as `Not provided` or `None` without changing surrounding behavior.

**Step 2: Rewrite dashboard and core Python strings**

Review flash messages, errors, prompts, logger messages, command output, choices, titles, and user-visible helpers individually. Update copy assertions to the same punctuation. Preserve meaning and tone.

**Step 3: Rewrite comments and docstrings mechanically**

Use sentence-appropriate punctuation without changing code, examples, aligned ASCII diagrams, or historical migration payload values.

**Step 4: Record exact approved exceptions**

Keep external-input fixture lines and migration data payloads unchanged. Add each retained line explicitly to the guard mapping with a reason in a neighboring comment.

**Step 5: Run the copy guard and affected tests**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_product_copy_policy
.venv/bin/python manage.py test core.tests.test_host_scoped_legal_pages core.tests.test_integrations_views core.tests.test_ghl_oauth_locations
```

Expected: PASS with zero unexplained occurrences.

### Task 5: Rebuild and run local release gates

**Files:**
- Modify: `scripts/capture-ui.mjs` only if the existing harness needs new read-only sidebar/card audit selectors
- Modify: generated assets under `static/css/` or `static/js/` when their frontend sources changed

**Step 1: Extend the existing capture harness minimally**

Add a Phase 13 audit mode only if necessary to validate card action association, CTA DOM order, collapsed tooltip/icon state, mobile-drawer visibility, and unchanged source-editor headers without submitting server forms.

**Step 2: Rebuild committed assets**

Run:

```bash
npm run build
```

**Step 3: Run syntax and detector checks once**

Run:

```bash
node --check scripts/capture-ui.mjs
node /home/bernardjr/.agents/skills/impeccable/scripts/detect.mjs --json templates/dashboard/page_list.html templates/base.html frontend/dashboard.css
```

Expected: clean syntax and no unexplained new detector findings.

**Step 4: Run the complete automated gate**

Run:

```bash
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py test
npm run check:assets
git diff --check
```

Expected: full suite PASS, deterministic generated assets, and no whitespace errors.

### Task 6: Merge and verify staging in bounded rounds

**Files:**
- No source changes unless the first batched browser round exposes a defect

**Step 1: Commit, push, and open the PR**

Commit the reviewed implementation, push `fix/cms-consistency-dash-sidebar`, open a PR against `main`, verify the PR diff and checks, then merge as explicitly authorized.

**Step 2: Confirm staging autodeploy**

Verify Dokploy reports the merge revision and `https://staging.sites.katek.app/healthz` returns 200.

**Step 3: Run one batched browser round**

Capture together:

- Page list card at 1280px and 390px.
- Agency sidebar expanded and collapsed.
- Agency mobile drawer open.
- Existing page-source and template-source editor headers as unchanged regressions.

Audit action association, CTA order and visibility, collapsed tooltip behavior, mobile touch access, DOM/focus agreement, and absence of a bottom action overlay. Do not allow capture submissions to reach the server.

**Step 4: Apply at most one consolidated correction batch**

If needed, fix all observed defects together, repeat focused/full gates as risk requires, merge a follow-up, and run one confirmation batch. Otherwise stop after the first round.

### Task 7: Deploy production and report

**Files:**
- No source changes

**Step 1: Record the rollback baseline**

Before deployment, confirm production is running `ca3c3c767499b22b1123f76199ea8708731a9414` and record its Dokploy deployment id. Rollback means restoring that exact Git ref for the production `sites` compose and redeploying it.

**Step 2: Deploy the merged main revision**

Trigger the production `sites` compose deployment and wait for Dokploy to report success at the new merge SHA.

**Step 3: Verify production read-only**

Confirm `/healthz`, confirm new hashed assets are referenced, and render one existing client site with GET-only checks. Perform no production content writes.

**Step 4: Report**

Include all three changes, before/after dash counts, every retained exception, guard behavior, screenshots, focused/full gates, PR and merge SHA, staging and production deployment ids, correct rollback baseline, and deliberate exclusions.
