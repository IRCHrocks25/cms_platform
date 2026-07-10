# GHL Agency Connect + Multi-Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-dashboard "Connect GHL agency" flow that installs once at the agency level, enumerates the agency's sub-accounts, and lets an operator bind/re-bind any sub-account to a CMS site — with encrypted tokens and refresh-before-use — supporting multiple agencies.

**Architecture:** Extend the existing marketplace OAuth flow. A new `GhlAgencyInstall` (keyed by `company_id`) stores the agency-level refresh token and the enumerated sub-account list. `GhlInstall` (per `location_id`) gains an `agency` FK, `location_name`, and `status`. HTTP calls stay in `core/ghl_oauth.py`; crypto in `core/ghl_crypto.py`; model-touching orchestration in `core/services/ghl_connect.py`. Dashboard views expose Connect/bind/reconnect/disconnect.

**Tech Stack:** Django 5.1, httpx, cryptography (Fernet), Django test framework. GHL v2 endpoints on `leadconnectorhq.com`.

**Reference spec:** `docs/specs/2026-07-10-ghl-agency-connect-design.md`

**Test command:** `python manage.py test core dashboard -v 2`

**Test-fixture note:** these tasks create `Tenant`/`Template` rows in tests. Before writing the first fixture, open an existing test (e.g. `core/tests/test_agency_admin.py` or `test_tenant_dashboard.py`) and mirror how it builds a `Tenant` — in particular satisfy any required FKs (`template`, and `owner` if non-nullable). Adjust the `setUp` fixtures in this plan to match the real model constraints rather than assuming.

**Task 5 ↔ 6 pairing:** Task 5's callback redirects to the `dashboard:integrations` route, which Task 6 creates. Implement Task 6 immediately after Task 5 (they land as a pair). If you run Task 5's callback test before Task 6's routes exist, the redirect target won't resolve — that's expected; complete Task 6, then both suites pass.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `core/ghl_crypto.py` | Fernet encrypt/decrypt for tokens at rest | Create |
| `core/models.py` | `GhlAgencyInstall` model; `GhlInstall` FK+status+name | Modify |
| `core/migrations/0019_ghl_agency_install.py` | Schema | Generate |
| `core/ghl_oauth.py` | `list_installed_locations`, `refresh_access_token` | Modify |
| `core/services/ghl_connect.py` | `ensure_fresh_agency_token`, `bind_location`, `reconnect_install` | Create |
| `core/ghl_views.py` | `oauth_callback` Company branch + encrypt on Location branch | Modify |
| `dashboard/views.py` | `integrations*` views | Modify |
| `dashboard/urls.py` | integrations routes | Modify |
| `templates/dashboard/integrations.html` | Connect UI + picker | Create |
| `templates/dashboard/tenant_detail.html` | location-id field → dropdown | Modify |
| `templates/base.html` | nav link to Integrations | Modify |
| `cms_platform/settings.py` | `GHL_TOKEN_ENCRYPTION_KEY`, `GHL_APP_VERSION_ID` | Modify |
| `requirements.txt` | `cryptography` | Modify |
| `.env.example` | document new vars | Modify |
| `core/tests/test_ghl_crypto.py` / `test_ghl_models.py` / `test_ghl_oauth_locations.py` / `test_ghl_connect.py` / `test_ghl_callback.py` / `test_integrations_views.py` | Tests | Create |

---

## Task 1: Token encryption helpers

**Files:**
- Create: `core/ghl_crypto.py`
- Modify: `cms_platform/settings.py`, `requirements.txt`
- Test: `core/tests/test_ghl_crypto.py`

- [ ] **Step 1: Add the dependency and setting**

In `requirements.txt` add a line (no upper bound — the venv already has 48.x transitively; pinning `<44` would force a downgrade/conflict):
```
cryptography>=42
```
In `cms_platform/settings.py`, in the GHL block (near `GHL_SHARED_SECRET`), add:
```python
# Fernet key (urlsafe base64, 32 bytes) for encrypting GHL tokens at rest.
GHL_TOKEN_ENCRYPTION_KEY = os.environ.get("GHL_TOKEN_ENCRYPTION_KEY", "")
# Optional explicit app version id; falls back to GHL_CLIENT_ID prefix.
GHL_APP_VERSION_ID = os.environ.get("GHL_APP_VERSION_ID", "")
```
Then install: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `core/tests/test_ghl_crypto.py`:
```python
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from core.ghl_crypto import TokenCryptoError, decrypt_token, encrypt_token

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY)
class TokenCryptoTests(TestCase):
    def test_round_trip(self):
        self.assertEqual(decrypt_token(encrypt_token("secret-abc")), "secret-abc")

    def test_ciphertext_is_not_plaintext(self):
        self.assertNotEqual(encrypt_token("secret-abc"), "secret-abc")

    def test_empty_values(self):
        self.assertEqual(encrypt_token(""), "")
        self.assertEqual(decrypt_token(""), "")


@override_settings(GHL_TOKEN_ENCRYPTION_KEY="")
class TokenCryptoMissingKeyTests(TestCase):
    def test_encrypt_fails_closed_without_key(self):
        with self.assertRaises(TokenCryptoError):
            encrypt_token("x")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python manage.py test core.tests.test_ghl_crypto -v 2`
Expected: FAIL / ImportError (`core.ghl_crypto` does not exist yet).

- [ ] **Step 4: Write the implementation**

