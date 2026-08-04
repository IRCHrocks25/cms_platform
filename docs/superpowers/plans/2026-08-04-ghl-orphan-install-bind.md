# Bind Orphan GHL Sub-Account Installs to a Tenant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agency admin link an orphan (agency-less) `GhlInstall` — created when a GHL sub-account installs the app directly, with no agency involved — to an existing CMS `Tenant`, from the Integrations dashboard page.

**Architecture:** A new service function `bind_orphan_install` in `core/services/ghl_connect.py` does the actual link (no OAuth call needed — the orphan already holds its own token). A new view `integrations_bind_orphan` in `dashboard/views.py` does clash-checking (mirroring the existing `integrations_bind`) before calling it, wired to a new URL. A small template change to `templates/dashboard/integrations.html` adds a per-row "Link to site" form for orphan rows that aren't bound yet.

**Tech Stack:** Django 5, Django TestCase (`manage.py test`), existing `GhlInstall`/`GhlAgencyInstall`/`Tenant` models — no new dependencies, no migration.

**Reference:** Design spec at `docs/superpowers/specs/2026-08-04-ghl-orphan-install-bind-design.md` (approved).

---

## File Structure

- **Modify:** `core/services/ghl_connect.py` — add `bind_orphan_install()`, inserted between `bind_location()` (ends line 72) and `reconnect_install()` (starts line 75). No new imports — `GhlAgencyInstall, GhlInstall, Tenant` already imported at the top of this file.
- **Modify:** `dashboard/views.py` — add `integrations_bind_orphan()` view, placed directly after `integrations_bind` (ends line 3206) and before `integrations_reconnect` (starts line 3210). No new imports — `messages`, `IntegrityError`, `get_object_or_404`, `redirect`, `require_POST`, `agency_admin_required`, `GhlInstall`, `Tenant`, `ghl_connect` are all already imported at the top of this file (lines 9, 11, 20, 24-29, 30, 37-40).
- **Modify:** `dashboard/urls.py` — add one `path(...)` line inside the existing `integrations/*` block (after line 78, the `integrations/disconnect/` line).
- **Modify:** `templates/dashboard/integrations.html` — add a conditional per-row form inside the `{% for i in orphan_installs %}` loop (currently lines 184-209), in the same `<span class="row-actions">` block as the existing Reconnect/Disconnect forms.
- **Modify:** `core/tests/test_ghl_connect.py` — add a new `BindOrphanInstallTests` test class.
- **Modify:** `core/tests/test_integrations_views.py` — add 5 new test methods to the existing `IntegrationsViewTests` class.

---

## Task 1: Service function `bind_orphan_install`

**Files:**
- Modify: `core/services/ghl_connect.py:72-75` (insert between `bind_location` and `reconnect_install`)
- Test: `core/tests/test_ghl_connect.py`

- [ ] **Step 1: Write the failing test**

Add this new test class at the end of `core/tests/test_ghl_connect.py` (append after the last existing test class in the file):

```python
@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app-ver", GHL_CLIENT_SECRET="s")
class BindOrphanInstallTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("orphan_owner", password="pw")
        self.template = Template.objects.create(name="OT", html_source="<div></div>")
        self.tenant = Tenant.objects.create(
            name="Orphan Target", subdomain="orphantarget", template=self.template, owner=self.owner
        )

    def test_bind_orphan_install_links_tenant_without_minting(self):
        from core.services import ghl_connect

        install = GhlInstall.objects.create(
            location_id="loc_orphan",
            access_token=encrypt_token("orig-access"),
            refresh_token=encrypt_token("orig-refresh"),
        )
        with mock.patch("core.ghl_oauth.mint_location_token") as mint:
            result = ghl_connect.bind_orphan_install(install=install, tenant=self.tenant)
            mint.assert_not_called()

        self.assertEqual(result.tenant_id, self.tenant.pk)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.ghl_location_id, "loc_orphan")
        # Tokens are untouched — still decrypt to their original values.
        self.assertEqual(decrypt_token(result.access_token), "orig-access")
        self.assertEqual(decrypt_token(result.refresh_token), "orig-refresh")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python manage.py test core.tests.test_ghl_connect.BindOrphanInstallTests -v 2`
