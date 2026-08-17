# Content Overrides and HTML Save Fixes Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an agency HTML re-upload actually reach the visitor, stop the dashboard reporting success for writes that changed nothing, and never silently discard a value a client authored.

**Architecture:** Two releases. Release 1 is four self-contained save-path fixes that touch no stored data. Release 2 introduces **explicit authorship**: `Tenant.content` / `Page.content` store only fields the client actually edited, tracked in a sticky `_authored` meta key, so a template's new defaults flow through every field the client never touched while authored values are frozen forever.

**Tech Stack:** Django 5.1.2 on Python 3.12, PostgreSQL in prod / SQLite locally, BeautifulSoup + lxml, no new dependencies.

---

## Revision history

**v1 was reviewed and rejected** (`gpt-5.6-sol` at high effort, verdict "do not ship"). The review is at `scratchpad/codex-plan-review.md`. v2 exists because of it. Four errors it found, all since verified against the code:

1. v1's no-op early return discarded operator metadata edits (`dashboard/views.py:243-247` mutates `name`/`description`/`editing_mode` in memory and relies on `save_template_version` to persist them).
2. v1's Phase 3 ran `reconcile_content` on production *before* deploying the code that contains it. Impossible.
3. v1's invariant contradicted itself: reconciliation pruned against **old** defaults while the autosave and the migration pruned against **current** defaults, so a template default converging on an earlier client edit silently reclassified it.
4. v1 claimed full coverage of the first review. It missed the MCP `if_match` race, `page_edit_html`'s unconditional success message, admin write paths, and legacy `ContentVersion.snapshot` rows.

v1 also mispredicted the test fallout. See "Expected test fallout" below for the corrected list.

## The semantics decision (settled)

**Authored values freeze forever.** A field the client explicitly edited keeps its value across every subsequent template change, even if a later template default happens to equal it. A field the client never edited follows the template.

Equality alone cannot express this, which is what broke v1. Authorship is therefore stored explicitly.

**Mechanism:** a meta key `_authored`, a sorted list of dotted `section.field` ids the client has set. It is a meta key, so it is never pruned and never treated as a section. Membership is **sticky**: once an id is in `_authored` it stays until the client resets that field.

Equality is used exactly once, in the one-time migration, to infer authorship for rows that predate the mechanism. After that it is never used again.

**What this buys over equality-based pruning:** a template default that later converges on a client's value cannot silently reclassify it, because membership does not depend on the value.

**What it cannot do:** the migration's inference is "differs from the *old* default". A site already masked by a historical template default change holds seeded values that differ from today's default; those will be misread as authored and will keep masking. The migration cannot fix that and must not claim to. It emits an exception report for a human instead (Task 9).

## Invariant

Stored content holds:

- `_authored`: sorted list of dotted ids the client has set.
- `content[section][field]` for exactly the ids in `_authored`, plus **dormant** ids (an authored id whose field no longer exists in the schema, kept so a template swap-back or field-loss confirmation restores it).
- Other meta keys (`_styles`, `_global`, `_tokens`, `_hidden`) verbatim.

Nothing else. `merge_with_defaults` remains the single display merge.

---

## Release 1: save-path correctness

No stored data changes. Shippable on its own. This is what closes the incident.

### Task 1: Reject blank HTML, preserving what the operator typed

**Files:**
- Modify: `dashboard/views.py` (`template_detail`)
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
    '<p data-edit="hero.sub" data-type="text">Old sub</p>'
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
        self.tpl = Template.objects.create(name="T", description="d", html_source=OLD)

    def _post(self, **extra):
        data = {"name": "Renamed", "description": "new desc",
                "editing_mode": "editable"}
        data.update(extra)
        return self.client.post(f"/dashboard/templates/{self.tpl.pk}/", data)

    def test_missing_html_source_is_rejected(self):
        before = self.tpl.versions.count()
        resp = self._post()
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)
        self.assertEqual(self.tpl.versions.count(), before)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn("HTML source cannot be empty.", msgs)

    def test_whitespace_only_html_source_is_rejected(self):
        resp = self._post(html_source="   \n\t  ")
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)

    def test_rejection_does_not_persist_metadata(self):
        self._post()
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.name, "T")
        self.assertEqual(self.tpl.description, "d")

    def test_rejection_redisplays_what_the_operator_typed(self):
        resp = self._post()
        body = resp.content.decode()
        self.assertIn("Renamed", body)
        self.assertIn("new desc", body)
        self.assertIn("Old headline", body)   # html_value falls back to the stored HTML
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.BlankHtmlRejectedTest -v 2
```

Expected: 4 failures. Today the view returns 302 and appends a version.

- [ ] **Step 3: Implement**

In `dashboard/views.py`, `template_detail`, the POST branch currently opens by mutating the model. Replace the opening of that branch so validation happens **before** any mutation:

```python
    if request.method == "POST":
        html_source = request.POST.get("html_source") or ""
        if not html_source.strip():
            # A blank field is never "keep the current HTML". That fallback is
            # what turned failed uploads into successful-looking no-ops.
            messages.error(request, "HTML source cannot be empty.")
            return render(
                request,
                "dashboard/template_form.html",
                {
                    "template": template,
                    "tenants_using": list(
                        template.tenants.only("id", "name", "subdomain").order_by("name")
                    ),
                    # Bound data, so the operator does not lose what they typed.
                    "form_data": request.POST,
                    # ...except the HTML itself, which is what they failed to supply.
                    "html_value": template.html_source,
                },
                status=400,
            )

        template.name = (request.POST.get("name") or template.name).strip()
        template.description = (request.POST.get("description") or "").strip()
        mode = (request.POST.get("editing_mode") or "").strip()
        if mode in {Template.EDITING_RAW, Template.EDITING_EDITABLE}:
            template.editing_mode = mode
        allow_field_loss = request.POST.get("allow_field_loss") in (
            "1", "true", "on", "yes",
        )