Create `core/ghl_crypto.py`:
```python
"""Fernet encryption for GHL tokens at rest.

Keyed by GHL_TOKEN_ENCRYPTION_KEY (urlsafe base64, 32 bytes). Fails closed
when the key is missing so we never silently store plaintext.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenCryptoError(Exception):
    """Raised when encryption/decryption cannot proceed."""


def _fernet() -> Fernet:
    key = getattr(settings, "GHL_TOKEN_ENCRYPTION_KEY", "") or ""
    if not key:
        raise TokenCryptoError("GHL_TOKEN_ENCRYPTION_KEY is not set.")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError(f"Invalid GHL_TOKEN_ENCRYPTION_KEY: {exc}") from exc


def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("Could not decrypt token (wrong key or corrupt data).") from exc
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test core.tests.test_ghl_crypto -v 2`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add core/ghl_crypto.py core/tests/test_ghl_crypto.py cms_platform/settings.py requirements.txt
git commit -m "feat(ghl): add Fernet token encryption helpers"
```

---

## Task 2: `GhlAgencyInstall` model + `GhlInstall` fields

**Files:**
- Modify: `core/models.py` (after the existing `GhlInstall` class, ~line 537)
- Generate: `core/migrations/0019_ghl_agency_install.py`
- Test: `core/tests/test_ghl_models.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_ghl_models.py`:
```python
from django.db import IntegrityError
from django.test import TestCase

from core.models import GhlAgencyInstall, GhlInstall, Tenant, Template


class GhlModelTests(TestCase):
    def test_agency_company_id_is_unique(self):
        GhlAgencyInstall.objects.create(company_id="co_1")
        with self.assertRaises(IntegrityError):
            GhlAgencyInstall.objects.create(company_id="co_1")

    def test_install_links_agency_and_defaults_connected(self):
        agency = GhlAgencyInstall.objects.create(company_id="co_2")
        install = GhlInstall.objects.create(
            location_id="loc_1", agency=agency, access_token="enc"
        )
        self.assertEqual(install.agency, agency)
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)
        self.assertEqual(list(agency.location_installs.all()), [install])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_ghl_models -v 2`
Expected: FAIL (`GhlAgencyInstall` not importable; `STATUS_CONNECTED` missing).

- [ ] **Step 3: Add the model + fields**

In `core/models.py`, add the new fields to `GhlInstall` (inside the class, alongside the existing fields):
```python
    STATUS_CONNECTED = "connected"
    STATUS_EXPIRED = "expired"
    STATUS_DISCONNECTED = "disconnected"
    STATUS_CHOICES = [
        (STATUS_CONNECTED, "Connected"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_DISCONNECTED, "Disconnected"),
    ]

    agency = models.ForeignKey(
        "core.GhlAgencyInstall",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="location_installs",
    )
    location_name = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_CONNECTED
    )
```

Then add the new model directly after the `GhlInstall` class:
```python
class GhlAgencyInstall(models.Model):
    """A GHL agency (Company) install. Holds the agency-level refresh token
    used to mint per-sub-account (location) tokens on demand, plus the list
    of sub-accounts the app is installed on. One row per GHL company; this
    is the multi-agency anchor. Tokens are stored encrypted (see ghl_crypto).
    """

    company_id = models.CharField(max_length=64, unique=True)
    company_name = models.CharField(max_length=200, blank=True, default="")
    access_token = models.TextField(blank=True, default="")   # encrypted
    refresh_token = models.TextField(blank=True, default="")  # encrypted
    expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    # [{"id": "loc_x", "name": "Acme Inc"}, ...] from installedLocations
    available_locations = models.JSONField(default=list, blank=True)
    installed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-installed_at"]

    def __str__(self):
        return f"GhlAgencyInstall(company={self.company_id})"
```

- [ ] **Step 4: Generate and inspect the migration**

Run: `python manage.py makemigrations core --name ghl_agency_install`
Expected: creates `core/migrations/0019_ghl_agency_install.py` adding `GhlAgencyInstall` and the three `GhlInstall` fields. Open it and confirm it only touches these.

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test core.tests.test_ghl_models -v 2`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add core/models.py core/migrations/0019_ghl_agency_install.py core/tests/test_ghl_models.py
git commit -m "feat(ghl): add GhlAgencyInstall model and GhlInstall agency/status/name fields"
```

---

## Task 3: HTTP calls — installed locations + token refresh

**Files:**
- Modify: `core/ghl_oauth.py` (add two functions after `mint_location_token`, ~line 164)
- Test: `core/tests/test_ghl_oauth_locations.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_ghl_oauth_locations.py`:
```python
from unittest import mock

import httpx
from django.test import TestCase, override_settings

from core import ghl_oauth


def _resp(status, json_body):
    request = httpx.Request("GET", "https://services.leadconnectorhq.com/x")
    return httpx.Response(status, json=json_body, request=request)


@override_settings(GHL_CLIENT_ID="app123-ver", GHL_CLIENT_SECRET="secret")
class InstalledLocationsTests(TestCase):
    def test_parses_locations(self):
        body = {"locations": [
            {"_id": "loc_a", "name": "Acme"},
            {"id": "loc_b", "name": "Beta"},
            {"name": "no id — skipped"},
        ]}
        with mock.patch.object(httpx, "get", return_value=_resp(200, body)):
            out = ghl_oauth.list_installed_locations(
                agency_access_token="tok", company_id="co", app_id="app123"
            )
        self.assertEqual(out, [
            {"id": "loc_a", "name": "Acme"},
            {"id": "loc_b", "name": "Beta"},
        ])

    def test_raises_on_error_status(self):
        with mock.patch.object(httpx, "get", return_value=_resp(401, {"error": "nope"})):
            with self.assertRaises(ghl_oauth.TokenExchangeFailed):
                ghl_oauth.list_installed_locations(
                    agency_access_token="tok", company_id="co", app_id="app123"
                )


