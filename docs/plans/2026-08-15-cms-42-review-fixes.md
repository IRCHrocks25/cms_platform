# CMS-42 Review Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close PR #34's tenant-isolation, CSP compatibility, preview-submission, and test-coverage findings without expanding beyond phase one.

**Architecture:** Keep tenant-specific form IDs out of templates by rejecting populated GHL defaults during schema construction. Fail closed for legacy schema during merge/render, preserve existing iframe compatibility by removing the global child-frame CSP restriction, and make preview non-submittable with a sandbox plus a full-slot overlay.

**Tech Stack:** Django, BeautifulSoup, Django TestCase/SimpleTestCase, JSON-RPC MCP tests, HTML/CSS.

---

### Task 1: Reject template-authored form values

**Files:**
- Modify: `core/tests/test_ghl_embed_slots.py`
- Modify: `api/tests/test_mcp_push_page.py`
- Modify: `core/tests/test_template_service.py`
- Modify: `core/parser.py`
- Modify: `core/renderer.py`
- Modify: `api/mcp/tools.py`

**Step 1: Write the failing tests**

Replace the parser test that accepts `form:<id>` defaults with a rejection
test. Add renderer coverage proving a legacy schema default is cleared while
explicit stored content still renders. Add MCP tests that submit populated
defaults resembling same-tenant and cross-tenant form IDs and assert identical
tool errors and unchanged templates. Add a version-restore test for archived
HTML containing a populated embed default.

**Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_ghl_embed_slots api.tests.test_mcp_push_page core.tests.test_template_service -v 2
```

Expected: failures show populated defaults are still accepted/rendered or
surface as unhandled errors.

**Step 3: Implement the minimal fail-closed behavior**

After validating annotation kind and value shape in `build_schema`, raise when
the parsed value is non-empty. In `merge_with_defaults`, replace embed defaults
with `""` before applying stored content. In `render_site`, always process an
annotated embed slot and use `""` when stored content lacks the field. Catch
parser `ValueError` in `_push_html_onto_template` and return `tool_error`.

**Step 4: Run tests to verify they pass**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add core/parser.py core/renderer.py api/mcp/tools.py core/tests/test_ghl_embed_slots.py api/tests/test_mcp_push_page.py core/tests/test_template_service.py
git commit -m "fix: reject GHL form template defaults"
```

### Task 2: Prove all MCP embed tools reparse current HTML

**Files:**
- Modify: `api/tests/test_mcp_ghl_embed.py`
- Modify: `api/mcp/tools.py`
- Modify: `core/services/ghl_forms.py`

**Step 1: Write the failing tests**

Poison the stored schema so the embed field appears to be text. Assert
`list_embed_slots` still lists the field from current HTML, and
`set_embed_slot` still validates and writes it. Add a defensive missing-template
test if the model fixture can represent it safely. Add an exception-contract
test proving `GhlFormsUnavailable.args` carries the public message and the
exception remains hashable.

**Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python manage.py test api.tests.test_mcp_ghl_embed core.tests.test_ghl_forms -v 2
```

Expected: new stale-schema assertions expose any path using stored schema; the
exception-contract assertion fails before `eq=False`/initialization changes.

**Step 3: Implement minimal robustness fixes**

Retain `_current_schema` on list/set, guard `template` before reading
`is_client_editable`, and make `GhlFormsUnavailable` a normal exception-valued
dataclass (`eq=False`) whose initialization populates `.args`.

**Step 4: Run tests to verify they pass**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add api/mcp/tools.py core/services/ghl_forms.py api/tests/test_mcp_ghl_embed.py core/tests/test_ghl_forms.py
git commit -m "test: cover stale GHL embed schemas"
```

### Task 3: Preserve CSP compatibility

**Files:**
- Modify: `core/tests/test_frame_ancestors.py`
- Modify: `core/middleware.py`

**Step 1: Write the failing test**

Replace `frame-src` allowlist assertions with a regression that the middleware
emits no `frame-src`, while keeping the existing negative assertion that GHL
child-frame support never changes `frame-ancestors`.

**Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_frame_ancestors -v 2
```

Expected: FAIL because the header currently includes `frame-src`.

**Step 3: Implement the minimal CSP change**

Remove the child-frame sources and emit only
`frame-ancestors <configured-sources>;`.

**Step 4: Run test to verify it passes**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add core/middleware.py core/tests/test_frame_ancestors.py
git commit -m "fix: preserve existing child iframe CSP behavior"
```

### Task 4: Structurally block preview submission

**Files:**
- Modify: `core/tests/test_ghl_embed_slots.py`
- Modify: `core/renderer.py`

**Step 1: Write the failing tests**

Extend the populated-preview test to require
`sandbox="allow-scripts"`, forbid `allow-forms`, require full-slot overlay
markup and preview CSS, and retain defense-in-depth attributes. Add a populated
public-render test proving it has no sandbox, preview note, or overlay.

**Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_ghl_embed_slots -v 2
```

Expected: FAIL because sandbox and overlay styling are missing.

**Step 3: Implement the shield**

Add the restricted sandbox to preview iframes, mark the slot as a preview
container, and add preview-injected CSS for a full inset overlay with readable
high-contrast copy. Keep all preview attributes absent from public output.

**Step 4: Run test to verify it passes**

Run the command from Step 2. Expected: PASS.

**Step 5: Commit**

```bash
git add core/renderer.py core/tests/test_ghl_embed_slots.py
git commit -m "fix: shield GHL forms in editor previews"
```

### Task 5: Reconcile documentation and verify the branch

**Files:**
- Modify: `README.md`
- Modify: `docs/plans/2026-08-14-cms-42-ghl-embed-slot-design.md`
- Modify: `docs/plans/2026-08-14-cms-42-ghl-embed-slot.md`

**Step 1: Update documentation**

Document that form IDs must be selected as tenant content, populated template
defaults are invalid, previews use a sandboxed full-slot shield, and the CSP
preserves unconstrained child-frame behavior while retaining
`frame-ancestors`.

**Step 2: Run focused and complete verification**

Run:

```bash
.venv/bin/python manage.py test core.tests.test_ghl_embed_slots core.tests.test_frame_ancestors core.tests.test_template_service core.tests.test_ghl_forms api.tests.test_mcp_push_page api.tests.test_mcp_ghl_embed -v 2
.venv/bin/python manage.py test
npm run check:assets
node --check static/js/editor.js
.venv/bin/python manage.py check
git diff --check origin/main...HEAD
```

Expected: all tests and checks PASS.

**Step 3: Commit**

```bash
git add README.md docs/plans/2026-08-14-cms-42-ghl-embed-slot-design.md docs/plans/2026-08-14-cms-42-ghl-embed-slot.md
git commit -m "docs: clarify GHL embed security boundaries"
```

**Step 4: Push and hand back**

Push `feat/cms-42-ghl-embed-slot` to the authorized personal SSH remote, update
PR #34, comment the fix and verification summary on CMS-42, and move the work
item from In Progress to In Review. Do not merge.