Expected: FAIL with `AttributeError: module 'core.services.ghl_connect' has no attribute 'bind_orphan_install'`

- [ ] **Step 3: Write minimal implementation**

In `core/services/ghl_connect.py`, insert this function between the end of `bind_location` (line 72, `return install`) and the blank lines before `def reconnect_install` (line 75):

```python
def bind_orphan_install(*, install: GhlInstall, tenant: Tenant) -> GhlInstall:
    """Link a direct (agency-less) GhlInstall to a Tenant. No token minting —
    the install already holds its own valid token from the direct OAuth
    exchange. Caller is responsible for clash-checking first, mirroring
    integrations_bind's convention (the clash check lives in the view, not
    the service function)."""
    install.tenant = tenant
    install.save(update_fields=["tenant", "updated_at"])
    if tenant.ghl_location_id != install.location_id:
        tenant.ghl_location_id = install.location_id
        tenant.save(update_fields=["ghl_location_id", "updated_at"])
    return install
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python manage.py test core.tests.test_ghl_connect.BindOrphanInstallTests -v 2`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add core/services/ghl_connect.py core/tests/test_ghl_connect.py
git commit -m "feat(ghl): add bind_orphan_install service function"
```

---

## Task 2: View `integrations_bind_orphan` + URL

**Files:**
- Modify: `dashboard/views.py:3206` (insert after `integrations_bind`, before `integrations_reconnect`)
- Modify: `dashboard/urls.py:78` (insert into the `integrations/*` block)
- Test: `core/tests/test_integrations_views.py`

- [ ] **Step 1: Write the failing tests**

Add these 5 methods to the existing `IntegrationsViewTests` class in `core/tests/test_integrations_views.py` (append after the last existing test method in that class — keep them inside the class body, same indentation as the other `test_*` methods):

```python
    def test_bind_orphan_links_tenant(self):
        install = GhlInstall.objects.create(location_id="loc_orphan", access_token=encrypt_token("x"))
        resp = self.client.post(reverse("dashboard:integrations_bind_orphan"), {
            "install_id": install.pk, "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(install.tenant_id, self.tenant.pk)
        self.assertEqual(self.tenant.ghl_location_id, "loc_orphan")

    def test_bind_orphan_rejects_when_location_claimed_by_another_tenant(self):
        other = Tenant.objects.create(
            name="Beta", subdomain="beta", template=self.template,
            owner=self.owner, ghl_location_id="loc_orphan",
        )
        install = GhlInstall.objects.create(location_id="loc_orphan", access_token=encrypt_token("x"))
        resp = self.client.post(reverse("dashboard:integrations_bind_orphan"), {
            "install_id": install.pk, "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertIsNone(install.tenant_id)
        self.assertNotEqual(self.tenant.ghl_location_id, "loc_orphan")
        other.refresh_from_db()
        self.assertEqual(other.ghl_location_id, "loc_orphan")

    def test_bind_orphan_rejects_when_install_already_bound_elsewhere(self):
        other = Tenant.objects.create(
            name="Gamma", subdomain="gamma", template=self.template, owner=self.owner,
        )
        install = GhlInstall.objects.create(
            location_id="loc_orphan", tenant=other, access_token=encrypt_token("x"),
        )
        resp = self.client.post(reverse("dashboard:integrations_bind_orphan"), {
            "install_id": install.pk, "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.assertEqual(install.tenant_id, other.pk)

    def test_bind_orphan_rejects_when_install_has_agency(self):
        install = GhlInstall.objects.create(
            location_id="loc_a", agency=self.agency, access_token=encrypt_token("x"),
        )
        resp = self.client.post(reverse("dashboard:integrations_bind_orphan"), {
            "install_id": install.pk, "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.assertIsNone(install.tenant_id)

    def test_bind_orphan_requires_superuser(self):
        self.client.logout()
        non_admin = User.objects.create_user("staffonly", password="pw", is_staff=True, is_superuser=False)
        self.client.force_login(non_admin)
        install = GhlInstall.objects.create(location_id="loc_orphan", access_token=encrypt_token("x"))
        resp = self.client.post(reverse("dashboard:integrations_bind_orphan"), {
            "install_id": install.pk, "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test core.tests.test_integrations_views.IntegrationsViewTests -v 2`
Expected: FAIL — `NoReverseMatch: Reverse for 'integrations_bind_orphan' not found` on all 5 new tests.

- [ ] **Step 3: Write minimal implementation — URL**

In `dashboard/urls.py`, add this line inside the `integrations/*` block, directly after the `integrations/disconnect/` line (line 78):

```python
    path("integrations/bind-orphan/", views.integrations_bind_orphan, name="integrations_bind_orphan"),
```

- [ ] **Step 4: Write minimal implementation — view**

In `dashboard/views.py`, insert this function directly after `integrations_bind` ends (line 3206, `return redirect(dest)`) and before the blank lines preceding `def integrations_reconnect` (line 3210):

```python
@agency_admin_required
@require_POST
def integrations_bind_orphan(request):
    install = get_object_or_404(GhlInstall, pk=request.POST.get("install_id"))
    tenant = get_object_or_404(Tenant, pk=request.POST.get("tenant_id"))
    if install.agency_id:
        messages.error(request, "This install belongs to an agency — use the agency bind flow instead.")
        return redirect("dashboard:integrations")
    clash = (
        Tenant.objects.filter(ghl_location_id=install.location_id).exclude(pk=tenant.pk).exists()
        or (install.tenant_id and install.tenant_id != tenant.pk)
    )
    if clash:
        messages.error(request, "That sub-account is already linked to another site.")
        return redirect("dashboard:integrations")
    try:
        ghl_connect.bind_orphan_install(install=install, tenant=tenant)
        messages.success(request, f"Connected '{tenant.name}' to sub-account {install.location_id}.")
    except IntegrityError:
        messages.error(request, "That sub-account is already linked to another site.")
    return redirect("dashboard:integrations")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test core.tests.test_integrations_views.IntegrationsViewTests -v 2`
Expected: PASS (all tests in the class, including the 5 new ones and the pre-existing ones)

- [ ] **Step 6: Commit**

```bash
git add dashboard/views.py dashboard/urls.py core/tests/test_integrations_views.py
git commit -m "feat(ghl): add integrations_bind_orphan view and URL"
```

---

## Task 3: Template — "Link to site" form for unbound orphan rows

**Files:**
- Modify: `templates/dashboard/integrations.html:197-209`
- Test: `core/tests/test_integrations_views.py`

- [ ] **Step 1: Write the failing tests**

Add these 2 methods to `IntegrationsViewTests` in `core/tests/test_integrations_views.py`:

```python
    def test_integrations_page_shows_bind_form_for_unbound_orphan(self):
        # NOTE: don't also assert on 'name="tenant_id"' here — the existing
        # agency-bind form (integrations.html ~line 88) already renders that
        # attribute whenever setUp's self.agency has an unbound location, so
        # that assertion would false-pass even without this task's template
        # change. The reverse()'d bind-orphan URL is unique to this form and
        # is the only assertion needed to isolate it.
        GhlInstall.objects.create(location_id="loc_unbound", access_token=encrypt_token("x"))
        resp = self.client.get(reverse("dashboard:integrations"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("dashboard:integrations_bind_orphan"))

    def test_integrations_page_hides_bind_form_for_bound_orphan(self):
        already_bound = Tenant.objects.create(
            name="Delta", subdomain="delta", template=self.template, owner=self.owner,
        )
        GhlInstall.objects.create(
            location_id="loc_bound", tenant=already_bound, access_token=encrypt_token("x"),
        )
        resp = self.client.get(reverse("dashboard:integrations"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Delta")
        self.assertNotContains(resp, reverse("dashboard:integrations_bind_orphan"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python manage.py test core.tests.test_integrations_views.IntegrationsViewTests.test_integrations_page_shows_bind_form_for_unbound_orphan core.tests.test_integrations_views.IntegrationsViewTests.test_integrations_page_hides_bind_form_for_bound_orphan -v 2`
Expected: FAIL — `test_integrations_page_shows_bind_form_for_unbound_orphan` fails because the bind-orphan URL never appears in the rendered page (no form yet).

- [ ] **Step 3: Write minimal implementation**

In `templates/dashboard/integrations.html`, the orphan-row Actions cell currently reads (lines 197-209):

```html
              <td class="right">
                <span class="row-actions">
                  <form method="post" action="{% url 'dashboard:integrations_reconnect' %}" style="display: inline; margin: 0;">
                    {% csrf_token %}
                    <input type="hidden" name="install_id" value="{{ i.pk }}">
                    <button class="btn btn-secondary btn-sm" type="submit">Reconnect</button>
                  </form>
                  <form method="post" action="{% url 'dashboard:integrations_disconnect' %}" style="display: inline; margin: 0;">
                    {% csrf_token %}
                    <input type="hidden" name="install_id" value="{{ i.pk }}">
                    <button class="btn btn-danger btn-sm" type="submit">Disconnect</button>
                  </form>
                </span>
              </td>
```

Replace it with (adds the conditional "Link to site" form before the existing two forms):

```html
              <td class="right">
                <span class="row-actions">
                  {% if not i.tenant %}
                  <form method="post" action="{% url 'dashboard:integrations_bind_orphan' %}" style="display: inline; margin: 0;">
                    {% csrf_token %}
                    <input type="hidden" name="install_id" value="{{ i.pk }}">
                    <select name="tenant_id" class="input" style="display: inline-block; width: auto;" required>
                      <option value="">Link to site…</option>
                      {% for t in tenants %}
                      <option value="{{ t.pk }}">{{ t.name }}</option>
                      {% endfor %}
                    </select>
                    <button class="btn btn-secondary btn-sm" type="submit">Link</button>
                  </form>
                  {% endif %}
                  <form method="post" action="{% url 'dashboard:integrations_reconnect' %}" style="display: inline; margin: 0;">
                    {% csrf_token %}
                    <input type="hidden" name="install_id" value="{{ i.pk }}">
                    <button class="btn btn-secondary btn-sm" type="submit">Reconnect</button>
                  </form>
                  <form method="post" action="{% url 'dashboard:integrations_disconnect' %}" style="display: inline; margin: 0;">
                    {% csrf_token %}
                    <input type="hidden" name="install_id" value="{{ i.pk }}">
                    <button class="btn btn-danger btn-sm" type="submit">Disconnect</button>
                  </form>
                </span>
              </td>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python manage.py test core.tests.test_integrations_views.IntegrationsViewTests -v 2`
Expected: PASS (entire class — confirms the new template logic doesn't break any pre-existing integrations-page test either)

- [ ] **Step 5: Commit**

```bash
git add templates/dashboard/integrations.html core/tests/test_integrations_views.py
git commit -m "feat(ghl): show a Link-to-site form for unbound orphan installs"
```

---

## Task 4: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python manage.py test -v 2`
Expected: PASS, 0 failures, 0 errors (confirms nothing in the broader suite — e.g. other `integrations.html` renders, other GHL flows — regressed)

- [ ] **Step 2: Manually verify against the real orphan install already sitting in prod**

This isn't a code step — it's the rollout verification called out in the design spec (section 8). After this branch is deployed:
1. Open `/dashboard/integrations/`, confirm the "Other connections" table shows `location_id=8ndFlzmAW53dNbjxC2Il` with a "Link to site…" dropdown.
2. Pick a test `Tenant`, click Link.
3. Confirm the row now shows that tenant under "Bound site" and no longer shows the dropdown.
4. Curl or open `https://sites.katek.app/embed/?location_id=8ndFlzmAW53dNbjxC2Il&email=<any-active-user-email>` and confirm it logs in and redirects into that tenant's editor.

Report back once done — this step needs a human with prod access, not something to automate here.

---

## Self-Review Notes

- **Spec coverage:** §5.1 → Task 1. §5.2 → Task 2 (view). §5.3 → Task 2 (URL). §5.4 → Task 3. §6 (error handling table) → covered by Task 2's 4 failure-path tests. §7 (required tests) → all 6 named tests present across Tasks 1-3. §8 (rollout verification) → Task 4 Step 2.
- **Placeholder scan:** none — every step has literal file content, exact commands, and expected output.
- **Type/name consistency:** `bind_orphan_install(*, install, tenant)` keyword-only signature matches its one call site in Task 2's view. `integrations_bind_orphan` name matches across the URL, the view `def`, and every `reverse("dashboard:integrations_bind_orphan")` call in tests and the template. Test class name `BindOrphanInstallTests` doesn't collide with the existing `BindLocationTests` in the same file.
