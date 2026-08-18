# Sticky Source Save Action Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move source-editor save actions into a responsive sticky page header and add keyboard save plus an honest unsaved-source signal.

**Architecture:** Keep each form in its existing card and associate the external header submit through `form=`. Scope layout and behavior with source-editor-specific classes/data attributes so global dashboard headers and CodeMirror's keymap remain unchanged.

**Tech Stack:** Django templates and tests, vanilla JavaScript, CodeMirror 6, Tailwind CSS build entry, Chrome DevTools Protocol capture harness.

---

### Task 1: Lock the render contract with failing tests

**Files:**
- Modify: `core/tests/test_page_html_editing.py`
- Modify: `core/tests/test_html_save_guards.py`

1. Replace the old sticky-bottom assertions with checks that each source form has a stable `id` and behavior marker.
2. Require the primary submit to live in `.source-page-actions`, reference the form through `form=`, and expose the unsaved indicator hook.
3. Require Cancel in both headers and assert `.source-form-actions` is absent from both pages.
4. Run `python manage.py test core.tests.test_page_html_editing.PageEditHtmlTests core.tests.test_html_save_guards.BlankHtmlRejectedTest`; expect the new contract tests to fail before implementation.

### Task 2: Move the actions and implement source-form behavior

**Files:**
- Modify: `templates/dashboard/page_edit_html.html`
- Modify: `templates/dashboard/template_form.html`
- Modify: `frontend/dashboard.js`

1. Give each source form a stable `id` and `data-source-edit-form` marker.
2. Render title, subtitle, dirty status, Cancel, and the associated submit in a scoped source page header; remove both bottom action rows.
3. Initialize the CodeMirror-backed textarea baseline, update the dirty status on `input`, and clear it on form submit.
4. Capture unmodified Cmd/Ctrl+S and call `requestSubmit()` only when a marked source form exists.
5. Run the focused render tests; expect green.

### Task 3: Apply the responsive sticky layout

**Files:**
- Modify: `frontend/dashboard.css`

1. Remove `.source-form-actions` and make `.annotate-status` normal flow.
2. Add a scoped desktop sticky header using the canvas background and one bottom border.
3. Add intrinsic title/action wrapping for intermediate widths.
4. Below 820px, keep the title/action row sticky at `top: 60px`, let the subtitle scroll, maintain z-index below app chrome, and set 44px mobile action targets.
5. Run the focused tests and build committed assets with `npm run build`.

### Task 4: Extend the bounded browser audit

**Files:**
- Modify: `scripts/capture-ui.mjs`

1. Add a source-action audit that inserts a large document through the CodeMirror instance and confirms the dirty status appears.
2. Intercept form submission without writing, then verify Cmd/Ctrl+S, header button click, and template-form Enter submission; confirm the status clears on each submit.
3. Scroll into the large document and report header/action bounds relative to the viewport.
4. Keep screenshot output compatible with the existing harness.

### Task 5: Run local quality gates

**Files:**
- Verify all changed source, template, test, harness, and generated asset files.

1. Run the focused tests.
2. Run the Impeccable detector once over the changed UI targets and resolve only in-scope findings.
3. Run `python manage.py test`; expect the full suite to pass.
4. Run `npm run check:assets`; expect no generated-asset diff.
5. Review `git diff --check`, the complete diff, and repository status for unrelated changes or secrets.

### Task 6: Review, merge, and verify staging

1. Commit the implementation, push `fix/cms-save-action-top-bar`, open a PR to `main`, and wait for required checks.
2. Merge the approved PR and record the PR number and merge SHA.
3. Confirm the `main`-tracking staging deployment reaches the merge SHA and `/healthz` returns 200.
4. Run one batch of four harness captures: page source and template source at 1280px and 390px, each deeply scrolled with the source-action audit.
5. Inspect all four screenshots and audit output together. Apply one consolidated fix batch only if needed, then run one confirmation batch and stop.

### Task 7: Deploy and verify production

1. Record the currently running production revision and exact rollback action.
2. Deploy merged `main` without environment or data changes.
3. Confirm production `/healthz`, the running merge SHA, and one existing public client page via a read-only request.
4. Report design decisions, screenshot paths, test/gate results, PR number, merge SHA, staging and production confirmations, rollback line, and deliberate omissions.
