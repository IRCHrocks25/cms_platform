# CMS Annotation Field Integrity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent silent field loss, surface annotation failures immediately, and keep large HTML editing bounded and saveable from both agency HTML screens.

**Architecture:** Reconcile model fields against nearest-section ownership before backfill, enforce the same ownership in schema parsing, and validate final HTML-to-schema parity. Carry structured reconciliation statistics through the annotation job, harden the compare overlay and save feedback, then bound CodeMirror and keep form actions sticky.

**Tech Stack:** Python 3.12, Django 5.1, BeautifulSoup, Django TestCase, vanilla JavaScript, CodeMirror 6, Tailwind CSS tooling, headless Chromium.

---

### Task 1: Establish the test environment and baseline

**Files:**
- Verify: `CLAUDE.md`
- Verify: `requirements.txt`
- Verify: `package.json`

**Step 1: Restore the Python 3.12 environment**

Locate the repository's shared Python 3.12 virtual environment or create the
missing `.venv` from pinned requirements without changing dependency files.

**Step 2: Collect static files once**

Run: `.venv/bin/python manage.py collectstatic --noinput`

Expected: exit 0 with the static manifest available to template tests.

**Step 3: Run the unmodified baseline**

Run: `.venv/bin/python manage.py test`

Expected: 821 tests pass. Record the actual count and any real baseline
failure before changing code.

### Task 2: Define reconciliation and nested ownership with failing tests

**Files:**
- Modify: `core/tests/test_annotator_parallel.py`
- Modify: `core/tests/test_annotator_backfill.py`
- Modify: `core/tests/test_parser.py`
- Modify: `core/services/annotator.py`
- Modify: `core/parser.py`

**Step 1: Write failing reconciliation tests**

Test that a mismatched prefix is rewritten to the nearest section and that an
orphan loses `data-edit`, `data-type`, and `data-label`. Assert exact rewritten
and dropped counts.

**Step 2: Write failing parity and nested-section tests**

Assert output `data-edit` count equals non-brand schema field count. Prove a
nested field belongs only to its nearest section and is never counted or
backfilled by an outer section.

