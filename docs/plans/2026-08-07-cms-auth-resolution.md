# CMS Auth Resolution (CMS-4 + CMS-5) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Customize the OAuth consent screen to show tenant/role context, and resolve access tokens to a concrete `(user, role, tenant)` principal for the MCP API surface.

**Architecture:** Subclass django-oauth-toolkit's `AuthorizationView` so the consent template receives membership-derived context while reusing CMS `LOGIN_URL`. Add a pure resolution function plus a django-ninja `HttpBearer` auth class that looks up `oauth2_provider.AccessToken`, mirrors `core/permissions.py` semantics (staff/superuser platform-wide; members scoped via `TenantMembership`), and returns nothing for expired, revoked, or non-member tokens.

**Tech Stack:** Django 5.1, django-oauth-toolkit 3.4.0, django-ninja 1.6.2, Django test runner (Python 3.12)

---

## Design decisions

- Consent shows every `TenantMembership` for the logged-in user (tenant name + role). Staff/superuser also see a platform-wide row (`tenant=None`, role `superadmin` or `staff`) so agency operators understand they are granting platform access.
- Resolution stays free of "admin-only" gates: the same function serves staff and client members. Callers decide which principals they accept later.
- Cross-tenant denial is explicit: `ResolvedAuth.for_tenant(tenant)` returns `None` when the principal has no platform role and no membership on that tenant.
- Wire the custom authorize view in `api/oauth_urls.py` (replace toolkit `AuthorizationView` only); leave metadata + token routes untouched.
- Override `templates/oauth2_provider/authorize.html` (Django template override) rather than changing package files.

---

### Task 1: Token → (user, role, tenant) resolution (CMS-5)

**Files:**
- Create: `api/auth.py`
- Create: `api/tests/__init__.py`
- Create: `api/tests/test_token_resolution.py`

**Step 1: Write the failing tests**

Cover:
1. Superuser resolves platform-wide (`platform_role="superadmin"`, empty membership scopes).
2. Staff (non-superuser) resolves platform-wide (`platform_role="staff"`).
3. Single-tenant member resolves to that tenant + membership role.
4. Multi-tenant member resolves to all memberships.
5. Cross-tenant denial: member of A → `for_tenant(B)` is `None`.
6. Non-member / no memberships → `resolve_access_token` returns `None`.
7. Expired token → `None`.
8. Revoked token → `None`.

**Step 2: Run tests — expect FAIL** (module missing).

**Step 3: Implement `api/auth.py`**

- `TenantScope(tenant, role)` dataclass
- `ResolvedAuth(user, platform_role, scopes)` with `for_tenant(tenant)`
- `resolve_access_token(token: str) -> ResolvedAuth | None`
- `CmsBearerAuth(HttpBearer)` that calls `resolve_access_token` and returns the principal (or `None`)

Use `AccessToken.objects.select_related("user").filter(token=...).first()`, then `token.is_valid()` (covers expiry + revocation). Build scopes from `TenantMembership.objects.filter(user=...)`.

**Step 4: Run tests — expect PASS.**

**Step 5: Commit** `test(api): cover OAuth token to tenant resolution` then `feat(api): resolve access tokens to user/role/tenant`.

---

### Task 2: Consent flow with tenant/role context (CMS-4)

**Files:**
- Create: `api/views.py` (or `api/oauth_views.py`) — `CmsAuthorizationView`
- Create: `templates/oauth2_provider/authorize.html`
- Modify: `api/oauth_urls.py` — mount custom authorize view
- Create: `api/tests/test_consent.py`
- Possibly share `build_consent_contexts(user)` with the view (can live in `api/auth.py`)

**Step 1: Write the failing tests**

1. Unit: `build_consent_contexts(user)` returns tenant name + role for a member; platform row for superuser.
2. View: logged-in member GETs `/authorize/` with PKCE params → 200, response contains tenant name and role, and `consent_contexts` is present in the template context (use `assertTemplateUsed` + context assertion via `response.context`).
3. Integration: full authorization_code + PKCE — login → GET authorize → POST allow → redirect to `redirect_uri` with `?code=...`.

**Step 2: Run tests — expect FAIL.**

**Step 3: Implement view + template + URL wiring.**

**Step 4: Run tests — expect PASS.**

**Step 5: Commit** `feat(api): show tenant and role on OAuth consent screen`.

---

### Task 3: Verify and ship

1. `python manage.py collectstatic --noinput`
2. Focused tests on Python 3.12 venv.
3. Full suite if feasible (skip claiming green on known 3.14 issues).
4. `makemigrations --check --dry-run`, `manage.py check`
5. Push `feat/cms-auth-resolution`, open one PR for CMS-4 + CMS-5.
6. Comment PR URL on both Plane tickets; move both to In Review. STOP.
