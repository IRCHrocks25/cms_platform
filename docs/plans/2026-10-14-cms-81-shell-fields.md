# CMS-81 Block-Shell Fields Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show Jill's already annotated Scorecard fields in Kate's block-based editor.

**Architecture:** Preserve parsed template-level Content sections when the editor builds its block instance list. Instance sections remain first. Existing controls, field IDs, save path, and public renderer handle the shell fields.

**Tech Stack:** Django views/templates, Django TestCase, GitHub Actions, Dokploy.

---

### Task 1: Prove the installed failure in a regression test

**Files:**
- Create: `core/tests/test_shell_editor_fields.py`

**Steps:**
1. Make a block-shell template with a standalone `scorecard_popup` section, a `main` region, and one allowed block instance. Create a tenant and member.
2. GET `dashboard:tenant_home` on the tenant host. Assert the response contains `data-field-id="scorecard_popup.corporate_label"`, `data-field-id="scorecard_popup.corporate_url"`, and the block instance field.
3. Run `python manage.py test core.tests.test_shell_editor_fields`; expect the popup assertion to fail on the current code.

### Task 2: Preserve shell Content sections

**Files:**
- Modify: `dashboard/views.py` in `_render_editor` near the `block_mode` branch.
- Modify: `templates/dashboard/editor.html` to mark fixed shell entries.
- Modify: `static/js/editor.js` to avoid dragging fixed shell entries.
- Test: `core/tests/test_shell_editor_fields.py`

**Steps:**
1. Save the parsed `content_sections` before creating the block instance sections.
2. Set `content_sections = instance_sections + shell_content_sections` instead of dropping the latter. Do not mark shell sections as block instances.
3. Add `data-static-section="1"` to the shell sidebar entry and skip its drag binding in JavaScript.
4. Add a POST save/reload assertion for both popup label and URL through the tenant editor route.
5. Run `python manage.py test core.tests.test_shell_editor_fields` and nearby editor/block tests; expect pass.
6. Run `python manage.py check`, `node --check static/js/editor.js`, `git diff --check`, then commit the code and test.

### Task 3: Review, merge, and accept live

**Steps:**
1. Push and open a CMS-81 follow-up PR; wait for hosted CI and review the diff.
2. Merge after green checks; verify staging and deploy production via Dokploy.
3. On Jill's installed account, verify the two popup fields appear in Kate. Save temporary label and URL values, reload, and verify rendering inside a transaction that is rolled back. Confirm the published popup still has its original labels and quiz URLs.
4. Record commands, environment, results, and any unrun browser checks under **Testing / acceptance evidence** on CMS-81. Mark Done only after every required check passes.