**Step 3: Run tests to verify red**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_parallel core.tests.test_annotator_backfill core.tests.test_parser -v 2`

Expected: new tests fail because reconciliation is absent and recursive scans
include nested-section descendants.

**Step 4: Implement minimal ownership logic**

Add reconciliation after `_apply_annotations` and before backfill. Use nearest
section checks in backfill and `build_schema`. Validate parity after block and
data URI restoration, raising `AnnotatorError` with both counts on divergence.

**Step 5: Run tests to verify green**

Run the command from Step 3. Expected: PASS.

**Step 6: Commit**

Run: `git commit -m "fix: reconcile annotated fields with sections"`

### Task 3: Carry integrity statistics through the job

**Files:**
- Modify: `core/services/annotator.py`
- Modify: `dashboard/views.py`
- Modify: `core/tests/test_background_annotate.py`

**Step 1: Write the failing worker payload test**

Mock a structured annotation result and assert `_run_annotation_job` persists
HTML, sections, rewritten count, dropped count, and backfilled count. Retain
coverage that existing background import callers receive a string.

**Step 2: Run test to verify red**

Run: `.venv/bin/python manage.py test core.tests.test_background_annotate -v 2`

Expected: FAIL because the worker summary contains only sections.

**Step 3: Implement the structured result path**

Keep `annotate_html(raw_html)` returning HTML. Add a structured result entry
point for the job worker, persist its counts with the section summary, and
return that payload unchanged from the status endpoint.

**Step 4: Run test to verify green**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

Run: `git commit -m "feat: report annotation reconciliation counts"`

### Task 4: Harden polling and zero-section states with failing tests

**Files:**
- Modify: `core/tests/test_background_annotate.py`
- Modify: `templates/dashboard/_html_source_editor.html`
- Modify: `frontend/dashboard.css`

**Step 1: Write failing response and UI tests**

Render both screens and require terminal handling for non-2xx responses and
error-only JSON. Require explicit overlay error controls and zero-section
warning copy. Assert the status endpoint preserves stored error text and the
missing-job message.

**Step 2: Run tests to verify red**

Run: `.venv/bin/python manage.py test core.tests.test_background_annotate core.tests.test_html_save_guards core.tests.test_page_html_editing -v 2`

Expected: new assertions fail because polling ignores `r.ok` and zero sections
use success handling.

**Step 3: Implement the overlay states**

Fail immediately on known HTTP or JSON errors while preserving server text.
Keep the overlay open with Apply disabled, Retry, and Close. Render zero
sections as an explicit warning with an honest apply label. Make inline status
sticky inside the source editor area.

**Step 4: Run tests to verify green**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

Run: `git commit -m "fix: surface annotation failures immediately"`

### Task 5: Warn after save about ignored submitted markers

**Files:**
- Modify: `core/services/templates.py`
- Modify: `dashboard/views.py`
- Modify: `core/tests/test_html_save_guards.py`
- Modify: `core/tests/test_page_html_editing.py`

**Step 1: Write failing save-honesty tests**

Submit orphan and mismatched markers, follow the successful redirect, and
assert the destination identifies every marker absent from the derived schema.
Cover template create, template detail, and page HTML save.

**Step 2: Run tests to verify red**

Run: `.venv/bin/python manage.py test core.tests.test_html_save_guards core.tests.test_page_html_editing -v 2`

Expected: FAIL because successful saves do not compare submitted markers with
the schema.

**Step 3: Implement occurrence-aware reporting**

Add a focused helper that parses submitted marker occurrences with `lxml`,
subtracts schema field occurrences, and returns ignored identifiers. Add a
warning after successful saves and before redirects.

**Step 4: Run tests to verify green**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

Run: `git commit -m "fix: report ignored fields after HTML save"`

### Task 6: Bound CodeMirror and keep actions reachable

**Files:**
- Modify: `frontend/code-editor.js`
- Modify: `frontend/dashboard.css`
- Modify: `templates/dashboard/template_form.html`
- Modify: `templates/dashboard/page_edit_html.html`
- Modify: `static/css/dashboard.css`
- Modify: `static/js/dashboard.js`
- Modify: `static/js/code-editor.js`

**Step 1: Record the before measurement**

Use `scripts/capture-ui.mjs` or a committed extension only if needed. Paste a
large real HTML document into each source screen and record file bytes,
viewport, document scroll height, editor client and scroll heights, and the
save-button rectangle.

**Step 2: Load the Impeccable craft floor**

Read its required craft-floor reference immediately before UI edits.

**Step 3: Implement bounded editor and sticky actions**

Set CodeMirror to `height: clamp(24rem, 60vh, 46rem)`, make `.cm-scroller`
scroll internally, and preserve all current extensions. Apply a shared sticky
action-row class to both forms with normal-flow spacing and responsive wrapping.

**Step 4: Rebuild and verify committed assets**

Run: `npm run build`

Run: `npm run check:assets`

Expected: both commands exit 0 and committed bundles match their sources.

**Step 5: Record the after measurement and inspect once**

Repeat Step 1 with the same file and viewport. Run the Impeccable detector once
over all changed frontend targets. Expected: document height no longer scales
with source length, editor overflow is internal, and save remains reachable.

**Step 6: Commit**

Run: `git commit -m "fix: bound HTML editor and pin save actions"`

### Task 7: Audit the full annotate-to-render flow

**Files:**
- Inspect: `dashboard/views.py`
- Inspect: `core/services/annotator.py`
- Inspect: `core/services/templates.py`
- Inspect: `core/models.py`
- Inspect: `core/parser.py`
- Inspect: `core/renderer.py`
- Inspect: `templates/dashboard/_html_source_editor.html`

**Step 1: Trace every hop**

Audit paste and URL fetch, job creation and worker failure, strip and restore,
parallel model calls and merge, apply and reconciliation, backfill and parity,
status serialization, polling and apply, all save paths, schema derivation,
editor field generation, and renderer substitution.

**Step 2: Record findings**

At each hop ask whether a field can disappear silently and whether failure can
be reported as success. Fix in-scope findings tests first. Record every
out-of-scope real risk with file, line, and one-line consequence.

**Step 3: Commit only in-scope audit fixes**

Use a focused commit if needed. Do not expand into unrelated pipeline,
chunking, parser, renderer, or import refactors.

### Task 8: Complete the local gate and open the pull request

**Files:**
- Verify: all changed files.

**Step 1: Run focused tests**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_parallel core.tests.test_annotator_backfill core.tests.test_parser core.tests.test_background_annotate core.tests.test_html_save_guards core.tests.test_page_html_editing core.tests.test_template_model -v 2`

Expected: PASS.

**Step 2: Run the full gate**

Run: `.venv/bin/python manage.py test`

Run: `npm run check:assets`

Run: `.venv/bin/python manage.py check`

Run: `git diff --check origin/main...HEAD`

Expected: every command exits 0. Record actual test counts and output.

**Step 3: Push and open the pull request**

Push the current branch and open a PR against `main`. Include root causes,
changes, test counts, asset check, measurements, and audit findings.

### Task 9: Merge and verify staging

**Files:**
- Inspect: `deploy/STAGING.md`
- Inspect: `docker-compose.staging.yml`
- Reuse or modify if necessary: `scripts/capture-ui.mjs`

**Step 1: Merge only after the gate is green**

Run: `gh pr merge --squash --delete-branch`

Expected: the pull request merges into `main`. Record PR number and merge SHA.

**Step 2: Confirm deployment identity**

Poll `https://staging.sites.katek.app/healthz` until healthy. Verify running
code matches the merge commit before drawing functional conclusions. Do not
touch production or staging isolation settings.

**Step 3: Run staging functional tests**

Record large-file height measurements, immediate missing-job error timing and
message, zero-section warning state, real annotation and saved schema parity,
and tenant editor field and preview iframe health.

**Step 4: Announce a credential block if necessary**

If the staging password cannot be obtained without guessing or unauthorized
account changes, run the exact `herdr-say.sh` command from the Phase 2 brief,
stop, and wait. Do not report staging as tested.

**Step 5: Report completion**

Report root causes, changes, test output, editor measurements, deliberate
non-work, audit findings, PR and merge identifiers, deployment confirmation,
and staging evidence in the order required by both briefs.