@override_settings(GHL_CLIENT_ID="app123-ver", GHL_CLIENT_SECRET="secret")
class RefreshTokenTests(TestCase):
    def test_posts_refresh_grant(self):
        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return _resp(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 86400})

        with mock.patch.object(httpx, "post", side_effect=fake_post):
            out = ghl_oauth.refresh_access_token(refresh_token="r1", user_type="Company")
        self.assertEqual(out["access_token"], "new")
        self.assertEqual(captured["data"]["grant_type"], "refresh_token")
        self.assertEqual(captured["data"]["user_type"], "Company")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_ghl_oauth_locations -v 2`
Expected: FAIL (`list_installed_locations` / `refresh_access_token` not defined).

- [ ] **Step 3: Implement both functions**

In `core/ghl_oauth.py`, after `mint_location_token` add:
```python
def list_installed_locations(
    *, agency_access_token: str, company_id: str, app_id: str
) -> list[dict]:
    """Return sub-accounts the app is installed on for this agency.

    Uses the agency (Company) access token. ``app_id`` is the client-id
    prefix (before the ``-``). Returns ``[{"id", "name"}, ...]``.
    """
    resp = httpx.get(
        INSTALLED_LOCATIONS_URL,
        params={"companyId": company_id, "appId": app_id},
        headers={
            "Authorization": f"Bearer {agency_access_token}",
            "Version": GHL_API_VERSION,
            "Accept": "application/json",
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise TokenExchangeFailed(
            f"GHL /oauth/installedLocations {resp.status_code}: {resp.text[:200]}"
        )
    out: list[dict] = []
    for loc in (resp.json().get("locations") or []):
        loc_id = loc.get("_id") or loc.get("id")
        if not loc_id:
            continue
        out.append({"id": loc_id, "name": (loc.get("name") or "").strip()})
    return out


def refresh_access_token(*, refresh_token: str, user_type: str = "Location") -> dict[str, Any]:
    """Exchange a refresh token for a new access token. ``user_type`` is
    'Company' for agency tokens, 'Location' for sub-account tokens."""
    client_id = settings.GHL_CLIENT_ID
    client_secret = settings.GHL_CLIENT_SECRET
    if not client_id or not client_secret:
        raise RuntimeError("GHL_CLIENT_ID and GHL_CLIENT_SECRET must be set.")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "user_type": user_type,
    }
    try:
        resp = httpx.post(
            TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise TokenExchangeFailed(f"network error: {exc}") from exc
    if resp.status_code >= 400:
        raise TokenExchangeFailed(
            f"GHL /oauth/token refresh {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_ghl_oauth_locations -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/ghl_oauth.py core/tests/test_ghl_oauth_locations.py
git commit -m "feat(ghl): add installedLocations enumeration and refresh_access_token"
```

---

## Task 4: Orchestration service — fresh token, bind, reconnect

**Files:**
- Create: `core/services/ghl_connect.py`
- Test: `core/tests/test_ghl_connect.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_ghl_connect.py`:
```python
from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from core.ghl_crypto import decrypt_token, encrypt_token
from core.models import GhlAgencyInstall, GhlInstall, Tenant, Template

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app-ver", GHL_CLIENT_SECRET="s")
class BindLocationTests(TestCase):
    def setUp(self):
        self.template = Template.objects.create(name="T", html_source="<div></div>")
        self.tenant = Tenant.objects.create(name="Acme", subdomain="acme", template=self.template)
        self.agency = GhlAgencyInstall.objects.create(
            company_id="co_1",
            access_token=encrypt_token("agency-access"),
            refresh_token=encrypt_token("agency-refresh"),
            expires_at=timezone.now() + timedelta(hours=1),
            available_locations=[{"id": "loc_a", "name": "Acme HQ"}],
        )

    def test_bind_mints_and_links(self):
        from core.services import ghl_connect

        mint = {"access_token": "loc-access", "refresh_token": "loc-refresh",
                "expires_in": 86400, "scope": "locations.readonly"}
        with mock.patch("core.ghl_oauth.mint_location_token", return_value=mint) as m:
            install = ghl_connect.bind_location(
                agency=self.agency, location_id="loc_a", tenant=self.tenant
            )
        m.assert_called_once()
        self.assertEqual(install.tenant, self.tenant)
        self.assertEqual(install.location_name, "Acme HQ")
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)
        self.assertEqual(decrypt_token(install.access_token), "loc-access")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.ghl_location_id, "loc_a")

    def test_ensure_fresh_agency_token_refreshes_when_expired(self):
        from core.services import ghl_connect

        self.agency.expires_at = timezone.now() - timedelta(seconds=10)
        self.agency.save(update_fields=["expires_at"])
        refreshed = {"access_token": "fresh-access", "refresh_token": "fresh-refresh",
                     "expires_in": 86400}
        with mock.patch("core.ghl_oauth.refresh_access_token", return_value=refreshed) as r:
            token = ghl_connect.ensure_fresh_agency_token(self.agency)
        r.assert_called_once()
        self.assertEqual(token, "fresh-access")
        self.agency.refresh_from_db()
        self.assertEqual(decrypt_token(self.agency.access_token), "fresh-access")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_ghl_connect -v 2`
Expected: FAIL (`core.services.ghl_connect` missing). Note: `core/services/__init__.py` already exists (annotator/traefik live there).

- [ ] **Step 3: Implement the service**

Create `core/services/ghl_connect.py`:
```python
"""Model-touching GHL orchestration: keep agency tokens fresh, mint and
persist per-location installs, and re-bind on demand. HTTP lives in
core.ghl_oauth; crypto in core.ghl_crypto."""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core import ghl_oauth
from core.ghl_crypto import decrypt_token, encrypt_token
from core.models import GhlAgencyInstall, GhlInstall, Tenant

REFRESH_MARGIN = timedelta(seconds=60)


def _expires_at(expires_in):
    if not expires_in:
        return None
    try:
        return timezone.now() + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def ensure_fresh_agency_token(agency: GhlAgencyInstall) -> str:
    """Return a valid agency access token, refreshing if within the margin."""
    if agency.expires_at and agency.expires_at - timezone.now() > REFRESH_MARGIN:
        return decrypt_token(agency.access_token)
    data = ghl_oauth.refresh_access_token(
        refresh_token=decrypt_token(agency.refresh_token), user_type="Company"
    )
    agency.access_token = encrypt_token(data.get("access_token", ""))
    if data.get("refresh_token"):
        agency.refresh_token = encrypt_token(data["refresh_token"])
    agency.expires_at = _expires_at(data.get("expires_in"))
    agency.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return decrypt_token(agency.access_token)


def bind_location(*, agency: GhlAgencyInstall, location_id: str, tenant: Tenant) -> GhlInstall:
    """Mint a location token from the agency token and persist a connected
    GhlInstall linked to ``tenant``. Also sets Tenant.ghl_location_id so the
    embed/SSO path lights up."""
    access = ensure_fresh_agency_token(agency)
    data = ghl_oauth.mint_location_token(
        agency_access_token=access, company_id=agency.company_id, location_id=location_id
    )
    name = next(
        (l.get("name", "") for l in agency.available_locations if l.get("id") == location_id),
        "",
    )
    scope = data.get("scope", "")
    install, _ = GhlInstall.objects.update_or_create(
        location_id=location_id,
        defaults={
            "agency": agency,
            "company_id": agency.company_id,
            "user_type": GhlInstall.USER_TYPE_LOCATION,
            "access_token": encrypt_token(data.get("access_token", "")),
            "refresh_token": encrypt_token(data.get("refresh_token", "")),
            "expires_at": _expires_at(data.get("expires_in")),
            "scopes": scope.split() if scope else [],
            "location_name": name,
            "status": GhlInstall.STATUS_CONNECTED,
            "tenant": tenant,
        },
    )
    if tenant.ghl_location_id != location_id:
        tenant.ghl_location_id = location_id
        tenant.save(update_fields=["ghl_location_id", "updated_at"])
    return install


def reconnect_install(install: GhlInstall) -> GhlInstall:
    """Re-mint a location token for an existing install from its agency."""
    if install.agency is None or install.tenant is None:
        raise ghl_oauth.TokenExchangeFailed("Install has no agency/tenant to reconnect.")
    return bind_location(
        agency=install.agency, location_id=install.location_id, tenant=install.tenant
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_ghl_connect -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add core/services/ghl_connect.py core/tests/test_ghl_connect.py
git commit -m "feat(ghl): add ghl_connect service (fresh token, bind, reconnect)"
```

---

## Task 5: Extend the OAuth callback (Company branch + encryption)

**Files:**
- Modify: `core/ghl_views.py` (`oauth_callback`, lines 121-216)
- Test: `core/tests/test_ghl_callback.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_ghl_callback.py`:
```python
from unittest import mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from core.ghl_crypto import decrypt_token
from core.models import GhlAgencyInstall, GhlInstall

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app123-ver",
                   GHL_CLIENT_SECRET="s", ALLOWED_HOSTS=["testserver"])
class CallbackCompanyBranchTests(TestCase):
    def test_company_install_enumerates_and_stores_agency(self):
        token = {"userType": "Company", "companyId": "co_9", "access_token": "a",
                 "refresh_token": "r", "expires_in": 86400, "scope": "locations.readonly"}
        locations = [{"id": "loc_a", "name": "Acme"}, {"id": "loc_b", "name": "Beta"}]
        with mock.patch("core.ghl_oauth.exchange_code", return_value=token), \
             mock.patch("core.ghl_oauth.list_installed_locations", return_value=locations):
            resp = self.client.get(reverse("ghl_oauth_callback"), {"code": "c"})
        self.assertEqual(resp.status_code, 302)
        agency = GhlAgencyInstall.objects.get(company_id="co_9")
        self.assertEqual(decrypt_token(agency.access_token), "a")
        self.assertEqual(agency.available_locations, locations)

    def test_location_install_stores_encrypted_install(self):
        token = {"userType": "Location", "locationId": "loc_x", "access_token": "aa",
                 "refresh_token": "rr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.exchange_code", return_value=token):
            resp = self.client.get(reverse("ghl_oauth_callback"), {"code": "c"})
        self.assertEqual(resp.status_code, 302)
        install = GhlInstall.objects.get(location_id="loc_x")
        self.assertEqual(decrypt_token(install.access_token), "aa")
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_ghl_callback -v 2`
Expected: FAIL (Company branch still writes a `company:<id>` placeholder GhlInstall; tokens stored plaintext).

- [ ] **Step 3: Rewrite the token-persistence section of `oauth_callback`**

In `core/ghl_views.py`, add imports near the top:
```python
from .ghl_crypto import encrypt_token
```
Replace everything from the `user_type = token_resp.get(...)` line through the `return render(... "ghl/install_success.html" ...)` (lines 161-216) with:
```python
    user_type = token_resp.get("userType", GhlInstall.USER_TYPE_LOCATION)
    company_id = token_resp.get("companyId", "")
    location_id = token_resp.get("locationId", "")
    access_token = token_resp.get("access_token", "")
    refresh_token = token_resp.get("refresh_token", "")
    expires_in = token_resp.get("expires_in")
    scope_str = token_resp.get("scope", "")

    expires_at = None
    if expires_in:
        try:
            expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            pass

    if user_type == GhlInstall.USER_TYPE_COMPANY:
        if not company_id:
            return HttpResponse("Company token missing companyId.", status=502)
        app_id = (settings.GHL_CLIENT_ID or "").split("-")[0]
        try:
            locations = ghl_oauth.list_installed_locations(
                agency_access_token=access_token, company_id=company_id, app_id=app_id
            )
        except ghl_oauth.TokenExchangeFailed as exc:
            logger.exception("GHL callback: installedLocations failed")
            return HttpResponse(f"Could not list sub-accounts: {exc}", status=502)
        GhlAgencyInstall.objects.update_or_create(
            company_id=company_id,
            defaults={
                "access_token": encrypt_token(access_token),
                "refresh_token": encrypt_token(refresh_token),
                "expires_at": expires_at,
                "scopes": scope_str.split() if scope_str else [],
                "available_locations": locations,
            },
        )
        logger.info("GHL agency install: company=%s locations=%d", company_id, len(locations))
        return redirect(f"{reverse('dashboard:integrations')}?connected=1")

    if not location_id:
        return HttpResponse("Token response missing locationId.", status=502)

    install, created = GhlInstall.objects.update_or_create(
        location_id=location_id,
        defaults={
            "company_id": company_id,
            "user_type": user_type,
            "access_token": encrypt_token(access_token),
            "refresh_token": encrypt_token(refresh_token),
            "expires_at": expires_at,
            "scopes": scope_str.split() if scope_str else [],
            "status": GhlInstall.STATUS_CONNECTED,
        },
    )
    tenant = Tenant.objects.filter(ghl_location_id=location_id).first()
    if tenant and install.tenant_id != tenant.id:
        install.tenant = tenant
        install.save(update_fields=["tenant", "updated_at"])
    logger.info("GHL install %s: location=%s", "created" if created else "refreshed", location_id)
    return redirect(f"{reverse('dashboard:integrations')}?connected=1")
```
Update the import line `from .models import GhlInstall, Tenant` to:
```python
from .models import GhlAgencyInstall, GhlInstall, Tenant
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_ghl_callback -v 2`
Expected: PASS (2 tests) **once Task 6's routes exist** — see the "Task 5 ↔ 6 pairing" note at the top. Do Task 6 next.

- [ ] **Step 5: Commit**

```bash
git add core/ghl_views.py core/tests/test_ghl_callback.py
git commit -m "feat(ghl): enumerate sub-accounts on agency install, encrypt install tokens"
```

---

## Task 6: Integrations page + bind endpoint + routes

**Files:**
- Modify: `dashboard/views.py`, `dashboard/urls.py`
- Test: `core/tests/test_integrations_views.py`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_integrations_views.py`:
```python
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from core.ghl_crypto import encrypt_token
from core.models import GhlAgencyInstall, GhlInstall, Tenant, Template

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app-ver",
                   GHL_CLIENT_SECRET="s", ALLOWED_HOSTS=["testserver"], TENANT_BASE_DOMAIN="localhost")
class IntegrationsViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.template = Template.objects.create(name="T", html_source="<div></div>")
        self.tenant = Tenant.objects.create(name="Acme", subdomain="acme", template=self.template)
        self.agency = GhlAgencyInstall.objects.create(
            company_id="co_1",
            access_token=encrypt_token("agency-access"),
            refresh_token=encrypt_token("agency-refresh"),
            expires_at=timezone.now() + timedelta(hours=1),
            available_locations=[{"id": "loc_a", "name": "Acme HQ"}],
        )

    def test_integrations_page_lists_agency(self):
        resp = self.client.get(reverse("dashboard:integrations"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "co_1")
        self.assertContains(resp, "Acme HQ")

    def test_bind_creates_install(self):
        mint = {"access_token": "la", "refresh_token": "lr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.mint_location_token", return_value=mint):
            resp = self.client.post(reverse("dashboard:integrations_bind"), {
                "agency_id": self.agency.pk, "location_id": "loc_a", "tenant_id": self.tenant.pk,
            })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(GhlInstall.objects.filter(location_id="loc_a", tenant=self.tenant).exists())

    def test_bind_rejects_location_already_on_other_tenant(self):
        other = Tenant.objects.create(name="Beta", subdomain="beta", template=self.template,
                                      ghl_location_id="loc_a")
        resp = self.client.post(reverse("dashboard:integrations_bind"), {
            "agency_id": self.agency.pk, "location_id": "loc_a", "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(GhlInstall.objects.filter(location_id="loc_a", tenant=self.tenant).exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_integrations_views -v 2`
Expected: FAIL (`dashboard:integrations` route missing).

- [ ] **Step 3: Add routes**

In `dashboard/urls.py`, add inside `urlpatterns` (agency section):
```python
    path("integrations/", views.integrations, name="integrations"),
    path("integrations/bind/", views.integrations_bind, name="integrations_bind"),
    path("integrations/reconnect/", views.integrations_reconnect, name="integrations_reconnect"),
    path("integrations/disconnect/", views.integrations_disconnect, name="integrations_disconnect"),
    path("integrations/refresh-locations/", views.integrations_refresh_locations,
         name="integrations_refresh_locations"),
```

- [ ] **Step 4: Add the two views (list + bind)**

In `dashboard/views.py`, add imports near the top (with the other `from core...` imports):
```python
from core import ghl_oauth
from core.models import GhlAgencyInstall, GhlInstall
from core.services import ghl_connect
```
Add the views (near the other `@agency_operator_required` views):
```python
@agency_operator_required
def integrations(request):
    agencies = GhlAgencyInstall.objects.all()
    installs = (
        GhlInstall.objects.select_related("agency", "tenant").order_by("-installed_at")
    )
    bound_location_ids = set(
        GhlInstall.objects.exclude(tenant__isnull=True).values_list("location_id", flat=True)
    )
    tenants = Tenant.objects.order_by("name")
    return render(request, "dashboard/integrations.html", {
        "agencies": agencies,
        "installs": installs,
        "bound_location_ids": bound_location_ids,
        "tenants": tenants,
        "just_connected": request.GET.get("connected") == "1",
    })


@agency_operator_required
@require_POST
def integrations_bind(request):
    agency = get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))
    location_id = (request.POST.get("location_id") or "").strip()
    tenant = get_object_or_404(Tenant, pk=request.POST.get("tenant_id"))
    clash = (
        GhlInstall.objects.filter(location_id=location_id).exclude(tenant=tenant).exists()
        or Tenant.objects.filter(ghl_location_id=location_id).exclude(pk=tenant.pk).exists()
    )
    if clash:
        messages.error(request, "That sub-account is already linked to another site.")
        return redirect("dashboard:integrations")
    try:
        ghl_connect.bind_location(agency=agency, location_id=location_id, tenant=tenant)
        messages.success(request, f"Connected “{tenant.name}” to sub-account {location_id}.")
    except ghl_oauth.TokenExchangeFailed as exc:
        messages.error(request, f"Could not connect: {exc}")
    return redirect("dashboard:integrations")
```
Confirm `require_POST`, `get_object_or_404`, `messages`, `redirect`, `render` are already imported at the top of `dashboard/views.py` (they are used elsewhere in the file); add any that are missing.

- [ ] **Step 5: Create a minimal template so the page renders**

Create `templates/dashboard/integrations.html`:
```html
{% extends "base.html" %}
{% block content %}
<div class="page">
  <h1>Integrations</h1>
  <a class="btn btn-purple" href="/connect/install/">Connect GHL agency</a>

  {% for agency in agencies %}
    <section class="stat-card">
      <h2>Agency {{ agency.company_id }}{% if agency.company_name %} — {{ agency.company_name }}{% endif %}</h2>
      <table class="data-table">
        <thead><tr><th>Sub-account</th><th>ID</th><th>Bind to site</th></tr></thead>
        <tbody>
          {% for loc in agency.available_locations %}
          <tr>
            <td>{{ loc.name|default:"(unnamed)" }}</td>
            <td><code>{{ loc.id }}</code></td>
            <td>
              {% if loc.id in bound_location_ids %}
                <span class="badge badge-purple">bound</span>
              {% else %}
                <form method="post" action="{% url 'dashboard:integrations_bind' %}">
                  {% csrf_token %}
                  <input type="hidden" name="agency_id" value="{{ agency.pk }}">
                  <input type="hidden" name="location_id" value="{{ loc.id }}">
                  <select name="tenant_id" required>
                    <option value="">Choose site…</option>
                    {% for t in tenants %}<option value="{{ t.pk }}">{{ t.name }}</option>{% endfor %}
                  </select>
                  <button class="btn" type="submit">Bind</button>
                </form>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>
  {% empty %}
    <p>No GHL agency connected yet. Click “Connect GHL agency” to start.</p>
  {% endfor %}

  <h2>Connected sub-accounts</h2>
  <table class="data-table">
    <thead><tr><th>Location</th><th>Site</th><th>Status</th><th></th></tr></thead>
    <tbody>
      {% for i in installs %}
      <tr>
        <td>{{ i.location_name|default:i.location_id }}</td>
        <td>{{ i.tenant.name|default:"—" }}</td>
        <td>{{ i.get_status_display }}</td>
        <td>
          <form method="post" action="{% url 'dashboard:integrations_reconnect' %}" style="display:inline">
            {% csrf_token %}<input type="hidden" name="install_id" value="{{ i.pk }}">
            <button class="btn" type="submit">Reconnect</button>
          </form>
          <form method="post" action="{% url 'dashboard:integrations_disconnect' %}" style="display:inline">
            {% csrf_token %}<input type="hidden" name="install_id" value="{{ i.pk }}">
            <button class="btn btn-danger" type="submit">Disconnect</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_integrations_views core.tests.test_ghl_callback -v 2`
Expected: PASS (integrations list + bind + reject; callback 302s resolve now).

- [ ] **Step 7: Commit**

```bash
git add dashboard/views.py dashboard/urls.py templates/dashboard/integrations.html core/tests/test_integrations_views.py
git commit -m "feat(ghl): integrations page + bind sub-account to site"
```

---

## Task 7: Reconnect, disconnect, refresh-locations endpoints

**Files:**
- Modify: `dashboard/views.py`
- Test: `core/tests/test_integrations_views.py` (extend)

- [ ] **Step 1: Add the failing tests**

Append to `core/tests/test_integrations_views.py` inside `IntegrationsViewTests`:
```python
    def test_reconnect_remints(self):
        install = GhlInstall.objects.create(
            location_id="loc_a", agency=self.agency, tenant=self.tenant,
            access_token=encrypt_token("old"), status=GhlInstall.STATUS_DISCONNECTED,
        )
        mint = {"access_token": "new", "refresh_token": "nr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.mint_location_token", return_value=mint):
            resp = self.client.post(reverse("dashboard:integrations_reconnect"),
                                    {"install_id": install.pk})
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)

    def test_disconnect_marks_disconnected(self):
        install = GhlInstall.objects.create(
            location_id="loc_a", agency=self.agency, tenant=self.tenant,
            access_token=encrypt_token("x"), status=GhlInstall.STATUS_CONNECTED,
        )
        resp = self.client.post(reverse("dashboard:integrations_disconnect"),
                                {"install_id": install.pk})
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.assertEqual(install.status, GhlInstall.STATUS_DISCONNECTED)

    def test_refresh_locations_updates_list(self):
        new_locs = [{"id": "loc_a", "name": "Acme HQ"}, {"id": "loc_c", "name": "Gamma"}]
        with mock.patch("core.services.ghl_connect.ensure_fresh_agency_token", return_value="tok"), \
             mock.patch("core.ghl_oauth.list_installed_locations", return_value=new_locs):
            resp = self.client.post(reverse("dashboard:integrations_refresh_locations"),
                                    {"agency_id": self.agency.pk})
        self.assertEqual(resp.status_code, 302)
        self.agency.refresh_from_db()
        self.assertEqual(self.agency.available_locations, new_locs)
```

- [ ] **Step 2: Run to verify failure**

Run: `python manage.py test core.tests.test_integrations_views -v 2`
Expected: FAIL (reconnect/disconnect/refresh views missing).

- [ ] **Step 3: Implement the three views**

In `dashboard/views.py` add:
```python
@agency_operator_required
@require_POST
def integrations_reconnect(request):
    install = get_object_or_404(GhlInstall, pk=request.POST.get("install_id"))
    try:
        ghl_connect.reconnect_install(install)
        messages.success(request, f"Reconnected {install.location_id}.")
    except ghl_oauth.TokenExchangeFailed as exc:
        messages.error(request, f"Reconnect failed: {exc}")
    return redirect("dashboard:integrations")


@agency_operator_required
@require_POST
def integrations_disconnect(request):
    install = get_object_or_404(GhlInstall, pk=request.POST.get("install_id"))
    install.status = GhlInstall.STATUS_DISCONNECTED
    install.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Disconnected {install.location_id}.")
    return redirect("dashboard:integrations")


@agency_operator_required
@require_POST
def integrations_refresh_locations(request):
    agency = get_object_or_404(GhlAgencyInstall, pk=request.POST.get("agency_id"))
    app_id = (settings.GHL_CLIENT_ID or "").split("-")[0]
    try:
        token = ghl_connect.ensure_fresh_agency_token(agency)
        agency.available_locations = ghl_oauth.list_installed_locations(
            agency_access_token=token, company_id=agency.company_id, app_id=app_id
        )
        agency.save(update_fields=["available_locations", "updated_at"])
        messages.success(request, "Sub-account list refreshed.")
    except ghl_oauth.TokenExchangeFailed as exc:
        messages.error(request, f"Refresh failed: {exc}")
    return redirect("dashboard:integrations")
```
Confirm `settings` is imported at the top of `dashboard/views.py`; add `from django.conf import settings` if missing.

- [ ] **Step 4: Run to verify pass**

Run: `python manage.py test core.tests.test_integrations_views -v 2`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py core/tests/test_integrations_views.py
git commit -m "feat(ghl): reconnect, disconnect, and refresh-locations endpoints"
```

---

## Task 8: Nav link + site-detail dropdown

**Files:**
- Modify: `templates/base.html`, `templates/dashboard/tenant_detail.html`
- Test: `core/tests/test_integrations_views.py` (extend for the dropdown render)

- [ ] **Step 1: Add a failing render test**

Append to `IntegrationsViewTests`:
```python
    def test_site_detail_shows_connected_subaccount_dropdown(self):
        resp = self.client.get(reverse("dashboard:tenant_detail", args=[self.tenant.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Link a connected sub-account")
        self.assertContains(resp, "loc_a")
```

- [ ] **Step 2: Run to verify failure**

Run: `python manage.py test core.tests.test_integrations_views.IntegrationsViewTests.test_site_detail_shows_connected_subaccount_dropdown -v 2`
Expected: FAIL (text not present).

- [ ] **Step 3: Provide the dropdown data to the detail view**

In `dashboard/views.py`, find `tenant_detail` and add to its context dict (compute the unbound sub-accounts across agencies):
```python
    connectable = []
    for agency in GhlAgencyInstall.objects.all():
        for loc in agency.available_locations:
            connectable.append({"agency_id": agency.pk, "id": loc.get("id"),
                                "name": loc.get("name", "")})
```
Add `"connectable_subaccounts": connectable` to the `render(...)` context for `tenant_detail.html`.

- [ ] **Step 4: Replace the raw field in `tenant_detail.html`**

**Important: HTML forms cannot nest.** The bind form posts to `integrations_bind`; the manual field belongs to the existing settings form. So this is a **two-part** edit.

**Part A — inside the existing settings `<form>`**, replace the "GHL location ID" `<label>`/`<input>` block (around lines 261-265) with just the advanced fallback (still a plain field the settings POST submits):
```html
<details style="margin-top:8px">
  <summary class="field-hint">Advanced: set GHL location ID manually</summary>
  <input class="input" type="text" name="ghl_location_id" id="settings_ghl_location_id"
         value="{{ tenant.ghl_location_id|default:'' }}">
  <span class="field-hint">When set, this client can auto-log in from their GHL sub-account via the embedded menu link.</span>
</details>
```

**Part B — as its own block OUTSIDE (before or after) the settings `<form>`, not nested in it**, add the connected-sub-account picker (its own form → `integrations_bind`):
```html
<div class="field">
  <label class="field-label">Link a connected sub-account</label>
  {% if connectable_subaccounts %}
  <form method="post" action="{% url 'dashboard:integrations_bind' %}">
    {% csrf_token %}
    <input type="hidden" name="tenant_id" value="{{ tenant.pk }}">
    <input type="hidden" name="agency_id" value="">
    <input type="hidden" name="location_id" value="">
    <select name="__combo" required
            onchange="const o=this.selectedOptions[0]; this.form.agency_id.value=o.dataset.agency||''; this.form.location_id.value=o.value;">
      <option value="">Choose sub-account…</option>
      {% for s in connectable_subaccounts %}
        <option value="{{ s.id }}" data-agency="{{ s.agency_id }}">{{ s.name|default:s.id }} ({{ s.id }})</option>
      {% endfor %}
    </select>
    <button class="btn" type="submit">Link</button>
  </form>
  {% else %}
  <p class="field-hint">No connected agency yet. <a href="{% url 'dashboard:integrations' %}">Connect one</a>.</p>
  {% endif %}
</div>
```
The manual `ghl_location_id` input (Part A) stays inside the settings form so `tenant_settings_update` (`dashboard/views.py:1100`) still works as the fallback; the picker (Part B) is a separate form so no forms are nested.

- [ ] **Step 5: Add the nav link**

In `templates/base.html`, in the agency-nav branch (where links like Sites / Users render), add:
```html
<a href="{% url 'dashboard:integrations' %}">Integrations</a>
```

- [ ] **Step 6: Run to verify pass**

Run: `python manage.py test core.tests.test_integrations_views -v 2`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add templates/base.html templates/dashboard/tenant_detail.html dashboard/views.py core/tests/test_integrations_views.py
git commit -m "feat(ghl): Integrations nav link + connected sub-account dropdown on site detail"
```

---

## Task 9: Env docs + full suite + admin registration

**Files:**
- Modify: `.env.example`, `core/admin.py`

- [ ] **Step 1: Document env vars**

In `.env.example`, in the GHL section add:
```
# Fernet key for encrypting GHL tokens at rest. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
GHL_TOKEN_ENCRYPTION_KEY=
# Optional: explicit GHL app version id (defaults to GHL_CLIENT_ID prefix).
GHL_APP_VERSION_ID=
```

- [ ] **Step 2: Register the new model in admin (read-only tokens)**

In `core/admin.py` add:
```python
from core.models import GhlAgencyInstall


@admin.register(GhlAgencyInstall)
class GhlAgencyInstallAdmin(admin.ModelAdmin):
    list_display = ("company_id", "company_name", "expires_at", "updated_at")
    search_fields = ("company_id", "company_name")
    readonly_fields = ("access_token", "refresh_token", "available_locations",
                       "installed_at", "updated_at")
```

- [ ] **Step 3: Run the full suite**

Run: `python manage.py test core dashboard -v 2`
Expected: PASS across all existing + new tests. Investigate any failure before continuing.

- [ ] **Step 4: Manual smoke against a dev server**

Run: `python manage.py migrate && python manage.py runserver`
- Log in as a superuser at `localhost:8000/login/`.
- Visit `localhost:8000/dashboard/integrations/` → page renders, "Connect GHL agency" button present.
- (Full OAuth requires real GHL creds + `GHL_TOKEN_ENCRYPTION_KEY`; the page-render + route wiring is the local check.)

- [ ] **Step 5: Commit**

```bash
git add .env.example core/admin.py
git commit -m "chore(ghl): document token-encryption env vars; register agency install in admin"
```

---

## Deployment notes (post-merge, not a code task)

- Set `GHL_TOKEN_ENCRYPTION_KEY` on the `cmsdashboard-sites-2ka9w7` compose via the Dokploy MCP **before** deploying — the encryption helpers fail closed without it, so the callback/bind will 500 until it's set.
- Deploy auto-applies migrations (entrypoint). Verify with `curl -sS -o /dev/null -w "%{http_code} %{redirect_url}" https://sites.katek.app/dashboard/integrations/` (302 → login when logged out).
- Then run one real agency install end-to-end and bind a single sub-account.
- Fast-follows (separate specs): webhook signature verification + uninstall handling; operator-level agency isolation (Shape 2); flip prod `DJANGO_DEBUG=0`.
