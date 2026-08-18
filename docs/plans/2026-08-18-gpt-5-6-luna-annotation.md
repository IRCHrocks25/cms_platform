# GPT-5.6 Luna Annotation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make GPT-5.6 Luna the compatible default annotator, expose usage metadata, deploy it to staging, and prove annotate-to-save field parity.

**Architecture:** A small request-options helper selects only the live-verified model-specific output budget and Luna reasoning parameter while retaining one Chat Completions call path. Chunk responses carry optional usage metadata that is summed into the existing annotation result and job JSON. The browser harness drives a realistic raw sample through the deployed UI and verifies the saved parser schema.

**Tech Stack:** Python 3.12, Django 5.1, OpenAI Python SDK Chat Completions, unittest/Django TestCase, Node.js Chrome DevTools Protocol harness, Dokploy API.

---

### Task 1: Lock the outgoing request contract with failing tests

**Files:**
- Modify: `core/tests/test_annotator_parallel.py`
- Modify: `core/tests/test_background_annotate.py`

**Step 1: Write the failing request tests**

Capture kwargs passed to the mocked completion client. Assert Luna sends `max_completion_tokens=65536`, `reasoning_effort="low"`, JSON response format, no `max_tokens`, and no `temperature`. Add legacy-model assertions for the verified 16,384 and 32,768 caps without reasoning effort.

**Step 2: Write the failing usage propagation tests**

Add SDK-like usage metadata to fake completions and assert chunk totals reach `AnnotationResult`, the persisted `AnnotationJob.sections` JSON, and the status response.

**Step 3: Run the focused tests and confirm failure**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_annotator_parallel core.tests.test_background_annotate
```

Expected: failures showing the old `max_tokens` and `temperature` kwargs and missing usage fields.

### Task 2: Implement model-aware request options and usage totals

**Files:**
- Modify: `core/services/annotator.py`
- Modify: `dashboard/views.py`
- Modify: `cms_platform/settings.py`

**Step 1: Add the request-options helper**

Return `max_completion_tokens` from the configured override or the verified model-family default. Add `reasoning_effort="low"` only for `gpt-5.6-luna`. Keep `response_format={"type": "json_object"}` in the shared request.

**Step 2: Collect usage**

Attach optional usage metadata to parsed chunk results, sum it after all chunks complete, and add default-zero fields to `AnnotationResult`.

**Step 3: Persist and return usage**

Store model and token counts in `AnnotationJob.sections`; return them from the status endpoint alongside reconciliation counters.

**Step 4: Run focused tests**

Run the Task 1 command. Expected: all tests pass.

**Step 5: Commit**

Commit message: `feat: switch annotation to gpt-5.6-luna`.

### Task 3: Update operator configuration and documentation

**Files:**
- Modify: `.env.example`
- Modify: `CLAUDE.md`
- Modify: `cms_platform/settings.py`

**Step 1: Update defaults**

Set the default model to `gpt-5.6-luna`. Document the optional completion-budget override and retain the deliberate 500,000-character and 40,000-character limits.

**Step 2: Update the annotation pipeline note**

Document the Luna default, low reasoning effort, `max_completion_tokens`, model override caps, and usage reporting.

**Step 3: Run settings and annotator tests**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_annotator_limits core.tests.test_annotator_parallel core.tests.test_background_annotate
```

Expected: all tests pass.

### Task 4: Finalize the browser audit harness

**Files:**
- Modify: `scripts/capture-ui.mjs`

**Step 1: Preserve navigation readiness fixes**

Keep bounded DOM and URL checks for login, target-page loading, and tenant preview readiness.

**Step 2: Use a realistic raw input and capture usage**

Load `samples/restaurant.html`, remove CMS annotation attributes before submitting it, and retain the original schema counts as a quality baseline. Capture model, token usage, reconciliation counters, generated marker counts, saved schema counts, and the created job id without printing secrets.

**Step 3: Add cleanup support**

Emit the created template URL and job id so the authorized cleanup can target only those records.

**Step 4: Verify syntax and assets**

Run:

```bash
node --check scripts/capture-ui.mjs
npm run check:assets
```

Expected: both pass and no built assets change.

### Task 5: Full verification and Git delivery

**Files:**
- Verify all changed files

**Step 1: Run the full suite**

Run:

```bash
.venv/bin/python manage.py test
npm run check:assets
```

Expected baseline: at least 919 tests, all passing, and clean generated assets.

**Step 2: Commit the completed harness**

Amend or squash the local harness work into the feature branch without losing commit `39fb53c` content.

**Step 3: Push, open, and merge the PR**

Push the branch, open one PR against `main`, confirm it is mergeable and green, then run:

```bash
gh pr merge --squash --delete-branch
```

### Task 6: Configure and verify staging

**Files:**
- No repository files

**Step 1: Update only `sites-staging`**

Read `OPEANAI_API_KEY_STAGING` from the operator stash in memory. Set it as `OPENAI_API_KEY` and set `OPENAI_ANNOTATE_MODEL=gpt-5.6-luna` on the staging service without printing either value.

**Step 2: Redeploy only staging**

Trigger one `sites-staging` deployment. Wait for completion, verify it references the merge commit, and confirm `/healthz` returns 200.

### Task 7: Run parity and clean up

**Files:**
- No repository files

**Step 1: Run the real browser audit**

Authenticate as staging admin, annotate the realistic raw sample, record wall-clock, model, usage, reconciliation counts, and output counts, then apply and save.

**Step 2: Verify saved parity**

Assert every saved `data-edit` marker appears in the schema and detected section/field counts match the compare result.

**Step 3: Compare output quality**

Run the same raw input locally through `gpt-4o-mini` with the compatible request path and compare section and field coverage to Luna and the original sample schema.

**Step 4: Clean up exact records**

Delete the temporary template through the dashboard. Delete the test AnnotationJob by its recorded UUID using authorized staging management access. Verify neither remains.

**Step 5: Report**

Report compatibility checks, staging-only environment changes, PR and merge SHA, parity and quality evidence, cleanup, and the separate production rollout requirements. Never include secret values.
