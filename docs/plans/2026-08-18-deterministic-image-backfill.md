# Deterministic Image Backfill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Guarantee that genuine content images missed by the annotation model become editable while deterministic chrome and tracking-image exclusions remain uneditable.

**Architecture:** A new BeautifulSoup post-processing pass mirrors text backfill after model annotations are applied. It operates within nearest-section ownership, shares chrome detection, synthesizes collision-free image field IDs, and contributes to the existing aggregate backfill counter.

**Tech Stack:** Python, Django tests, BeautifulSoup, existing OpenAI annotation pipeline.

---

### Task 1: Specify image classification and annotation

**Files:**
- Modify: `core/tests/test_annotator_backfill.py`

**Step 1: Write failing content-image tests**

Test that an unmarked content image, including one with `alt=""`, receives `data-type="image"`, a unique section-owned ID, and a sensible label. Test an image inside `<picture>` and an existing model annotation.

**Step 2: Write failing exclusion tests**

Test nested-section ownership, nav/footer and shared chrome ancestors, presentation/hidden images, missing sources, explicit icon/logo tokens, and HTML/CSS dimensions at or below the tiny-image threshold.

**Step 3: Run the tests to verify failure**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_backfill`

Expected: FAIL because `_backfill_missed_image_fields` does not exist.

### Task 2: Implement the deterministic pass

**Files:**
- Modify: `core/services/annotator.py`

**Step 1: Extract shared ancestry checking**

Move the existing text-backfill ancestor loop into a small helper so image and text logic use the same rule.

**Step 2: Add explicit image exclusions**

Implement semantic chrome, presentation state, exact icon-like tokens, source presence, and numeric dimension checks. Do not exclude an image solely because its alt is empty.

**Step 3: Add collision-safe image annotations**

Use the nearest owning section, reserve existing field IDs, generate `image_N`, and label from alt or section metadata.

**Step 4: Invoke image backfill in the real path**

Run it after text backfill and sum both counts into `AnnotationResult.backfilled_fields`.

**Step 5: Run focused tests**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_backfill core.tests.test_annotator_parallel`

Expected: PASS.

### Task 3: Prove integration behavior

**Files:**
- Modify: `core/tests/test_annotator_parallel.py`

**Step 1: Add a model-skip integration test**

Mock the model returning a valid section and text field but no image field. Assert final HTML/schema includes the image and `backfilled_fields` increments.

**Step 2: Run the single test before implementation hookup**

Expected: FAIL because the final image remains unmarked.

**Step 3: Run focused suites after hookup**

Expected: PASS.

### Task 4: Remeasure all model configurations

**Files:**
- Create temporarily: `/tmp/cms_phase6_backfill_benchmark.py`

**Step 1: Use the exact Phase 5 input transformation**

Call `annotate_html_result()` for `gpt-4o-mini` and Luna low, medium, and high. Emit aggregate counts and usage only.

**Step 2: Compare before and after**

Record total schema fields, non-Brand fields, image fields, and backfilled count for each configuration. Confirm whether every saved marker remains represented in schema.

### Task 5: Verify and prepare the branch

**Files:**
- Verify all changed files

**Step 1: Run full verification**

Run `.venv/bin/python manage.py test`, `npm run check:assets`, and `git diff --check`.

**Step 2: Commit and push without merging**

Commit the design, tests, and prototype to the existing PR #38 branch. Do not merge, deploy, or change any environment.

**Step 3: Report and stop**

Report the exact diff, classification, proven mechanism, before/after table, revised recommendation, and explicit no-merge/no-deploy status.

