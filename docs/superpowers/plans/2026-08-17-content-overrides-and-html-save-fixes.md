# Content Overrides and HTML Save Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an agency HTML re-upload actually reach the visitor, and stop the dashboard reporting success for writes that changed nothing.

**Architecture:** Two layers. Layer 1 is three self-contained correctness fixes in the save path (reject blank HTML, keep the operator's candidate on a field-loss conflict, refuse to append a byte-identical version). Layer 2 introduces a sparse-overrides invariant for `Tenant.content` / `Page.content`: stored content holds only values that differ from the template's current defaults, so `merge_with_defaults` stays the single display merge and new template defaults flow through. A one-off reconciliation command migrates existing rows.

**Tech Stack:** Django 5.1.2 on Python 3.12, PostgreSQL in prod / SQLite locally, BeautifulSoup + lxml for parsing, no new dependencies.

---

## Background

Read these before starting:

- `CLAUDE.md` in the repo root, especially "Constraints / non-goals" and "Known sharp edges".
- `core/parser.py` and `core/renderer.py`. The repo calls these the heart of the system.
- The incident this plan closes: site `denny`, Template id 128, accumulated 7 byte-identical `TemplateVersion` rows while the operator was told "Template updated." every time. The public page never changed.

Two verified defects and one verified root cause:

1. `dashboard/views.py:248` falls back to the stored HTML when `html_source` is missing, empty, or whitespace-only, then saves and reports success.
2. `templates/dashboard/template_form.html:101` renders `template.html_source|default:form_data.html_source`. On a `FieldLossError` re-render the old value is truthy, so the conflict page hands the operator back the **old** HTML. Confirming from that page saves the old bytes. This is the mechanism that produced the 7 identical versions.
3. `core/renderer.py:858-871` writes stored content over every surviving `data-edit` id, and `core/services/accounts.py:127` seeds `tenant.content` from schema defaults at creation, so every field is populated from day one and masks any newly uploaded HTML.

## Semantics this plan locks in

Stated once here so every task agrees:

- **Meta keys** are top-level keys starting with `_` (`_styles`, `_global`, `_tokens`, `_hidden`). They are never pruned and never treated as sections.
- **An override** is a stored `content[section][field]` whose value differs from `defaults[section][field]`.
- **Pruning** removes stored fields whose value equals the corresponding default. A section left with no fields is removed.
- **Dormant fields are kept.** A stored field with no corresponding default (the template dropped or renamed it) is preserved, because template swap-back and field-loss confirmation both rely on it. `core/tests/test_template_service.py` and `core/tests/test_tenant_template_swap.py` assert this today.
- **Setting a field to its current default means "follow the template".** It does not mean "freeze this literal forever".

## File Structure

| File | Responsibility |
|---|---|
| `core/services/content_overrides.py` | **Create.** Pure functions: `prune_to_overrides`, `is_untouched`. No Django model imports, so it is trivially testable. |
| `core/services/templates.py` | **Modify.** No-op HTML detection, row locking, and the template-transition reconciliation call. |
| `core/services/accounts.py:127,163` | **Modify.** Stop seeding content from defaults. |
| `core/services/content_versions.py` | **Modify.** Prune on save and on restore. |
| `dashboard/views.py:239-300` | **Modify.** Reject blank HTML; pass an explicit `html_value`; report an unchanged save honestly. |
| `dashboard/views.py:2426-2476` | **Modify.** `_save_content` prunes before persisting. |
| `templates/dashboard/template_form.html:101` | **Modify.** Stop preferring the model value over bound form data. |
| `templates/dashboard/_html_source_editor.html:248-253` | **Modify.** Dispatch `input` after assigning `textarea.value`. |
| `api/mcp/tools.py` | **Modify.** `push_page` no-op response, `patch_content` pruning, `_content_still_template_defaults` rewrite. |
| `core/management/commands/reconcile_content.py` | **Create.** `--dry-run` by default; migrates existing rows to the sparse invariant. |
| `core/tests/test_content_overrides.py` | **Create.** Unit tests for the pure functions. |
| `core/tests/test_html_save_guards.py` | **Create.** Layer 1 regression tests. |
| `core/tests/test_content_reconcile.py` | **Create.** Layer 2 integration tests. |

## Test environment

The repo's `.venv` is Python 3.14 with Django 5.2.15 and is broken per `CLAUDE.md:402`. Build a correct one first:

```bash
cd /home/bernardjr/Desktop/Code/work/katalyst-ai/cms_platform
uv venv --python 3.12 .venv-3.12
VIRTUAL_ENV=$PWD/.venv-3.12 uv pip install -r requirements.txt
.venv-3.12/bin/python -c "import django; print(django.get_version())"   # expect 5.1.2
```

Five rendering tests error on `Missing staticfiles manifest entry for 'css/dashboard.css'` in a bare checkout. That is a pre-existing environment issue, not caused by this work. Any test in this plan that renders a dashboard template must carry:

```python
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
```

applied with `@override_settings(STORAGES=PLAIN_STATIC)`.

Run the full suite with:

```bash
.venv-3.12/bin/python manage.py test 2>&1 | tail -20
```

---

## Phase 0: Safety rails (human, before any deploy)

These are not code tasks. They gate Phase 3.

- [ ] **Step 1: Back up the production database**

Dokploy panel at `dokploy.katek.app`, project `cms-dashboard`, environment `production` (`RTqjeg8in9kGVOmJ6L_Cc`). Take a manual Postgres backup and confirm the dump file exists and is non-zero before continuing. Record the backup id and timestamp in the PR description.

- [ ] **Step 2: Confirm production autoDeploy is still off**

`deploy/STAGING.md` states production has `autoDeploy` off and staging has it on, both tracking `main`. Verify in Dokploy before merging anything, because the whole staging rehearsal depends on a merge to `main` not shipping production.

---

## Phase 1: Save-path correctness

Three independent fixes. Each is shippable on its own and none of them touch stored content.

### Task 1: Reject blank HTML in `template_detail`

**Files:**
- Modify: `dashboard/views.py:248`
- Test: `core/tests/test_html_save_guards.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_html_save_guards.py`:

```python
"""Regression guards for the template HTML save path."""
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.parser import build_schema
from core.renderer import merge_with_defaults

OLD = (
    '<html><body>'
    '<section data-section="hero" data-label="Hero">'
    '<h1 data-edit="hero.title" data-type="text">Old headline</h1>'
    '</section></body></html>'
)
NEW_SAME_FIELDS = OLD.replace("Old headline", "New headline")
NEW_DROPS_FIELDS = '<html><body><h1>Unannotated rewrite</h1></body></html>'

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class BlankHtmlRejectedTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        self.tpl.versions.all().delete()

    def _post(self, **extra):
        data = {"name": "T", "description": "", "editing_mode": "editable"}
        data.update(extra)
        return self.client.post(f"/dashboard/templates/{self.tpl.pk}/", data)

    def test_missing_html_source_is_rejected(self):
        resp = self._post()
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)
        self.assertEqual(self.tpl.versions.count(), 0)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn("HTML source cannot be empty.", msgs)

    def test_empty_html_source_is_rejected(self):
        resp = self._post(html_source="")
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)
        self.assertEqual(self.tpl.versions.count(), 0)

    def test_whitespace_only_html_source_is_rejected(self):
        resp = self._post(html_source="   \n\t  ")
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)
        self.assertEqual(self.tpl.versions.count(), 0)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.BlankHtmlRejectedTest -v 2
```

Expected: all three fail. Today the view returns 302 and appends a version.

- [ ] **Step 3: Implement**

In `dashboard/views.py`, inside `template_detail`'s `if request.method == "POST":` block, replace:

```python
        html_source = request.POST.get("html_source") or template.html_source
```

with:

```python
        html_source = request.POST.get("html_source") or ""
        if not html_source.strip():
            # A blank field is never "keep the current HTML" — that fallback
            # silently turned failed uploads into successful-looking no-ops.
            messages.error(request, "HTML source cannot be empty.")
            return render(
                request,
                "dashboard/template_form.html",
                {
                    "template": template,
                    "tenants_using": list(
                        template.tenants.only("id", "name", "subdomain").order_by("name")
                    ),
                    "form_data": {
                        "name": template.name,
                        "description": template.description,
                        "html_source": template.html_source,
                        "editing_mode": template.editing_mode,
                    },
                },
                status=400,
            )
```

Note this returns **before** the `template.name` / `template.description` / `template.editing_mode` assignments take effect in the database, because nothing is saved on this path. Move the blank check above those assignments so a rejected submit cannot half-apply metadata.

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.BlankHtmlRejectedTest -v 2
```

Expected: 3 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py core/tests/test_html_save_guards.py
git commit -m "fix(dashboard): reject blank html_source instead of silently re-saving"
```

### Task 2: Keep the operator's candidate on a field-loss conflict

**Files:**
- Modify: `dashboard/views.py:264-282`, `templates/dashboard/template_form.html:101`
- Test: `core/tests/test_html_save_guards.py`

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_html_save_guards.py`:

```python
@override_settings(STORAGES=PLAIN_STATIC)
class FieldLossKeepsCandidateTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops2", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        owner = User.objects.create_user("client2", password="pw")
        Tenant.objects.create(
            name="Denny", subdomain="denny", template=self.tpl, owner=owner,
            content=merge_with_defaults(build_schema(OLD), {}),
            is_published=True,
        )

    def _post(self, html, allow=False):
        data = {"name": "T", "description": "", "editing_mode": "editable",
                "html_source": html}
        if allow:
            data["allow_field_loss"] = "1"
        return self.client.post(f"/dashboard/templates/{self.tpl.pk}/", data)

    def test_conflict_page_shows_the_submitted_html_not_the_old_one(self):
        resp = self._post(NEW_DROPS_FIELDS)
        self.assertEqual(resp.status_code, 409)
        body = resp.content.decode()
        self.assertIn("Unannotated rewrite", body)
        self.assertNotIn("Old headline", body)

    def test_confirming_the_conflict_saves_the_new_html(self):
        self._post(NEW_DROPS_FIELDS)
        resp = self._post(NEW_DROPS_FIELDS, allow=True)
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.tpl.html_source, NEW_DROPS_FIELDS)
```

- [ ] **Step 2: Run it and watch the first test fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.FieldLossKeepsCandidateTest -v 2
```

