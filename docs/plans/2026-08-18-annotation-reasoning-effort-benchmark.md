# Annotation Reasoning-Effort Benchmark Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Luna reasoning effort configurable, benchmark low/medium/high against `gpt-4o-mini` twice on one curated input, and prepare the cost-selected default without merging or deploying.

**Architecture:** Django settings owns the environment-backed effort value and Compose forwards it. The existing Luna-only request-options branch applies the value, while fresh local Django processes exercise the complete annotation pipeline for every benchmark trial.

**Tech Stack:** Django settings/tests, Python OpenAI SDK Chat Completions, BeautifulSoup schema inspection, Docker Compose env forwarding.

---

### Task 1: Specify configurable request behavior

**Files:**
- Modify: `core/tests/test_annotator_parallel.py`
- Test: `core/tests/test_annotator_parallel.py`

**Step 1: Write the failing tests**

Use `override_settings(OPENAI_ANNOTATE_REASONING_EFFORT="high")` around a Luna chunk call and assert `reasoning_effort == "high"`. Extend the non-reasoning model test under the same override and assert the option remains absent.

**Step 2: Run tests to verify failure**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_parallel.AnnotatorParallelTests.test_luna_request_uses_configured_reasoning_effort`

Expected: FAIL because Luna is still hardcoded to `low`.

### Task 2: Implement and document the setting

**Files:**
- Modify: `cms_platform/settings.py`
- Modify: `core/services/annotator.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `CLAUDE.md`

**Step 1: Add the setting with the deployed default**

Add:

```python
OPENAI_ANNOTATE_REASONING_EFFORT = os.environ.get(
    "OPENAI_ANNOTATE_REASONING_EFFORT", "low"
)
```

**Step 2: Apply it only to Luna**

Replace the hardcoded request value with `settings.OPENAI_ANNOTATE_REASONING_EFFORT` inside the existing `model == _LUNA_MODEL` branch.

**Step 3: Forward and document it**

Add the Compose environment entry and describe the valid Luna values in `.env.example` and `CLAUDE.md`.

**Step 4: Run focused tests**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_parallel core.tests.test_compose_env_passthrough`

Expected: PASS.

### Task 3: Run the live benchmark

**Files:**
- Create temporarily: `/tmp/cms_reasoning_benchmark.py`

**Step 1: Build the measurement runner**

Read and de-annotate `samples/restaurant.html` with the same attribute-removal expression as the staging harness. Call `annotate_html_result()`, then emit only model, effort, elapsed time, schema/field/image counts, integrity counters, and usage.

**Step 2: Run two trials of each configuration**

Launch a fresh process for `gpt-4o-mini`, then Luna at low, medium, and high. Export the local stash value as `OPENAI_API_KEY` only for the child process. Do not print the key.

**Step 3: Record all eight results**

Preserve the per-run JSON metrics in the working transcript. Do not commit benchmark outputs containing generated HTML.

### Task 4: Apply the operator's cost rule

**Files:**
- Modify if selected: `cms_platform/settings.py`
- Modify if selected: `docker-compose.yml`
- Modify if selected: `.env.example`
- Modify if selected: `CLAUDE.md`
- Modify: `core/tests/test_annotator_parallel.py`

**Step 1: Calculate per-run cost**

Use:

```text
cost = prompt_tokens / 1,000,000 * input_rate
     + completion_tokens / 1,000,000 * output_rate
```

Reasoning tokens are a reported subset of completion tokens and must not be charged twice.

**Step 2: Select the default**

If both Luna-high trials cost less than the `gpt-4o-mini` trials, change the default and test expectation to `high`; otherwise retain `low`. Report field and image coverage independently.

**Step 3: Re-run focused tests**

Run: `.venv/bin/python manage.py test core.tests.test_annotator_parallel core.tests.test_compose_env_passthrough`

Expected: PASS.

### Task 5: Verify and prepare the unmerged branch

**Files:**
- Verify all modified files

**Step 1: Run full verification**

Run: `.venv/bin/python manage.py test`

Expected: all tests PASS.

Run: `npm run check:assets`

Expected: PASS with no generated asset diff.

Run: `git diff --check`

Expected: no output.

**Step 2: Commit without merging**

Commit the configurable setting and selected default on the existing unmerged branch. Do not merge PR #38, deploy, or change any environment.

**Step 3: Report and stop**

Provide both runs for all configurations, official pricing links and read date, explicit cost arithmetic, quality comparison, recommendation, branch/commit status, and an explicit statement that nothing was merged or deployed.

