# Relaxed Image Annotation Policy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Annotate empty-alt content photographs without marking decorative chrome, run Luna at medium effort, and prove the result locally and on staging.

**Architecture:** The LLM prompt performs contextual image classification and the existing deterministic backfill supplies idempotent coverage for missed content images. Configuration defaults to Luna medium, while the staging harness records image-field parity through save.

**Tech Stack:** Django 5.1, Beautiful Soup, OpenAI Chat Completions, Node/Chromium CDP, Dokploy.

---

### Task 1: Lock the image-policy contract with failing tests

**Files:**
- Modify: `core/tests/test_annotator_backfill.py`
- Modify: `core/tests/test_annotator_parallel.py`

1. Add a backfill test asserting a model-marked image keeps its one existing
   `data-edit` id and adds no field.
2. Change the Luna default-effort expectation from `low` to `medium`.
3. Add a prompt-capture assertion that empty `alt` is contextual rather than an
   automatic exclusion and that explicit decorative signals remain exclusions.
4. Run the focused tests and confirm they fail for the old prompt/default.

### Task 2: Implement the shared prompt policy and medium default

**Files:**
- Modify: `core/services/annotator.py`
- Modify: `cms_platform/settings.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `CLAUDE.md`

1. Replace the blanket empty-alt skip with concise contextual guidance and note
   that prompt and backfill are two halves of one policy.
2. Keep the deterministic backfill logic unchanged.
3. Set every documented/runtime fallback for
   `OPENAI_ANNOTATE_REASONING_EFFORT` to `medium`.
4. Run the focused annotation tests and confirm they pass.

### Task 3: Extend staging evidence to image parity

**Files:**
- Modify: `scripts/capture-ui.mjs`

1. Count baseline, annotated-output, and saved `data-type="image"` markers.
2. Fail the real-save audit unless all three image counts agree and marker ids
   remain unique.
3. Run `node --check scripts/capture-ui.mjs` and confirm a clean generated-asset
   diff.

### Task 4: Benchmark the approved configuration

**Files:**
- No tracked source changes required; save sanitized results under `/tmp`.

1. Run the restaurant sample twice each with GPT-4o mini and Luna low, medium,
   and high under the relaxed prompt with backfill enabled.
2. Record final/schema field coverage, image fields, unexpected image fields,
   backfill count, prompt/completion/reasoning/total tokens, and latency.
3. Calculate cost from current official input/output token prices without
   double-counting reasoning tokens.
4. Run a mixed content/decorative probe and list any non-content image the model
   or final pipeline marks.

### Task 5: Ship and verify Phase 7

**Files:**
- All files above.

1. Run `.venv/bin/python manage.py test` and `npm run check:assets`.
2. Commit, push, open a PR, verify it, and squash-merge it to `main`.
3. Confirm staging auto-deploys the merge commit and `/healthz` returns 200.
4. Set staging model to `gpt-5.6-luna` and effort to `medium`, changing no other
   environment entry, then deploy and wait for success.
5. Run the real-save browser audit; prove two editable image fields and full
   marker/schema parity; delete the temporary template and job.

### Task 6: Roll out the verified release to production

**Files:**
- No tracked source changes.

1. Record the currently deployed production commit, image tag, and exact
   rollback action before writing anything.
2. Set only `OPENAI_ANNOTATE_MODEL=gpt-5.6-luna` and
   `OPENAI_ANNOTATE_REASONING_EFFORT=medium`; preserve the existing API key and
   every other setting.
3. Explicitly deploy the Phase 7 merge commit and wait for success.
4. Verify `/healthz`, one existing public client site, and one annotation job
   without saving or mutating client-visible content.
5. Roll back immediately if any production verification is red.