Expected: `test_conflict_page_shows_the_submitted_html_not_the_old_one` fails on `AssertionError: 'Unannotated rewrite' not found`. The second test already passes; it is there to prove the fix does not break confirmation.

- [ ] **Step 3: Implement**

In `templates/dashboard/template_form.html`, replace line 101:

```django
        {% include "dashboard/_html_source_editor.html" with html_value=template.html_source|default:form_data.html_source|default_if_none:'' %}
```

with:

```django
        {% include "dashboard/_html_source_editor.html" with html_value=html_value|default_if_none:'' %}
```

Then make every render of `template_form.html` pass an explicit `html_value`, mirroring what `page_edit_html` already does. In `dashboard/views.py`:

- `template_create` GET: `"html_value": STARTER_TEMPLATE_HTML`
- `template_create` POST error path: `"html_value": html_source`
- `template_detail` GET: `"html_value": template.html_source`
- `template_detail` blank-reject path (Task 1): `"html_value": template.html_source`
- `template_detail` `FieldLossError` path: `"html_value": html_source` (the candidate)

Keep `form_data` as-is for the other fields. It still needs every key the template references, per the `form_data` warning in `CLAUDE.md`.

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards -v 2
```

Expected: 5 tests, OK.

- [ ] **Step 5: Fix the annotator's stale-editor sync**

In `templates/dashboard/_html_source_editor.html:248-253`, the fallback path assigns `textarea.value` without notifying CodeMirror. Add the dispatch immediately after the assignment:

```javascript
      textarea.value = annotated;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