```

`request.POST` is safe to pass as `form_data` here because the browser submits every named input, per the `form_data` warning in `CLAUDE.md`.

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.BlankHtmlRejectedTest -v 2
```

Expected: 4 tests, OK. (`test_rejection_redisplays_what_the_operator_typed` needs Task 2's `html_value` wiring; if it fails on that alone, do Task 2 and re-run.)

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py core/tests/test_html_save_guards.py
git commit -m "fix(dashboard): reject blank html_source instead of silently re-saving"
```

### Task 2: Keep the operator's candidate on a field-loss conflict

This is the defect that produced the incident.

**Files:**
- Modify: `templates/dashboard/template_form.html:101`, `dashboard/views.py` (every render of that template), `templates/dashboard/_html_source_editor.html:248-253`
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

Expected: `test_conflict_page_shows_the_submitted_html_not_the_old_one` fails on `'Unannotated rewrite' not found`.

- [ ] **Step 3: Implement**

In `templates/dashboard/template_form.html`, replace line 101:

```django
        {% include "dashboard/_html_source_editor.html" with html_value=template.html_source|default:form_data.html_source|default_if_none:'' %}
```

with:

```django
        {% include "dashboard/_html_source_editor.html" with html_value=html_value|default_if_none:'' %}
```

Then supply `html_value` at **every** render of `template_form.html` in `dashboard/views.py`. There are five:

| Location | `html_value` |
|---|---|
| `template_create` GET | `STARTER_TEMPLATE_HTML` |
| `template_create` POST, missing-name/HTML branch | `request.POST.get("html_source") or ""` |
| `template_create` POST, name-too-long branch | `request.POST.get("html_source") or ""` |
| `template_detail` GET | `template.html_source` |
| `template_detail` POST, `FieldLossError` branch | `html_source` (the candidate) |

Task 1 adds a sixth (the blank rejection); it is already written above.

Leave `form_data` in place for the other fields.

- [ ] **Step 4: Run it and watch it pass**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards -v 2
```

Expected: 6 tests, OK.

- [ ] **Step 5: Fix the annotator's stale-editor sync**

`templates/dashboard/_html_source_editor.html:248-253` assigns `textarea.value` without notifying CodeMirror. `frontend/code-editor.js:69-74` listens for `input`. Add the dispatch immediately after the assignment:

```javascript
      textarea.value = annotated;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
```

- [ ] **Step 6: Commit**

```bash
git add dashboard/views.py templates/dashboard/template_form.html \
        templates/dashboard/_html_source_editor.html core/tests/test_html_save_guards.py
git commit -m "fix(dashboard): keep submitted HTML on field-loss conflict re-render"
```

### Task 3: One lock, honest no-op, no lost metadata

**Files:**
- Modify: `core/services/templates.py`, `dashboard/views.py` (`template_detail`, `page_edit_html`, `_annotate_template_in_background`), `api/mcp/tools.py` (`push_page`, `_push_html_onto_template`)
- Test: `core/tests/test_html_save_guards.py`

Everything the write decision depends on must be read from the locked row. v1 computed `old_fields`, `lost` and `affected` from a possibly stale caller object, then wrote that stale object.

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

    def test_version_is_cut_when_latest_does_not_archive_current_bytes(self):
        # Admin/direct Template.save() can move html_source without a version.
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        self.tpl.html_source = NEW_SAME_FIELDS
        self.tpl.save()
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(
            self.tpl, NEW_SAME_FIELDS, user=self.user,
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before + 1)

    def test_changed_html_still_creates_a_version(self):
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(
            self.tpl, NEW_SAME_FIELDS, user=self.user,
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before + 1)


@override_settings(STORAGES=PLAIN_STATIC)
class NoOpKeepsMetadataTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops4", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.tpl = Template.objects.create(name="Before", description="", html_source=OLD)

    def test_unchanged_html_still_saves_a_rename(self):
        resp = self.client.post(
            f"/dashboard/templates/{self.tpl.pk}/",
            {"name": "After", "description": "now described",
             "editing_mode": "editable", "html_source": OLD},
        )
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.tpl.name, "After")
        self.assertEqual(self.tpl.description, "now described")
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn("No HTML changes — metadata saved.", msgs)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.NoOpVersionTest core.tests.test_html_save_guards.NoOpKeepsMetadataTest -v 2
```

Expected: failures on the missing `unchanged` attribute.

- [ ] **Step 3: Implement the service**

In `core/services/templates.py`, add `unchanged: bool = False` to `SaveTemplateResult`, then replace the body of `save_template_version` with:

```python
    with transaction.atomic():
        # Everything the write decision depends on is read from the locked row.
        # Reading from the caller's possibly-stale instance let a writer validate
        # against A while overwriting B.
        locked = Template.objects.select_for_update().get(pk=template.pk)

        old_fields = _dotted_field_ids(locked.schema)
        new_schema = build_schema(html_source)
        lost = old_fields - _dotted_field_ids(new_schema)

        affected: list[dict[str, Any]] = []
        if lost:
            affected = _affected_published(locked, lost)
            if affected and not allow_field_loss:
                raise FieldLossError(lost, affected)

        latest = locked.versions.order_by("-number").first()
        # Only treat this as a no-op when the latest version genuinely archives
        # the bytes that are live. A direct Template.save() (admin) can move
        # html_source without cutting a version.
        if (
            latest is not None
            and latest.html_source == locked.html_source == html_source
        ):
            _sync(template, locked)
            return SaveTemplateResult(
                template=template,
                version=latest,
                lost_fields=set(),
                affected=[],
                unchanged=True,
            )

        locked.html_source = html_source
        locked.save()  # re-derives schema
        version = TemplateVersion.objects.create(
            template=locked,
            number=(latest.number if latest is not None else 0) + 1,
            html_source=locked.html_source,
            schema=locked.schema or {},
            label=label or "",
            saved_by=user if getattr(user, "pk", None) else None,
        )
        _sync(template, locked)

    return SaveTemplateResult(
        template=template,
        version=version,
        lost_fields=lost,
        affected=affected,
    )
