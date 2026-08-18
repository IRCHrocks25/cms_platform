# Annotation Orphan Recovery and Starter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Recover structurally grouped orphan annotations, expose recovery counters, and ship a representative round-trip-safe starter through staging and production.

**Architecture:** Preserve semantic wrappers during chunk selection, then run a conservative orphan-recovery pass after model attributes are applied and before strict reconciliation. Propagate deterministic recovery counters through the existing background-job response and UI, and validate the starter through the normal annotation entry point with a mocked model.

**Tech Stack:** Python, Django, BeautifulSoup/lxml, unittest, vanilla JavaScript templates, OpenAI Luna corpus harness, GitHub CLI, Dokploy API.

---

### Task 1: Preserve semantic split boundaries and recover grouped orphan fields

**Files:**
- Modify: `core/services/annotator.py`
- Test: `core/tests/test_annotator_parallel.py`

1. Run the five Phase 10 regression tests and record three failures plus two passes.
2. Stop `_find_split_root` before descending into a sole `section` or `article`.
3. Add a pre-reconciliation helper that finds orphan fields, groups them under their lowest safe shared wrapper, promotes that wrapper, removes conflicting orphan section markers, and returns promoted/salvaged counts.
4. Call recovery after annotation application and before reconciliation; retain the strict removal path for ungroupable fields.
5. Add the counters to `AnnotationResult` and annotation logging.
6. Run the five regression tests and the adjacent annotator suite; expect green.

### Task 2: Surface recovery counters to the operator

**Files:**
- Modify: `dashboard/views.py`
- Modify: `templates/dashboard/_html_source_editor.html`
- Modify: `templates/dashboard/tenant_form.html`
- Test: `core/tests/test_background_annotate.py`

1. Extend worker/status tests to require `promoted_sections` and `salvaged_fields`.
2. Extend UI source assertions to require both counters.
3. Persist and unwrap both values in annotation-job responses.
4. Add concise promoted/salvaged messages to both compare overlays.
5. Run `core.tests.test_background_annotate`; expect green.

### Task 3: Replace and round-trip the starter

**Files:**
- Modify: `dashboard/views.py`
- Create: `core/tests/test_starter_template.py`

1. Write a mocked-model test that strips annotations from `STARTER_TEMPLATE_HTML`, calls `annotate_html_result`, and compares all non-brand field IDs/types with the original schema.
2. Replace the starter with a compact multi-section page demonstrating text, rich text, image, link, color, `data-group`, and `data-tokens`.
3. Run the new starter test and template/dashboard tests; expect green.
4. Inspect restaurant and seed-template top-level shapes; change only an obvious safe structural issue.

### Task 4: Run the full local corpus and release gates

**Files:**
- Retain: `scripts/run_annotation_corpus.py`

1. Collect/reuse the exact 120-document corpus without printing page HTML or secrets.
2. Run all documents locally with `gpt-5.6-luna` and reasoning effort `medium`.
3. Generate the after report and compare every row with the Phase 10 baseline; stop if any row is worse.
4. Record hard errors, orphan-dropping rows, reconciliation rows, backfill rows, schema parity, tokens, latency, and spend.
5. Run the full Django suite and `npm run check:assets`; expect green.

**Execution amendment:** The operator approved a targeted corpus instead of the
full 120-document run. It included all known failure/reconciliation/drop shapes,
representative size bands, the starter, and a control. All worse single-run rows
were repeated three times; each delta was no larger than the observed
default-temperature spread, clearing the release gate.

### Task 5: Review, merge, and verify staging

**Files:**
- Commit all approved source, test, plan, and corpus-harness changes.

1. Check the diff for accidental secrets and unrelated changes.
2. Push a feature branch, open a PR, verify CI, and merge to `main`.
3. Confirm staging autodeploys the merge commit and health checks pass.
4. Run real staging annotations for the exact failed sample, the new starter, and one previously passing template; confirm editable-field parity and visible recovery warnings where applicable.
5. Remove temporary staging templates and annotation jobs and verify cleanup.

### Task 6: Roll out and smoke production

1. Record the currently running production commit and an exact rollback action.
2. Deploy merged `main` without changing production environment variables.
3. Confirm production health, running commit, and a read-only existing public client page.
4. Retrieve the authorized Passbolt production superuser credential in memory, create one unsaved annotation job using the exact failed sample, and verify every expected field is editable.
5. Remove the transient annotation job and verify no template/site/page was persisted.
6. If any production check fails, execute the recorded rollback action and report the failure without a forward fix.