```

`frontend/code-editor.js:69` listens for `input` and re-dispatches into the CodeMirror document, so this keeps the visible editor and the submitted field in step.

- [ ] **Step 6: Commit**

```bash
git add dashboard/views.py templates/dashboard/template_form.html \
        templates/dashboard/_html_source_editor.html core/tests/test_html_save_guards.py
git commit -m "fix(dashboard): keep submitted HTML on field-loss conflict re-render"
```

### Task 3: Refuse to append a byte-identical version

**Files:**
- Modify: `core/services/templates.py:99-218`, `dashboard/views.py:283`, `api/mcp/tools.py:563-592`
- Test: `core/tests/test_html_save_guards.py`

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_html_save_guards.py`:

```python
from core.services import templates as template_svc


class NoOpVersionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops3", password="pw", is_staff=True)
        self.tpl = Template.objects.create(name="T", html_source=OLD)

    def test_first_save_creates_v1_even_though_bytes_match(self):
        self.tpl.versions.all().delete()
        result = template_svc.save_template_version(
            self.tpl, OLD, user=self.user, label="Initial",
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), 1)

    def test_identical_resave_creates_no_version(self):
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(self.tpl, OLD, user=self.user)
        self.assertTrue(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before)
        self.assertEqual(result.version.number, before)

    def test_changed_html_still_creates_a_version(self):
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(
            self.tpl, NEW_SAME_FIELDS, user=self.user,
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before + 1)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.NoOpVersionTest -v 2
```

Expected: fails on `SaveTemplateResult` having no `unchanged` attribute.

- [ ] **Step 3: Implement**

In `core/services/templates.py`, add `unchanged: bool = False` to the `SaveTemplateResult` dataclass, then rewrite the body of `save_template_version`:

```python
    old_fields = _dotted_field_ids(template.schema)
    new_schema = build_schema(html_source)
    new_fields = _dotted_field_ids(new_schema)
    lost = old_fields - new_fields

    affected: list[dict[str, Any]] = []
    if lost:
        affected = _affected_published(template, lost)
        if affected and not allow_field_loss:
            raise FieldLossError(lost, affected)

    with transaction.atomic():
        # Lock the row so two concurrent writers cannot both compute the same
        # next_number and collide on uniq_template_version_number.
        locked = Template.objects.select_for_update().get(pk=template.pk)
        latest = (
            template.versions.select_for_update().order_by("-number").first()
        )
        # A brand-new template has no versions yet: always cut v1, even though
        # html_source already equals what the caller is "saving".
        if latest is not None and locked.html_source == html_source:
            return SaveTemplateResult(
                template=template,
                version=latest,
                lost_fields=set(),
                affected=[],
                unchanged=True,
            )

        template.html_source = html_source
        template.save()  # re-derives schema
        next_number = (latest.number if latest is not None else 0) + 1
        version = TemplateVersion.objects.create(
            template=template,
            number=next_number,
            html_source=template.html_source,
            schema=template.schema or {},
            label=label or "",
            saved_by=user if getattr(user, "pk", None) else None,
        )

    return SaveTemplateResult(
        template=template,
        version=version,
        lost_fields=lost,
        affected=affected,
    )
```