```

`FieldLossError` is raised inside `atomic()`. It propagates out and rolls the block back, which is correct: nothing was written yet. Add the helper above `save_template_version`:

```python
def _sync(caller: Template, locked: Template) -> None:
    """Copy the committed state back onto the caller's instance so callers that
    keep using their object (and the dashboard's message logic) see the truth."""
    caller.html_source = locked.html_source
    caller.schema = locked.schema
```

**Note for the implementer:** `template_detail` mutates `name`/`description`/`editing_mode` on its own instance before calling this. Those must be persisted separately now (next step) rather than riding on the service's `template.save()`.

- [ ] **Step 4: Implement the callers**

`dashboard/views.py`, `template_detail`, replacing the current call and success message:

```python
        try:
            result = template_svc.save_template_version(
                template,
                html_source,
                user=request.user,
                allow_field_loss=allow_field_loss,
            )
        except template_svc.FieldLossError as exc:
            ...  # unchanged except for html_value, see Task 2
        # Metadata is edited on this instance and is no longer persisted by the
        # service on a no-op, so save it explicitly either way.
        template.save(update_fields=["name", "description", "editing_mode", "updated_at"])
        if result.unchanged:
            messages.info(request, "No HTML changes — metadata saved.")
        else:
            messages.success(request, "Template updated.")
        return redirect("dashboard:template_detail", pk=template.pk)
```

`dashboard/views.py`, `page_edit_html`, replacing the unconditional success at line 2084:

```python
            if result.unchanged:
                messages.info(request, f"No HTML changes for “{page.title}”.")
            else:
                messages.success(request, f"HTML updated for “{page.title}”.")
```

with `result = template_svc.save_template_version(...)` capturing the return value.

`dashboard/views.py`, `_annotate_template_in_background`, after the service call:

```python
        if result.unchanged:
            logger.info("Sibling annotation returned unchanged HTML for template=%s", template_id)
            return
```

`api/mcp/tools.py`, `_push_html_onto_template`: delete the pre-transaction `if_match` comparison and pass the expected etag into the service instead. Add a parameter to `save_template_version`:

```python
def save_template_version(
    template: Template,
    html_source: str,
    *,
    user,
    allow_field_loss: bool = False,
    label: str = "",
    expect_html_etag: Optional[str] = None,
) -> SaveTemplateResult:
```

and immediately after `locked = ...` inside the transaction:

```python
        if expect_html_etag is not None:
            import hashlib
            current = hashlib.sha256((locked.html_source or "").encode("utf-8")).hexdigest()
            if current != expect_html_etag:
                raise ConcurrentWriteError(
                    "Conflict (409): template has changed since if_match. "
                    "Re-read and retry with the current etag."
                )
```

Define `class ConcurrentWriteError(Exception)` next to `FieldLossError`, and have `_push_html_onto_template` catch it and return `tool_conflict(str(exc))`. This closes the time-of-check/time-of-use gap: the comparison now happens under the same lock as the write.

Add `"unchanged": result.unchanged` to both `tool_success` payloads in `push_page`.

- [ ] **Step 5: Run the full suite**

```bash
.venv-3.12/bin/python manage.py test 2>&1 | tail -30
```

See "Expected test fallout". Anything not on that list is a regression: read it, do not paper over it.

- [ ] **Step 6: Commit**

```bash
git add core/services/templates.py dashboard/views.py api/mcp/tools.py \
        core/tests/test_html_save_guards.py
