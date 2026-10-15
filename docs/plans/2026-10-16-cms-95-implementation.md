# CMS-95 Layout-Aware Conversion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert eligible classic pages without changing wrapper structure or interleaved fixed siblings, while refusing any render difference.

**Architecture:** Add a versioned layout descriptor to Template. The converter extracts root bands into ordered body slots, keeps wrapper and fixed markup in the shell, and marks same-parent runs with comment anchors. The renderer, editor, API, and restore path validate the same slot contract. Existing `{}` layouts keep legacy behavior.

**Tech Stack:** Django, BeautifulSoup/lxml, pytest, Plane, GitHub Actions.

---

### Task 1: Conversion contract and failing fixtures

**Files:** `core/tests/test_blocks.py`, `core/services/blocks.py`.

1. Add Lisa-shaped, interleaved direct-child, nested wrapper, and stripped-anchor fixtures asserting exact normalized classic/render equality, stable slot order, and refusal on mutation.
2. Run the focused tests and record the initial failures.
3. Add `split_shell_and_blocks_v2`, layout validation, multi-slot content mapping, and a strict v2 comparator. Keep legacy functions unchanged for `{}` layouts.
4. Re-run focused tests; commit.

### Task 2: Persistence and rendering

**Files:** `core/models.py`, `core/migrations/0031_template_block_layout.py`, `core/renderer.py`, `core/tests/test_blocks.py`.

1. Add tests for wrapper and comment-range rendering, missing/duplicate anchors, legacy rendering, and clone behavior. Run red.
2. Add `Template.block_layout` JSONField default `{}` and an additive migration, renderer support, and atomic conversion write. Run green.
3. Run `manage.py migrate --plan` against a disposable database; assert only `core.0031_template_block_layout` is pending. Commit.

### Task 3: Editor, MCP, restore and HTML processing

**Files:** `dashboard/views.py`, `api/mcp/structure.py`, `api/mcp/tools.py`, `core/services/content_versions.py`, relevant tests.

1. Add failing tests for v2 slot order/save validation, cross-slot refusal, connector get/add/remove/reorder/push behavior, old-version restore refusal, and anchor preservation through sanitizer, guard, and minifier.
2. Make all entry points accept declared v2 slots or return a clear error. Keep fixed spans locked. Require all anchor pairs before save/render.
3. Run focused suites and commit.

### Task 4: Read-only survey and delivery

1. Re-run the same 47-home read-only production sample with old and new converter, classify equal/mismatch/no-block/error, and record each remaining mismatch by structure class.
2. Test Lisa and representative mismatches in a local/staging editor round trip. No installed production conversion.
3. Run relevant test/lint gates, push branch, open PR. State next release must run `migrate` and attach migration plan and survey results. Wait for CI, then move CMS-95 to In Review and report to PM.