`select_for_update()` is a no-op on SQLite outside a transaction but harmless; it does real work on the production Postgres.

In `dashboard/views.py:283`, make the flash honest:

```python
        if result.unchanged:
            messages.info(request, "No HTML changes — nothing to save.")
        else:
            messages.success(request, "Template updated.")
```

This requires capturing the return value: change `template_svc.save_template_version(...)` to `result = template_svc.save_template_version(...)`.

In `api/mcp/tools.py`, `push_page` already returns `result.version.number` and the current etag, so an unchanged push now returns the existing version rather than a new one. Add `"unchanged": result.unchanged` to both `tool_success` payloads in `push_page` so a caller can tell.

- [ ] **Step 4: Run the whole suite**

```bash
.venv-3.12/bin/python manage.py test 2>&1 | tail -20
```

Expected: the 5 pre-existing staticfiles errors and nothing else. If `test_mcp_push_page.py` or `test_template_service.py` fail, read them: they may assert a version count that this change deliberately alters. Update those assertions and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add core/services/templates.py dashboard/views.py api/mcp/tools.py \
        core/tests/test_html_save_guards.py
git commit -m "fix(templates): no version row for a byte-identical save; lock version allocation"
```

---

## Phase 2: Sparse content overrides

### Task 4: The pure canonicalizer

**Files:**
- Create: `core/services/content_overrides.py`
- Test: `core/tests/test_content_overrides.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_content_overrides.py`:

```python
from django.test import SimpleTestCase

from core.services.content_overrides import is_untouched, prune_to_overrides

DEFAULTS = {
    "hero": {"title": "Old headline", "cta": "#contact"},
    "about": {"body": "Old body"},
}


class PruneToOverridesTest(SimpleTestCase):
    def test_values_equal_to_defaults_are_dropped(self):
        content = {"hero": {"title": "Old headline", "cta": "#contact"}}
        self.assertEqual(prune_to_overrides(content, DEFAULTS), {})

    def test_values_differing_from_defaults_survive(self):
        content = {"hero": {"title": "Client wrote this", "cta": "#contact"}}
        self.assertEqual(
            prune_to_overrides(content, DEFAULTS),
            {"hero": {"title": "Client wrote this"}},
        )

    def test_dormant_fields_with_no_default_are_kept(self):
        content = {"gone": {"headline": "from an older template"}}
        self.assertEqual(prune_to_overrides(content, DEFAULTS), content)

    def test_meta_keys_pass_through_untouched(self):
        content = {
            "hero": {"title": "Old headline"},
            "_hidden": ["about"],
            "_styles": {"hero.title": {"color": "#fff"}},
            "_global": {},
            "_tokens": {"blue": "#2A5EB0"},
        }
        self.assertEqual(
            prune_to_overrides(content, DEFAULTS),
            {
                "_hidden": ["about"],
                "_styles": {"hero.title": {"color": "#fff"}},
                "_global": {},
                "_tokens": {"blue": "#2A5EB0"},
            },
        )

    def test_empty_and_none_inputs_are_safe(self):
        self.assertEqual(prune_to_overrides(None, DEFAULTS), {})
        self.assertEqual(prune_to_overrides({}, DEFAULTS), {})
        self.assertEqual(prune_to_overrides({"hero": None}, DEFAULTS), {})

    def test_no_defaults_means_everything_is_an_override(self):
        content = {"hero": {"title": "x"}}
        self.assertEqual(prune_to_overrides(content, {}), content)

    def test_input_is_not_mutated(self):
        content = {"hero": {"title": "Old headline"}}
        prune_to_overrides(content, DEFAULTS)
        self.assertEqual(content, {"hero": {"title": "Old headline"}})