git commit -m "fix(templates): single-lock write path, honest no-op, if_match under the lock"
```

### Task 4: Close the admin bypass

**Files:**
- Modify: `core/admin.py`
- Test: `core/tests/test_html_save_guards.py`

`TemplateAdmin.readonly_fields` is `("schema", "created_at", "updated_at")`, so `html_source` is editable and a save there skips versioning, field-loss checks and the etag discipline entirely. `TenantAdmin` and `PageAdmin` leave `content` editable, which will break the Release 2 invariant.

- [ ] **Step 1: Write the failing test**

```python
class AdminCannotBypassTheServiceTest(TestCase):
    def test_template_html_source_is_readonly_in_admin(self):
        from django.contrib import admin as dj_admin
        from core.models import Page, Template, Tenant

        self.assertIn("html_source", dj_admin.site._registry[Template].readonly_fields)
        self.assertIn("content", dj_admin.site._registry[Tenant].readonly_fields)
        self.assertIn("content", dj_admin.site._registry[Page].readonly_fields)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards.AdminCannotBypassTheServiceTest -v 2
```

- [ ] **Step 3: Implement**

In `core/admin.py`, add `"html_source"` to `TemplateAdmin.readonly_fields` and `"content"` to `TenantAdmin.readonly_fields` and `PageAdmin.readonly_fields`, adding the attribute where it does not exist yet. Add a one-line comment on each pointing at `core/services/templates.py` as the supported write path.

- [ ] **Step 4: Run it and watch it pass, then commit**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_html_save_guards -v 2
git add core/admin.py core/tests/test_html_save_guards.py
git commit -m "fix(admin): make html_source and content read-only so writes go through the services"
```

---

## Release 2: explicit authorship

Do not start until Release 1 is deployed and the production dry run in Release 2 Phase B has been reviewed.

### Task 5: The authorship module

**Files:**
- Create: `core/services/content_overrides.py`
- Test: `core/tests/test_content_overrides.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_content_overrides.py`:

```python
from django.test import SimpleTestCase

from core.services.content_overrides import (
    AUTHORED_KEY, authored_ids, infer_authored, is_untouched,
    prune_to_authored, record_authored,
)

DEFAULTS = {
    "hero": {"title": "Old headline", "cta": "#contact"},
    "about": {"body": "Old body"},
}


class PruneToAuthoredTest(SimpleTestCase):
    def test_unauthored_fields_are_dropped_even_when_they_differ(self):
        content = {"hero": {"title": "seeded or stale"}, AUTHORED_KEY: []}
        self.assertEqual(prune_to_authored(content), {AUTHORED_KEY: []})

    def test_authored_fields_survive_even_when_equal_to_a_default(self):
        content = {"hero": {"title": "Old headline"}, AUTHORED_KEY: ["hero.title"]}
        self.assertEqual(prune_to_authored(content), content)

    def test_meta_keys_pass_through(self):
        content = {
            "hero": {"title": "x"},
            AUTHORED_KEY: ["hero.title"],
            "_hidden": ["about"], "_styles": {"hero.title": {"color": "#fff"}},
            "_global": {}, "_tokens": {"blue": "#2A5EB0"},
        }
        self.assertEqual(prune_to_authored(content), content)

    def test_missing_authored_key_means_nothing_is_authored(self):
        self.assertEqual(prune_to_authored({"hero": {"title": "x"}}), {})

    def test_input_is_not_mutated(self):
        content = {"hero": {"title": "x"}, AUTHORED_KEY: []}
        prune_to_authored(content)
        self.assertEqual(content, {"hero": {"title": "x"}, AUTHORED_KEY: []})


class RecordAuthoredTest(SimpleTestCase):
    def test_a_changed_field_becomes_authored(self):
        out = record_authored(
            previous={"hero": {"title": "a"}},
            incoming={"hero": {"title": "b"}},
            already=["about.body"],
        )
        self.assertEqual(out, ["about.body", "hero.title"])

    def test_membership_is_sticky(self):
        out = record_authored(
            previous={"hero": {"title": "b"}},
            incoming={"hero": {"title": "b"}},
            already=["hero.title"],
        )
        self.assertEqual(out, ["hero.title"])

    def test_meta_keys_never_become_authored(self):
        out = record_authored(
            previous={}, incoming={"_hidden": ["x"]}, already=[],
        )
        self.assertEqual(out, [])


class InferAuthoredTest(SimpleTestCase):
    def test_values_differing_from_the_old_default_are_inferred_authored(self):
        content = {"hero": {"title": "Client wrote this", "cta": "#contact"}}
        self.assertEqual(infer_authored(content, DEFAULTS), ["hero.title"])

    def test_fields_with_no_default_are_inferred_authored(self):
        content = {"gone": {"headline": "from an older template"}}
        self.assertEqual(infer_authored(content, DEFAULTS), ["gone.headline"])

    def test_a_fully_seeded_blob_infers_nothing(self):
        content = {"hero": {"title": "Old headline", "cta": "#contact"},
                   "about": {"body": "Old body"}}
        self.assertEqual(infer_authored(content, DEFAULTS), [])


class IsUntouchedTest(SimpleTestCase):
    def test_no_authored_ids_means_untouched(self):
        self.assertTrue(is_untouched({}))
        self.assertTrue(is_untouched({AUTHORED_KEY: [], "_hidden": []}))

    def test_any_authored_id_means_touched(self):
        self.assertFalse(is_untouched({AUTHORED_KEY: ["hero.title"]}))
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_overrides -v 2
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `core/services/content_overrides.py`:

```python
"""Explicit authorship for Tenant.content / Page.content.

A field the client edited keeps its value across every later template change.
A field they never edited follows the template. Equality cannot express that:
a template default can later converge on a value the client authored, and an
equality rule would then silently reclassify it as "following the template".

So authorship is stored, not inferred. ``_authored`` holds the dotted ids the
client has set. Membership is sticky: it survives the value later matching a
default. Equality is used exactly once, by ``infer_authored``, to bootstrap
rows that predate this mechanism.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

AUTHORED_KEY = "_authored"


def _is_meta(key: Any) -> bool:
    return isinstance(key, str) and key.startswith("_")


def authored_ids(content: dict[str, Any] | None) -> list[str]:
    raw = (content or {}).get(AUTHORED_KEY)
    if not isinstance(raw, (list, tuple)):
        return []
    return sorted({x for x in raw if isinstance(x, str) and "." in x})


def _flatten(content: dict[str, Any] | None) -> dict[str, Any]:
    """{"hero": {"title": "x"}} -> {"hero.title": "x"}, meta keys excluded."""
    out: dict[str, Any] = {}
    for section_id, section in (content or {}).items():
        if _is_meta(section_id) or not isinstance(section, dict):
            continue
        for field, value in section.items():
            out[f"{section_id}.{field}"] = value
    return out


def prune_to_authored(content: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only authored fields, plus every meta key verbatim."""
    keep = set(authored_ids(content))
    out: dict[str, Any] = {}
    for section_id, section in (content or {}).items():
        if _is_meta(section_id):
            out[section_id] = deepcopy(section)
            continue
        if not isinstance(section, dict):
            continue
        kept = {
            field: deepcopy(value)
            for field, value in section.items()
            if f"{section_id}.{field}" in keep
        }
        if kept:
            out[section_id] = kept
    return out


