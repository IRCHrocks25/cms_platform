# Design: Bind orphan GHL sub-account installs to a Tenant

**Date:** 2026-08-04
**Author:** Bernard (with Claude)
**Status:** Approved — implementation next
**Repo:** `IRCHrocks25/cms_platform` (sites.katek.app)

---

## 1. Problem

A GHL sub-account can already install this app directly, with no agency involved — confirmed live in prod: a `GhlInstall` row with `user_type="Location"`, `agency=None`, created purely from a sub-account admin authorizing the OAuth flow.

But there is no supported way to connect that install to a CMS `Tenant`:

- The Integrations page (`/dashboard/integrations/`) already lists these under "Other connections" (`orphan_installs` in `dashboard/views.py::integrations`), but that table only offers **Reconnect** / **Disconnect** — no bind action.
- The existing `integrations_bind` view (`dashboard/views.py:3183`) hard-requires a `GhlAgencyInstall` (`get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))`) and validates the location against that agency's `available_locations` — it cannot be reused for an agency-less install.
- The only way to get a working CMS login today is an undocumented fallback: manually pasting the raw `location_id` into a `Tenant`'s Settings form (`templates/dashboard/tenant_detail.html:264-266`), which sets `Tenant.ghl_location_id` directly but leaves `GhlInstall.tenant` unset, so the Integrations page keeps showing the install as unbound.

## 2. Goals / Non-goals

### Goals
1. Let an agency admin bind an orphan (agency-less) `GhlInstall` to an existing `Tenant` from the Integrations page.
2. Keep `GhlInstall.tenant` and `Tenant.ghl_location_id` in sync when bound — mirrors what `bind_location` already does for agency-sourced binds.
3. Reuse the existing clash-prevention semantics exactly as `integrations_bind` already enforces them (a location or tenant can't end up double-bound).

### Non-goals (out of scope for this design)
- **Auto-provisioning a new Tenant/site on install.** The operator still creates the `Tenant` via the existing new-client flow (`tenant_create`), then links it here.
- **The Custom Page / SSO sidebar.** This is "Slice A" of the broader self-serve sub-account effort; the Sub-Account-distribution GHL app + Custom Page module + SSO-blob-authenticated embed (replacing the unsigned-URL-param Custom Menu Link) is "Slice B" and gets its own design doc when picked up.
- **The manual raw `ghl_location_id` fallback field.** Stays exactly as-is; this adds a second, better-integrated path alongside it, not a replacement.
- **Touching the existing agency `integrations_bind` view or `bind_location` service function.** Zero changes to that already-shipped, working path.

## 3. What already exists (reuse, don't rebuild)

| Primitive | Location | Reuse |
|---|---|---|
| Orphan detection & context | `integrations()` (`dashboard/views.py:3153`) | Already builds `orphan_installs` and `tenants` and passes both to the template |
| Clash-check pattern | `integrations_bind` (`dashboard/views.py:3183-3206`) | Mirror the same two-part clash logic |
| Tenant-linking half of a bind | `ghl_connect.bind_location` (`core/services/ghl_connect.py:41-72`) | Mirror the `tenant.ghl_location_id` sync tail; skip the token-mint head (the orphan already has its own valid token from the direct OAuth exchange) |
| Row-scoped action forms | `templates/dashboard/integrations.html:198-209` (Reconnect/Disconnect) | Copy this exact per-row form pattern for "Link to site" |
| Permission decorator | `agency_admin_required` (`core/permissions.py:44-59`) | Same decorator already used on every other GHL integrations view (superuser-only, stricter than `agency_operator_required`) |

## 4. Data model

No schema changes. Uses the existing `GhlInstall.tenant` FK and `Tenant.ghl_location_id` field.

## 5. Design

### 5.1 Service function

`core/services/ghl_connect.py`:

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

### 5.2 View

`dashboard/views.py`, alongside the other `integrations_*` views:

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

### 5.3 URL

`dashboard/urls.py`, alongside the other `integrations/*` routes:

```python
path("integrations/bind-orphan/", views.integrations_bind_orphan, name="integrations_bind_orphan"),
```

### 5.4 Template

`templates/dashboard/integrations.html`, inside the `{% for i in orphan_installs %}` loop (around line 184-210): in the Actions cell, **only when `i.tenant` is falsy**, add a form with a `<select name="tenant_id">` + hidden `install_id` + a "Link" submit button, styled identically to the neighboring Reconnect/Disconnect forms (`row-actions`, `btn btn-secondary btn-sm`). When `i.tenant` is already set (e.g. bound earlier via the manual raw-paste fallback), show no form — the existing "Bound site" column already renders `i.tenant.name`.

The `<select>` lists **all** tenants from the existing unfiltered `tenants` context variable (no pre-filtering to "only unbound" tenants) — matches the codebase's existing minimalism (the agency-side bind dropdown in `tenant_detail.html` doesn't pre-filter its options either); a doomed pick is caught by the view's clash check and surfaced as an error message rather than hidden from the list.

## 6. Error handling

| Case | Behavior |
|---|---|
| Bad `install_id` or `tenant_id` | 404 (`get_object_or_404`, matches existing pattern) |
| Install already has an `agency` | Rejected with an error message, no mutation. UI won't offer this (only agency-less rows get the form), but the endpoint guards it since it's a reachable POST target. |
| Location already claimed by a different tenant, or install already bound to a different tenant | Rejected with "That sub-account is already linked to another site.", no mutation — same message text as `integrations_bind`'s clash case |
| Race condition (`IntegrityError`) | Caught the same way `integrations_bind` catches it today |
| Non-superuser | 403 via `agency_admin_required` |

## 7. Testing

- `core/tests/test_ghl_connect.py`: `bind_orphan_install` happy path — sets `install.tenant`, sets `tenant.ghl_location_id`, does not touch `access_token`/`refresh_token`/`expires_at`.
- `core/tests/test_integrations_views.py`:
  - Success bind (redirect + success message + DB state: `GhlInstall.tenant` and `Tenant.ghl_location_id` both set)
  - Clash: location already claimed by another tenant → error message, no mutation
  - Clash: install already bound to a different tenant → error message, no mutation
  - Install has an `agency` set → rejected with error message
  - Non-superuser → 403

## 8. Rollout

Pure code change — no migration, no new env var, no GHL portal change. Deploys via the normal `main` push (`cmsdashboard-sites-2ka9w7` compose has `autoDeploy: true`). Verify post-deploy by binding the sub-account already installed in prod (`location_id=8ndFlzmAW53dNbjxC2Il`, currently orphaned — confirmed via direct DB check on 2026-08-03) to a test `Tenant`, then confirming `/embed/?location_id=8ndFlzmAW53dNbjxC2Il&email=...` logs in correctly.

## 9. Relationship to future work

This is **Slice A** of the broader self-serve sub-account GHL install effort scoped in conversation on 2026-08-03/04. **Slice B** — a second, Sub-Account-distribution GHL marketplace app with a Custom Page module and SSO-blob-authenticated embed (decrypting the `REQUEST_USER_DATA` postMessage payload with the app's Shared Secret Key, replacing the unsigned `?location_id=&email=` trust model) — is deliberately deferred and will get its own design doc. The existing Agency-targeted app and its Company-install/bind-picker flow (`docs/specs/2026-07-10-ghl-agency-connect-design.md`) are untouched by either slice.