class IsUntouchedTest(SimpleTestCase):
    def test_empty_content_is_untouched(self):
        self.assertTrue(is_untouched({}))
        self.assertTrue(is_untouched(None))

    def test_meta_only_content_is_untouched(self):
        self.assertTrue(is_untouched({"_styles": {}, "_hidden": []}))

    def test_any_override_means_touched(self):
        self.assertFalse(is_untouched({"hero": {"title": "x"}}))
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_overrides -v 2
```

Expected: `ModuleNotFoundError: No module named 'core.services.content_overrides'`.

- [ ] **Step 3: Implement**

Create `core/services/content_overrides.py`:

```python
"""Sparse-overrides invariant for Tenant.content / Page.content.

Stored content holds only what differs from the template's defaults, so
``merge_with_defaults`` stays the single display merge and a newly uploaded
template's defaults reach the visitor instead of being masked by a value the
client never typed.

Meta namespaces (``_styles``, ``_global``, ``_tokens``, ``_hidden``) are editor
state, not fields, and pass through untouched. Fields with no matching default
are *dormant*, not stale: a template swap-back or a field-loss confirmation is
expected to bring them back, and existing tests assert they survive.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _is_meta(key: Any) -> bool:
    return isinstance(key, str) and key.startswith("_")


def prune_to_overrides(
    content: dict[str, Any] | None, defaults: dict[str, Any] | None
) -> dict[str, Any]:
    """Return a copy of ``content`` holding only values that differ from
    ``defaults``, plus every meta key verbatim."""
    out: dict[str, Any] = {}
    for section_id, section in (content or {}).items():
        if _is_meta(section_id):
            out[section_id] = deepcopy(section)
            continue
        if not isinstance(section, dict):
            continue
        section_defaults = (defaults or {}).get(section_id)
        if not isinstance(section_defaults, dict):
            section_defaults = {}
        kept = {
            field: deepcopy(value)
            for field, value in section.items()
            if field not in section_defaults or section_defaults[field] != value
        }
        if kept:
            out[section_id] = kept
    return out


def is_untouched(content: dict[str, Any] | None) -> bool:
    """True when nothing but meta state is stored, i.e. no field overrides."""
    return not any(not _is_meta(key) for key in (content or {}))
```

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_overrides -v 2
```

Expected: 10 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add core/services/content_overrides.py core/tests/test_content_overrides.py
git commit -m "feat(content): add sparse-overrides canonicalizer"
```

### Task 5: Reconcile stored content when a template's HTML changes

**Files:**
- Modify: `core/services/templates.py`
- Test: `core/tests/test_content_reconcile.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_content_reconcile.py`:

```python
from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Page, Template, Tenant
from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site
from core.services import templates as template_svc

OLD = (
    '<html><body>'
    '<section data-section="hero" data-label="Hero">'
    '<h1 data-edit="hero.title" data-type="text">Old headline</h1>'
    '<p data-edit="hero.sub" data-type="text">Old sub</p>'
    '</section></body></html>'
)
NEW = OLD.replace("Old headline", "New headline").replace("Old sub", "New sub")


class TemplateChangeReconcilesContentTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ops", password="pw", is_staff=True)
        self.owner = User.objects.create_user("client", password="pw")
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        self.tenant = Tenant.objects.create(
            name="Denny", subdomain="denny", template=self.tpl, owner=self.owner,
            content=merge_with_defaults(build_schema(OLD), {}),
            is_published=True,
        )

    def test_untouched_content_lets_the_new_html_through(self):
        template_svc.save_template_version(
            self.tpl, NEW, user=self.user, allow_field_loss=True,
        )
        self.tenant.refresh_from_db()
        html = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        self.assertIn("New headline", html)
        self.assertNotIn("Old headline", html)

    def test_a_real_client_edit_survives_the_template_change(self):
        self.tenant.content["hero"]["title"] = "Client wrote this"
        self.tenant.save(update_fields=["content"])
        template_svc.save_template_version(
            self.tpl, NEW, user=self.user, allow_field_loss=True,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["hero"]["title"], "Client wrote this")
        html = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        self.assertIn("Client wrote this", html)
        self.assertIn("New sub", html)   # unedited field follows the template

    def test_meta_state_survives(self):
        self.tenant.content["_hidden"] = ["hero"]
        self.tenant.content["_tokens"] = {"blue": "#000000"}
        self.tenant.save(update_fields=["content"])
        template_svc.save_template_version(
            self.tpl, NEW, user=self.user, allow_field_loss=True,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["_hidden"], ["hero"])
        self.assertEqual(self.tenant.content["_tokens"], {"blue": "#000000"})

    def test_pages_on_the_same_template_are_reconciled_too(self):
        page_tpl = Template.objects.create(name="P", html_source=OLD, tenant=self.tenant)
        page = Page.objects.create(
            tenant=self.tenant, template=page_tpl, title="About", slug="about",
            content=merge_with_defaults(build_schema(OLD), {}),
        )
        template_svc.save_template_version(
            page_tpl, NEW, user=self.user, allow_field_loss=True,
        )
        page.refresh_from_db()
        self.assertEqual(page.content, {})

    def test_an_unchanged_save_does_not_touch_content(self):
        before = dict(self.tenant.content)
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, before)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_reconcile -v 2
```

Expected: `test_untouched_content_lets_the_new_html_through` fails with "Old headline" still rendered.

- [ ] **Step 3: Implement**

In `core/services/templates.py`, import the canonicalizer at the top:

```python
from core.services.content_overrides import prune_to_overrides
```

Then inside `save_template_version`, capture the old defaults **before** `template.save()` re-derives the schema, and reconcile every consumer inside the same transaction after the write:

```python
    old_defaults = (template.schema or {}).get("defaults") or {}
```

Put that line immediately after `old_fields = _dotted_field_ids(template.schema)`.

Then, inside the `with transaction.atomic():` block, after `TemplateVersion.objects.create(...)`:

```python
        # Values a client never typed (equal to the OLD defaults) must not mask
        # the new HTML. Genuine overrides and dormant fields are preserved.
        for _label, row, content, _published in _sites_using_template(template):
            pruned = prune_to_overrides(content, old_defaults)
            if pruned != (content or {}):
                row.content = pruned
                row.save(update_fields=["content", "updated_at"])
```

`_sites_using_template` already yields both `Tenant` and `Page` rows for the template, which is what covers a shared template feeding several sites.

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_reconcile -v 2
```

Expected: 5 tests, OK.

- [ ] **Step 5: Run the full suite and fix fallout**

```bash
.venv-3.12/bin/python manage.py test 2>&1 | tail -30
```

`core/tests/test_renderer_preserves_unedited.py`, `core/tests/test_template_service.py` and `core/tests/test_tenant_template_swap.py` are the likely failures. Read each before changing it. Preserve any assertion about **dormant** fields surviving; those are intentional. Change assertions that encode "a seeded default is stored forever", because that is the behaviour this task removes.

- [ ] **Step 6: Commit**

```bash
git add core/services/templates.py core/tests/test_content_reconcile.py
git commit -m "feat(content): prune seeded defaults when a template's HTML changes"
```

### Task 6: Enforce the invariant on every content write path

**Files:**
- Modify: `dashboard/views.py:2426-2476`, `core/services/content_versions.py`, `core/services/accounts.py:127,163`, `api/mcp/tools.py` (`patch_content`)
- Test: `core/tests/test_content_reconcile.py`

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_content_reconcile.py`:

```python
import json

from django.test import override_settings

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class WritePathsStaySparseTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        owner = User.objects.create_user("client", password="pw")
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        self.tenant = Tenant.objects.create(
            name="Denny", subdomain="denny", template=self.tpl, owner=owner,
            content={}, is_published=True,
        )

    def test_editor_autosave_of_the_full_merged_blob_is_pruned(self):
        merged = merge_with_defaults(build_schema(OLD), {})
        merged["hero"]["title"] = "Client wrote this"
        resp = self.client.post(
            f"/dashboard/sites/{self.tenant.pk}/save/",
            data=json.dumps({"content": merged}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": "Client wrote this"}})

    def test_new_tenant_starts_with_empty_content(self):
        from core.services import accounts

        result = accounts.create_tenant_account(
            name="Fresh", subdomain="fresh", template=self.tpl,
            username="freshuser", created_by=self.staff,
        )
        self.assertEqual(result.tenant.content, {})
```

Read `core/services/accounts.py` for the real signature of the creation helper and adjust the second test's call to match it. Do not invent parameters.

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_reconcile.WritePathsStaySparseTest -v 2
```

Expected: the autosave test fails because the full merged blob is stored verbatim.

- [ ] **Step 3: Implement**

`dashboard/views.py`, in `_save_content`, immediately after `_normalize_styles(content)`:

```python
    # The editor posts the whole merged blob on every autosave. Store only what
    # differs from the template's current defaults so a later HTML upload is not
    # masked by values the client never typed.
    schema = editable.template.schema or {}
    content = prune_to_overrides(content, schema.get("defaults") or {})
```

with `from core.services.content_overrides import prune_to_overrides` added to the imports.

`core/services/accounts.py:127`, replace:

```python
            content=template.schema.get("defaults", {}) or {},
```

with:

```python
            content={},
```

and at line 163, replace the two-line reseed with:

```python
            tenant.content = {}
            tenant.save(update_fields=["content", "updated_at"])
```

`api/mcp/tools.py`, in `patch_content`, after `new_content = content_mod.write_field(...)`:

```python
    new_content = prune_to_overrides(
        new_content, (tpl.schema or {}).get("defaults") or {}
    )
```

`core/services/content_versions.py`, in `restore_tenant_content`, replace:

```python
        tenant.content = deepcopy(version.snapshot or {})
```

with:

```python
        defaults = (tenant.template.schema or {}).get("defaults") or {}
        tenant.content = prune_to_overrides(deepcopy(version.snapshot or {}), defaults)
```

Do **not** prune inside `save_tenant_content`. Its callers already prune, and pruning there would silently rewrite an MCP caller's `if_match` etag mid-transaction.

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_reconcile -v 2
```

Expected: 7 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py core/services/accounts.py core/services/content_versions.py \
        api/mcp/tools.py core/tests/test_content_reconcile.py
git commit -m "feat(content): enforce sparse overrides on every write path"
```

### Task 7: Rewrite the publish guard

**Files:**
- Modify: `api/mcp/tools.py:217-242`
- Test: `api/tests/test_mcp_publish_guard.py` (extend whichever existing file covers `publish_site`; find it with `grep -rl "_content_still_template_defaults\|publish_site" api/tests`)

- [ ] **Step 1: Write the failing test**

```python
def test_untouched_site_is_still_untouched_after_a_template_update(self):
    # Seeded-then-pruned content is {}, so an HTML update must not make the
    # site look edited to the publish guard.
    template_svc.save_template_version(
        self.tenant.template, NEW, user=self.staff, allow_field_loss=True,
    )
    self.tenant.refresh_from_db()
    self.assertTrue(_content_still_template_defaults(self.tenant))

def test_a_client_override_makes_it_touched(self):
    self.tenant.content = {"hero": {"title": "Client wrote this"}}
    self.tenant.save(update_fields=["content"])
    self.assertFalse(_content_still_template_defaults(self.tenant))

def test_meta_only_content_is_still_untouched(self):
    self.tenant.content = {"_hidden": ["hero"]}
    self.tenant.save(update_fields=["content"])
    self.assertTrue(_content_still_template_defaults(self.tenant))
```

- [ ] **Step 2: Run it and watch it fail**

The current implementation compares stored old defaults against the new schema defaults, so the first test fails after a template update.

- [ ] **Step 3: Implement**

Replace the body of `_content_still_template_defaults` in `api/mcp/tools.py` with:

```python
def _content_still_template_defaults(tenant: Tenant) -> bool:
    """True when the site holds no field overrides, only meta editor state.

    Under the sparse-overrides invariant (``core/services/content_overrides``)
    stored content *is* the override set, so "untouched" is simply "no
    non-meta keys". The old blob-comparison drifted after any template update,
    because it compared the old defaults it had stored against the new
    schema's defaults.
    """
    return is_untouched(tenant.content)
```

with `from core.services.content_overrides import is_untouched` added to the imports. Delete the now-unused `_public` helper.

- [ ] **Step 4: Run the MCP test module**

```bash
.venv-3.12/bin/python manage.py test api.tests -v 2 2>&1 | tail -20
```

- [ ] **Step 5: Commit**

```bash
git add api/mcp/tools.py api/tests/
git commit -m "fix(mcp): base the publish guard on overrides, not a defaults blob compare"
```

### Task 8: The reconciliation management command

**Files:**
- Create: `core/management/commands/reconcile_content.py`
- Test: `core/tests/test_content_reconcile.py`

Existing rows still hold fully-seeded blobs. Tasks 5 and 6 only fix rows that get written after deploy. This command migrates the rest, and its dry run is how we validate against production data without restoring a dump anywhere.

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_content_reconcile.py`:

```python
from io import StringIO

from django.core.management import call_command


class ReconcileCommandTest(TestCase):
    def setUp(self):
        owner = User.objects.create_user("client", password="pw")
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        self.tenant = Tenant.objects.create(
            name="Denny", subdomain="denny", template=self.tpl, owner=owner,
            content=merge_with_defaults(build_schema(OLD), {}),
            is_published=True,
        )
        self.tenant.content["hero"]["title"] = "Client wrote this"
        self.tenant.save(update_fields=["content"])

    def test_dry_run_reports_but_does_not_write(self):
        out = StringIO()
        call_command("reconcile_content", stdout=out)
        self.tenant.refresh_from_db()
        self.assertIn("hero.sub", out.getvalue())
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("Old sub", str(self.tenant.content))

    def test_apply_prunes_defaults_and_keeps_overrides(self):
        call_command("reconcile_content", "--apply", stdout=StringIO())
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": "Client wrote this"}})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_reconcile.ReconcileCommandTest -v 2
```

Expected: `CommandError: Unknown command: 'reconcile_content'`.

- [ ] **Step 3: Implement**

Create `core/management/commands/reconcile_content.py`:

```python
"""Migrate Tenant.content / Page.content to the sparse-overrides invariant.