def record_authored(
    *,
    previous: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
    already: Iterable[str],
) -> list[str]:
    """Sticky union of ``already`` with every field whose value changed."""
    before = _flatten(previous)
    after = _flatten(incoming)
    changed = {
        field_id for field_id, value in after.items()
        if field_id not in before or before[field_id] != value
    }
    return sorted(set(already) | changed)


def infer_authored(
    content: dict[str, Any] | None, defaults: dict[str, Any] | None
) -> list[str]:
    """One-time bootstrap: a stored value that differs from its default (or has
    no default) is assumed authored. This is the only place equality is used,
    and it cannot see values that matched a *historical* default."""
    out: list[str] = []
    for field_id, value in _flatten(content).items():
        section_id, field = field_id.split(".", 1)
        section_defaults = (defaults or {}).get(section_id)
        if not isinstance(section_defaults, dict) or field not in section_defaults:
            out.append(field_id)
        elif section_defaults[field] != value:
            out.append(field_id)
    return sorted(out)


def is_untouched(content: dict[str, Any] | None) -> bool:
    """True when the client has authored nothing."""
    return not authored_ids(content)
```

- [ ] **Step 4: Run it and watch it pass, then commit**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_overrides -v 2
git add core/services/content_overrides.py core/tests/test_content_overrides.py
git commit -m "feat(content): explicit authorship module"
```

### Task 6: Maintain authorship on every write path

**Files:**
- Modify: `dashboard/views.py` (`_save_content`), `api/mcp/tools.py` (`patch_content`), `core/services/accounts.py`, `core/services/content_versions.py`
- Test: `core/tests/test_content_authorship.py`

Under A there is **no reconciliation on template change**. If content only ever holds authored fields, a new template's defaults reach every other field automatically. That removes v1's cross-row fan-out transaction and the lock-ordering hazard with it.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_content_authorship.py`:

```python
import json

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site
from core.services import templates as template_svc
from core.services.content_overrides import AUTHORED_KEY