Dry run by default. Prints, per row, which stored fields are being dropped as
equal to the current template default and which are kept as real overrides, so
the output can be reviewed against production before anything is written.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Page, Tenant
from core.services.content_overrides import prune_to_overrides


class Command(BaseCommand):
    help = "Prune stored content down to genuine overrides."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the changes. Without this the command only reports.",
        )
        parser.add_argument(
            "--subdomain", default="",
            help="Limit to one tenant subdomain (and its pages).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        only = (options["subdomain"] or "").strip()

        tenants = Tenant.objects.select_related("template")
        pages = Page.objects.select_related("template", "tenant")
        if only:
            tenants = tenants.filter(subdomain=only)
            pages = pages.filter(tenant__subdomain=only)

        rows = [(f"site:{t.subdomain}", t) for t in tenants]
        rows += [(f"page:{p.tenant.subdomain}/{p.slug}", p) for p in pages]

        total_dropped = 0
        total_kept = 0
        changed = 0

        for label, row in rows:
            if row.template_id is None:
                self.stdout.write(f"{label}: SKIP (no template)")
                continue
            defaults = (row.template.schema or {}).get("defaults") or {}
            before = row.content or {}
            after = prune_to_overrides(before, defaults)

            dropped = sorted(
                f"{s}.{f}"
                for s, fields in before.items()
                if not s.startswith("_") and isinstance(fields, dict)
                for f in fields
                if f not in (after.get(s) or {})
            )
            kept = sorted(
                f"{s}.{f}"
                for s, fields in after.items()
                if not s.startswith("_") and isinstance(fields, dict)
                for f in fields
            )
            total_dropped += len(dropped)
            total_kept += len(kept)

            if after == before:
                self.stdout.write(f"{label}: unchanged")
                continue

            changed += 1
            self.stdout.write(
                f"{label}: drop {len(dropped)} -> keep {len(kept)}"
            )
            if dropped:
                self.stdout.write(f"    dropped: {', '.join(dropped)}")
            if kept:
                self.stdout.write(f"    kept:    {', '.join(kept)}")

            if apply_changes:
                with transaction.atomic():
                    row.content = after
                    row.save(update_fields=["content", "updated_at"])

        mode = "APPLIED" if apply_changes else "DRY RUN (use --apply to write)"
        self.stdout.write(
            f"\n{mode}: {changed} row(s) would change, "
            f"{total_dropped} field(s) dropped, {total_kept} override(s) kept."
        )
```

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_reconcile -v 2
```

Expected: 9 tests, OK.

- [ ] **Step 5: Run the whole suite one more time**

```bash
.venv-3.12/bin/python manage.py test 2>&1 | tail -20
```

Expected: only the 5 pre-existing staticfiles errors.

- [ ] **Step 6: Commit**

```bash
git add core/management/commands/reconcile_content.py core/tests/test_content_reconcile.py
git commit -m "feat(content): add reconcile_content management command with dry run"
```

---

## Phase 3: Staging rehearsal, then production

`deploy/STAGING.md` is the authority here. Two rules from it that constrain this phase:

- **Never restore a production dump into staging.** It carries real client content and real user rows onto a URL with no access control beyond login.
- Staging tracks `main` with autoDeploy **on**; production tracks `main` with autoDeploy **off**. So a merge to `main` reaches staging automatically and production only when someone triggers it.

- [ ] **Step 1: Point staging at the branch**

In Dokploy, project `cms-dashboard`, environment `staging` (`MzPbhgIhn6vzLJWBH5Clq`), change the branch on the `sites-staging` compose service to this work's branch and redeploy. Set it back to `main` when the rehearsal is done. This avoids merging to `main` before the code has run anywhere.

- [ ] **Step 2: Seed and exercise the UI paths**

```bash
python manage.py seed_demo_data     # staging only; ALLOW_DEMO_SEED=1 is set there
```

Then at `https://staging.sites.katek.app/` walk the exact incident:

1. Open a seeded site's home template at `/dashboard/templates/<pk>/`.
2. Paste HTML that drops fields. Expect a 409 conflict page that **still shows the HTML you pasted**.
3. Tick "save anyway" and submit. Expect the new HTML to be saved.
4. Reload the public render and confirm the new copy is visible, not the old.
5. Save the template again with no changes. Expect "No HTML changes" and no new version row.
6. Submit with the HTML field emptied via devtools. Expect a 400 and no version row.

- [ ] **Step 3: Dry-run the reconciliation on staging, then apply**

```bash
python manage.py reconcile_content              # review the report
python manage.py reconcile_content --apply
```

Then re-check a seeded client edit is still present and an unedited field now follows the template.

- [ ] **Step 4: Dry-run against production before deploying to it**

This is the step that validates the migration against real data without copying it anywhere. It only reads.

```bash
python manage.py reconcile_content              # production terminal, NO --apply
```

Read the report. Expect `site:denny` to show a large `drop` count and few or no `kept` entries, because that site's content is entirely seeded defaults. Any site showing a surprising number of kept overrides deserves a look before proceeding.

- [ ] **Step 5: Merge, deploy production, apply**

1. Confirm the Phase 0 backup exists and note its id.
2. Merge the branch to `main`. Staging redeploys automatically; confirm it is healthy.
3. Set staging's branch back to `main`.
4. Trigger the production deploy in Dokploy.
5. `curl -i https://sites.katek.app/healthz` expects 200.
6. Run `python manage.py reconcile_content --apply` in the production terminal.
7. Spot-check two sites' public renders against what they showed before.

- [ ] **Step 6: Verify the original incident is closed**

Re-annotate `hodges-advisory.html` and push it to `denny`. The client editor should come back with sections, and the public page should show the annotated content rather than reverting to the pre-incident copy.

---

## Out of scope, deliberately

- **Changing `editor.js` to send patches instead of the full blob.** Task 6 prunes server-side, which makes the wire format an optimisation rather than a correctness requirement. Worth doing later; not needed for this fix.
- **Per-field provenance** (a marker recording who authored each value). More exact than equality-based inference, but it needs new state and its own migration. Equality cannot prove authorship, which is why Task 8's dry run exists: a human reads the report before anything is written.
- **Type-change compatibility.** If a surviving field id changes `data-type` between template versions, an old value is reinterpreted under the new type. Worth a warning in a later pass.

## Self-review notes

- Every codex review point is covered: point 1 by Task 1, point 2 by Task 2, point 3 by Task 3, points 4 and 6 by Tasks 4 and 6, point 5 by Task 5, point 7 by Task 7, plus the version-allocation race in Task 3 and the annotator sync in Task 2 Step 5.
- The one thing Task 8 cannot prove is authorship. A value equal to the old default is assumed to be seeded rather than typed. A client who deliberately typed a string identical to the template default loses that override and falls back to the same visible text, so the render is unchanged either way.