OLD = (
    '<html><body>'
    '<section data-section="hero" data-label="Hero">'
    '<h1 data-edit="hero.title" data-type="text">Old headline</h1>'
    '<p data-edit="hero.sub" data-type="text">Old sub</p>'
    '</section></body></html>'
)
NEW = OLD.replace("Old headline", "New headline").replace("Old sub", "New sub")

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class AuthorshipTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        owner = User.objects.create_user("client", password="pw")
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        self.tenant = Tenant.objects.create(
            name="Denny", subdomain="denny", template=self.tpl, owner=owner,
            content={}, is_published=True,
        )

    def _autosave(self, content):
        return self.client.post(
            f"/dashboard/sites/{self.tenant.pk}/save/",
            data=json.dumps({"content": content}),
            content_type="application/json",
        )

    def test_new_tenant_starts_empty(self):
        self.assertEqual(self.tenant.content, {})

    def test_autosave_of_the_full_blob_stores_only_what_changed(self):
        merged = merge_with_defaults(build_schema(OLD), {})
        merged["hero"]["title"] = "Client wrote this"
        self.assertEqual(self._autosave(merged).status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(
            self.tenant.content,
            {"hero": {"title": "Client wrote this"}, AUTHORED_KEY: ["hero.title"]},
        )

    def test_unedited_fields_follow_a_new_template(self):
        merged = merge_with_defaults(build_schema(OLD), {})
        merged["hero"]["title"] = "Client wrote this"
        self._autosave(merged)
        template_svc.save_template_version(
            self.tpl, NEW, user=self.staff, allow_field_loss=True,
        )
        self.tenant.refresh_from_db()
        html = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        self.assertIn("Client wrote this", html)   # authored, frozen
        self.assertIn("New sub", html)             # never edited, follows template
        self.assertNotIn("Old sub", html)

    def test_a_converging_default_does_not_reclassify_an_authored_field(self):
        merged = merge_with_defaults(build_schema(OLD), {})
        merged["hero"]["title"] = "New headline"   # equals the FUTURE default
        self._autosave(merged)
        template_svc.save_template_version(
            self.tpl, NEW, user=self.staff, allow_field_loss=True,
        )
        # A later template change must not move this field.
        later = NEW.replace("New headline", "Third headline")
        template_svc.save_template_version(
            self.tpl, later, user=self.staff, allow_field_loss=True,
        )
        self.tenant.refresh_from_db()
        self.assertIn("hero.title", self.tenant.content[AUTHORED_KEY])
        html = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        self.assertIn("New headline", html)
        self.assertNotIn("Third headline", html)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_authorship -v 2
```

- [ ] **Step 3: Implement**

`dashboard/views.py`, in `_save_content`, immediately after `_normalize_styles(content)`:

```python
    # The editor posts the whole merged blob on every autosave. Keep only what
    # the client has actually authored, so a later HTML upload reaches every
    # other field.
    previous = merge_with_defaults(editable.template.schema or {}, editable.content)
    content[AUTHORED_KEY] = record_authored(
        previous=previous,
        incoming=content,
        already=authored_ids(editable.content),
    )
    content = prune_to_authored(content)
```

with `from core.services.content_overrides import (AUTHORED_KEY, authored_ids, prune_to_authored, record_authored)` added to the imports.

`api/mcp/tools.py`, in `patch_content`, after `new_content = content_mod.write_field(...)`:

```python
    new_content[AUTHORED_KEY] = sorted(set(authored_ids(stored)) | {field})
    new_content = prune_to_authored(new_content)
```

`core/services/accounts.py`, replace the seeding at line 127:

```python
            content={},
```

and at line 163:

```python
            tenant.content = {}
            tenant.save(update_fields=["content", "updated_at"])
```

`core/services/content_versions.py`, in `restore_tenant_content`, prune the snapshot so a legacy fat snapshot cannot reintroduce the masking bug:

```python
        tenant.content = prune_to_authored(deepcopy(version.snapshot or {}))
```

Leave `save_tenant_content` alone; its callers prune before calling it, and pruning inside would rewrite an MCP caller's etag mid-transaction.

- [ ] **Step 4: Run it and watch it pass, then commit**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_authorship -v 2
git add dashboard/views.py api/mcp/tools.py core/services/accounts.py \
        core/services/content_versions.py core/tests/test_content_authorship.py
git commit -m "feat(content): track authorship on every write path"
```

### Task 7: Rewrite the publish guard

**Files:**
- Modify: `api/mcp/tools.py:217-242`
- Test: whichever `api/tests` module covers `publish_site` (find with `grep -rl "_content_still_template_defaults\|publish_site" api/tests`)

- [ ] **Step 1: Write the failing test**

```python
def test_untouched_site_is_still_untouched_after_a_template_update(self):
    template_svc.save_template_version(
        self.tenant.template, NEW, user=self.staff, allow_field_loss=True,
    )
    self.tenant.refresh_from_db()
    self.assertTrue(_content_still_template_defaults(self.tenant))

def test_an_authored_field_makes_it_touched(self):
    self.tenant.content = {"hero": {"title": "x"}, "_authored": ["hero.title"]}
    self.tenant.save(update_fields=["content"])
    self.assertFalse(_content_still_template_defaults(self.tenant))

def test_meta_only_content_is_still_untouched(self):
    self.tenant.content = {"_hidden": ["hero"]}
    self.tenant.save(update_fields=["content"])
    self.assertTrue(_content_still_template_defaults(self.tenant))
```

- [ ] **Step 2: Run it and watch it fail**

The current implementation compares stored old defaults against the new schema's defaults, so the first test fails after any template update.

- [ ] **Step 3: Implement**

Replace `_content_still_template_defaults` in `api/mcp/tools.py` with:

```python
def _content_still_template_defaults(tenant: Tenant) -> bool:
    """True when the client has authored nothing.

    The old blob comparison drifted after any template update: it compared the
    defaults it had stored against the *new* schema's defaults, so an untouched
    site started reading as edited.
    """
    return is_untouched(tenant.content)
```

Add `from core.services.content_overrides import is_untouched` and delete the unused `_public` helper.

- [ ] **Step 4: Run, then commit**

```bash
.venv-3.12/bin/python manage.py test api.tests -v 2 2>&1 | tail -20
git add api/mcp/tools.py api/tests/
git commit -m "fix(mcp): base the publish guard on authorship"
```

### Task 8: The migration command

**Files:**
- Create: `core/management/commands/reconcile_content.py`
- Test: `core/tests/test_content_authorship.py`

Migrates legacy fat rows **and** legacy `ContentVersion.snapshot` rows. Concurrency-safe: each row is refetched under `select_for_update()` and its `updated_at` verified, in bounded batches.

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_content_authorship.py`:

```python
from io import StringIO

from django.core.management import call_command

from core.models import ContentVersion


class ReconcileCommandTest(TestCase):
    def setUp(self):
        owner = User.objects.create_user("c", password="pw")
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        seeded = merge_with_defaults(build_schema(OLD), {})
        seeded["hero"]["title"] = "Client wrote this"
        self.tenant = Tenant.objects.create(
            name="D", subdomain="d", template=self.tpl, owner=owner,
            content=seeded, is_published=True,
        )
        ContentVersion.objects.create(
            tenant=self.tenant,
            snapshot=merge_with_defaults(build_schema(OLD), {}),
            source="dashboard",
        )

    def test_dry_run_reports_but_does_not_write(self):
        out = StringIO()
        call_command("reconcile_content", stdout=out)
        self.tenant.refresh_from_db()
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("Old sub", str(self.tenant.content))

    def test_apply_keeps_the_authored_field_and_drops_the_seeded_ones(self):
        call_command("reconcile_content", "--apply", stdout=StringIO())
        self.tenant.refresh_from_db()
        self.assertEqual(
            self.tenant.content,
            {"hero": {"title": "Client wrote this"}, AUTHORED_KEY: ["hero.title"]},
        )

    def test_apply_canonicalises_legacy_snapshots(self):
        call_command("reconcile_content", "--apply", stdout=StringIO())
        snap = ContentVersion.objects.get(tenant=self.tenant).snapshot
        self.assertEqual(snap.get(AUTHORED_KEY), [])

    def test_apply_is_idempotent(self):
        call_command("reconcile_content", "--apply", stdout=StringIO())
        self.tenant.refresh_from_db()
        first = dict(self.tenant.content)
        out = StringIO()
        call_command("reconcile_content", stdout=out)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, first)
        self.assertIn("0 row(s) would change", out.getvalue())
```

- [ ] **Step 2: Run it and watch it fail**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_authorship.ReconcileCommandTest -v 2
```

Expected: `CommandError: Unknown command: 'reconcile_content'`.

- [ ] **Step 3: Implement**

Create `core/management/commands/reconcile_content.py`:

```python
"""Bootstrap explicit authorship on rows that predate it.

Dry run by default. For each Tenant / Page it infers which stored fields the
client authored (value differs from the current template default, or has no
default), writes that into ``_authored``, and drops the rest.

It CANNOT see a value that matched a *historical* default: such a value differs
from today's default and is kept as authored. Those rows are listed in the
exception report for a human to check.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import ContentVersion, Page, Tenant
from core.services.content_overrides import (
    AUTHORED_KEY, infer_authored, prune_to_authored,
)

BATCH = 100


class Command(BaseCommand):
    help = "Bootstrap _authored and drop unauthored stored fields."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Write. Without this the command only reports.")
        parser.add_argument("--subdomain", default="",
                            help="Limit to one tenant subdomain and its pages.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        only = (options["subdomain"] or "").strip()

        targets = []
        tq = Tenant.objects.only("pk", "subdomain")
        pq = Page.objects.select_related("tenant").only("pk", "slug", "tenant__subdomain")
        if only:
            tq = tq.filter(subdomain=only)
            pq = pq.filter(tenant__subdomain=only)
        targets += [(Tenant, t.pk, f"site:{t.subdomain}") for t in tq]
        targets += [(Page, p.pk, f"page:{p.tenant.subdomain}/{p.slug}") for p in pq]

        changed = 0
        exceptions = []

        for start in range(0, len(targets), BATCH):
            for model, pk, label in targets[start:start + BATCH]:
                with transaction.atomic():
                    row = (
                        model.objects.select_for_update()
                        .select_related("template")
                        .get(pk=pk)
                    )
                    if row.template_id is None:
                        self.stdout.write(f"{label}: SKIP (no template)")
                        continue
                    defaults = (row.template.schema or {}).get("defaults") or {}
                    before = row.content or {}
                    if AUTHORED_KEY in before:
                        continue  # already migrated
                    inferred = infer_authored(before, defaults)
                    after = prune_to_authored({**before, AUTHORED_KEY: inferred})

                    dropped = sum(
                        len(v) for k, v in before.items()
                        if not k.startswith("_") and isinstance(v, dict)
                    ) - len(inferred)
                    self.stdout.write(
                        f"{label}: drop {dropped} -> authored {len(inferred)}"
                    )
                    if inferred:
                        self.stdout.write(f"    authored: {', '.join(inferred)}")
                        exceptions.append((label, inferred))

                    if after != before:
                        changed += 1
                        if apply_changes:
                            row.content = after
                            row.save(update_fields=["content", "updated_at"])

                    if apply_changes and model is Tenant:
                        for cv in ContentVersion.objects.select_for_update().filter(
                            tenant_id=pk
                        ):
                            snap = cv.snapshot or {}
                            if AUTHORED_KEY in snap:
                                continue
                            cv.snapshot = prune_to_authored({
                                **snap,
                                AUTHORED_KEY: infer_authored(snap, defaults),
                            })
                            cv.save(update_fields=["snapshot"])

        mode = "APPLIED" if apply_changes else "DRY RUN (use --apply to write)"
        self.stdout.write(f"\n{mode}: {changed} row(s) would change.")
        if exceptions:
            self.stdout.write(
                "\nEXCEPTION REPORT — these were inferred as client-authored. "
                "A value that matched a HISTORICAL template default is "
                "indistinguishable from a real edit here and will keep masking "
                "new HTML. Review before applying:"
            )
            for label, ids in exceptions:
                self.stdout.write(f"  {label}: {', '.join(ids)}")
```

- [ ] **Step 4: Run it and watch it pass, then commit**

```bash
.venv-3.12/bin/python manage.py test core.tests.test_content_authorship -v 2
git add core/management/commands/reconcile_content.py core/tests/test_content_authorship.py
git commit -m "feat(content): reconcile_content bootstraps authorship with an exception report"
```

---

## Expected test fallout

From the v2 review, verified against the code. Anything **not** on this list is a regression.

| Test | Expectation |
|---|---|
| `core/tests/test_account_services.py:41` `test_creates_tenant_account_without_request` | **Will fail.** Asserts `tenant.content == schema["defaults"]`. Change to `{}`. This is the intended behaviour change |
| `test_template_service.py::FieldLossGuardTests::test_published_field_loss_allowed_with_flag_preserves_content` | **Must keep passing.** `hero.sub` differs from its default, so it is inferred authored and preserved. A failure is a regression |
| `test_tenant_template_swap.py::TenantTemplateSwapTests::test_swap_does_not_wipe_tenant_content` | **Must keep passing.** Exercises `assign_template`, not the HTML save path |
| `test_template_service.py::TemplateVersionServiceTests::test_save_appends_contiguous_numbers` | **Must keep passing.** No versions exist before the first call, so v1 is cut, then v2 |
| 4 template-render tests | Pre-existing `Missing staticfiles manifest entry for 'css/dashboard.css'`. Not caused by this work |

Baseline before starting: 67 passed, 4 errored across the seven relevant modules.

## Rollout

Two releases, per the v2 review. Never restore a production dump into staging (`deploy/STAGING.md:84`). Staging tracks `main` with autoDeploy on; production tracks `main` with autoDeploy off.

### Phase A: Release 1

- [ ] Point staging at this branch in Dokploy (`cms-dashboard` / `staging` / `MzPbhgIhn6vzLJWBH5Clq`), redeploy, `seed_demo_data`.
- [ ] Walk the incident on staging: paste HTML that drops fields, confirm the 409 page still shows **your** HTML, confirm with "save anyway", confirm the new HTML is live. Re-save unchanged and expect "No HTML changes — metadata saved." with no new version. Rename with unchanged HTML and confirm the rename persists.
- [ ] Set staging back to `main`. Merge. Confirm staging is healthy.
- [ ] Take a fresh production backup. Record its id.
- [ ] Trigger the production deploy. `curl -i https://sites.katek.app/healthz` expects 200.
- [ ] Re-annotate and re-push `hodges-advisory.html` to `denny`. This is the incident's acceptance test.

### Phase B: Release 2

- [ ] Merge and deploy Release 2 code. Nothing migrates yet; new writes start carrying `_authored`.
- [ ] Run `python manage.py reconcile_content` on production. **No `--apply`.** Read the exception report in full.
- [ ] For every site in the exception report, decide whether those ids are real client edits. This is the step the tooling cannot do.
- [ ] Announce a short write freeze (clients out of the editor, MCP writes paused).
- [ ] Take a **fresh** backup immediately before applying. The Phase A backup is not sufficient.
- [ ] `python manage.py reconcile_content --apply`.
- [ ] Re-run the dry run; expect `0 row(s) would change`.
- [ ] Spot-check two sites' public renders and one version restore.
- [ ] Reopen writes.

## Out of scope, deliberately

- **Changing `editor.js` to send patches.** Task 6 derives authorship server-side from the full blob, so the wire format is an optimisation.
- **Repairing sites already masked by a historical default change.** Detectable only by a human reading the exception report. `TemplateVersion.schema` history could narrow the candidates; that is a follow-up.
- **Type-change compatibility.** A surviving field id whose `data-type` changes between versions reinterprets an old value under the new type.
