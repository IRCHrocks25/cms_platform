# Design: In-app GHL "Connect" + agency-level multi-location install

**Date:** 2026-07-10
**Author:** Bernard (with Claude)
**Status:** Draft — awaiting review
**Repo:** `IRCHrocks25/cms_platform` (sites.katek.app)

---

## 1. Problem

Today, connecting a GoHighLevel sub-account to a CMS site is a manual, single-agency, copy-paste affair:

- There is **no Connect button** in the dashboard. The marketplace OAuth flow (`/connect/install/`) works but is an unlinked URL; installs originate from GHL's side.
- Linking a sub-account to a site means **pasting a raw `location_id`** into a text field on the site detail page (`tenant_detail.html:261`).
- **Agency (Company-level) installs don't enumerate sub-accounts.** The callback records a `company:<id>` placeholder and stops (`ghl_views.py:175` TODO). So "installed for the agency" ≠ "each sub-account connectable."
- There is **no first-class Agency entity**. `GhlInstall.company_id` is captured but unused; every `is_staff` operator sees every site.
- There is **no token refresh**. GHL location tokens expire (~24h), so any future API use would 401 the next day.

We want: a **Connect button inside the CMS**, an **agency-install-once → bind-any-sub-account** flow (including re-including sub-accounts missed earlier), built so it supports **multiple agencies**.

The IBC product (`IRCHrocks25/Intelligent_Busines_Center`) already implements this exact pattern; this design ports it into the Django CMS, adapted to the CMS's "one agency, many sub-accounts-as-sites" model.

---

## 2. Goals / Non-goals

### Goals
1. A **"Connect GHL agency"** button in the agency dashboard that starts the OAuth flow (no more bare URL).
2. Handle the **Company (agency-owner) install**: enumerate the agency's sub-accounts via `installedLocations`, store the agency-level refresh token.
3. A **sub-account picker**: bind any enumerated sub-account to a CMS site by minting a location token (`/oauth/locationToken`).
4. **Re-include / reconnect any sub-account at any time** from the stored agency token, with **no new OAuth** — this is the direct answer to "a sub-account wasn't included, how do I re-include it?"
5. **Multi-agency at the credential layer**: N agencies each install once; each keyed by `company_id`.
6. **Token refresh-before-use** so connections stay live.
7. **Encrypt stored tokens** at rest (the `GhlInstall` docstring already flags this as the production-hardening step).

### Non-goals (explicitly out of scope for this spec)
- **Operator-level isolation** (agency B's staff cannot see agency A's sites — the "Shape 2" decision). The data model here lays the groundwork (agency FK on installs), but scoping the dashboard per operator is a separate follow-up. This spec assumes Katalyst operators manage all agencies (Shape 1).
- Expanding GHL scopes beyond what's needed to enumerate + name locations. Kept minimal per the "no unused Sensitive scopes" rule.
- Changing the existing **embed/SSO** path (`/embed/`). It keeps working; binding a sub-account now sets `Tenant.ghl_location_id` for it automatically.
- Full CRM data sync (contacts/opportunities/etc.). IBC does this; the CMS does not need it yet.

---

## 3. What already exists (reuse, don't rebuild)

| Primitive | Location | Reuse |
|---|---|---|
| Signed-state OAuth kickoff | `ghl_oauth.sign_state` / `build_install_url`; view `oauth_install` (`ghl_views.py:100`) | Connect button targets this |
| Callback + code→token exchange | `oauth_callback` (`ghl_views.py:121`), `ghl_oauth.exchange_code` | Extend the Company branch |
| Location-token minting | `ghl_oauth.mint_location_token` (`ghl_oauth.py:140`) | Wire into bind flow |
| Installed-locations URL | `ghl_oauth.INSTALLED_LOCATIONS_URL` (`ghl_oauth.py:30`) | Add a function that calls it |
| Per-location install row | `GhlInstall` model (`models.py:491`), keyed by `location_id`, FK `tenant` | Extend |
| Site↔location field | `Tenant.ghl_location_id` (`models.py:93`) | Set automatically on bind |
| Prod creds already set | `GHL_CLIENT_ID/SECRET/SHARED_SECRET`, `GHL_AUTO_LOGIN=1` on compose `cmsdashboard-sites-2ka9w7` | No new app registration needed |

---

## 4. Data model

### New: `GhlAgencyInstall`
One row per connected GHL agency (Company). This is the multi-agency anchor and the home for the agency-level refresh token that lets us mint any sub-account's token later.

```
GhlAgencyInstall
  company_id           CharField, unique          # GHL companyId
  company_name         CharField, blank           # optional, from GHL
  access_token         (encrypted) TextField      # agency-scoped
  refresh_token        (encrypted) TextField      # agency-scoped — the reusable key
  expires_at           DateTimeField, null
  scopes               JSONField (list)
  available_locations  JSONField (list)           # [{id, name}, ...] from installedLocations
  installed_at         DateTimeField (auto_now_add)
  updated_at           DateTimeField (auto_now)
```

> **Improvement over IBC:** IBC stashes the agency token + pending location list inside a single org credential's `metadata` using a sentinel `PENDING_LOCATION_BIND` access token. The CMS models it cleanly instead — the agency install is its own row; "pending" is simply "an agency row exists with `available_locations` but no bound `GhlInstall` yet." No sentinels.

### Changed: `GhlInstall` (per sub-account)
```
GhlInstall
  location_id     CharField, unique               # (existing)
  agency          FK GhlAgencyInstall, null        # NEW — null for direct Location installs
  company_id      CharField, blank                 # (existing; keep, or derive from agency)
  user_type       CharField                        # (existing)
  access_token    (encrypted) TextField            # (existing, now encrypted)
  refresh_token   (encrypted) TextField            # (existing, now encrypted)
  expires_at      DateTimeField, null              # (existing)
  scopes          JSONField                        # (existing)
  location_name   CharField, blank                 # NEW — from installedLocations / GET location
  status          CharField                        # NEW — connected|expired|disconnected
  tenant          FK Tenant, null                  # (existing)
  installed_at / updated_at                        # (existing)
```

### Token encryption
Wrap `access_token`/`refresh_token` reads/writes in Fernet using a new `GHL_TOKEN_ENCRYPTION_KEY` env var (32-byte urlsafe base64). Implemented as an `EncryptedTextField` or explicit encrypt/decrypt helpers in `ghl_oauth.py`. Existing plaintext rows (if any) migrate lazily or via a one-off management command.

---

## 5. Flows

### 5.1 Connect (agency install)
1. Operator opens **Dashboard → Integrations**, clicks **"Connect GHL agency"**.
2. Button links to existing `GET /connect/install/` → `build_install_url` → redirect to `marketplace.leadconnectorhq.com/oauth/chooselocation`.
3. Agency owner authorizes → GHL redirects to `GET /connect/callback/?code=&state=`.
4. `exchange_code` (hint `user_type=Location`; GHL may return `Company`).
   - **`userType == "Location"`** (a single sub-account authorized): create/update a `GhlInstall` for that location (current behavior), `status=connected`. No agency row.
   - **`userType == "Company"`** (agency owner): 
     a. Upsert `GhlAgencyInstall` (company_id, agency tokens, scopes).
     b. Call **`list_installed_locations(agency_access_token, company_id, app_version_id)`** (new fn hitting `INSTALLED_LOCATIONS_URL`) → store `available_locations`.
     c. Redirect to **Integrations** with the sub-account picker shown.

### 5.2 Bind a sub-account to a site
1. On Integrations, each agency shows its `available_locations`. Operator picks a location and the CMS site (`Tenant`) it should drive.
2. `POST /dashboard/integrations/bind/` → `mint_location_token(agency_access_token, company_id, location_id)` → upsert `GhlInstall(location_id, agency=…, tenant=…, tokens, location_name, status=connected)` **and** set `Tenant.ghl_location_id = location_id` (so the embed/SSO path lights up too).
3. Guard: a `location_id` already bound to another tenant is rejected (mirror the existing check at `dashboard/views.py:1102`).

### 5.3 Re-include / reconnect a sub-account (the key ask)
- A sub-account that wasn't bound is simply **still in `available_locations`** — pick it and bind (5.2). No new OAuth.
- If a bound install expired/disconnected, **Reconnect** re-mints from the stored agency refresh token: refresh agency token (`/oauth/token`, Company grant) → `mint_location_token` → update `GhlInstall`, `status=connected`. No user consent screen.
- If the agency added *new* sub-accounts in GHL since last install, a **"Refresh sub-account list"** action re-calls `list_installed_locations` and updates `available_locations`.

### 5.4 Token refresh-before-use
- `ensure_fresh_token(install)` helper: if `expires_at - now < 60s`, refresh (`/oauth/token`, `refresh_token` grant; Location grant for `GhlInstall`, Company grant for `GhlAgencyInstall`) and persist. Called before any GHL API request.

### 5.5 Embed/SSO (unchanged)
`/embed/` continues to match `location_id → Tenant.ghl_location_id`. Binding (5.2) now sets that field, so the manual paste becomes unnecessary (field stays as a fallback/override).

---

## 6. UI surfaces

1. **New: Dashboard → Integrations** (`/dashboard/integrations/`, `agency_operator_required`):
   - "Connect GHL agency" button (→ `/connect/install/`).
   - Per connected agency: company name, status, and the **sub-account picker** (list of `available_locations` with a "Bind to site…" control), plus "Refresh list".
   - Connected sub-accounts: which `Tenant` each drives, with **Reconnect** / **Disconnect**.
2. **Site detail page** (`tenant_detail.html`): replace the raw "GHL location ID" text input with a **"Link a connected sub-account"** dropdown (populated from unbound `available_locations`); keep the raw field behind an "advanced" toggle as a fallback.
3. **`install_success.html`**: redirect into the Integrations picker instead of telling the user to copy-paste an ID.

---

## 7. Endpoints / routes

| Route | Method | Decorator | Purpose |
|---|---|---|---|
| `/connect/install/` | GET | (none; needs client id) | existing — Connect button target |
| `/connect/callback/` | GET | (none) | existing — extend Company branch (§5.1) |
| `/dashboard/integrations/` | GET | `agency_operator_required` | new — connect UI + picker |
| `/dashboard/integrations/bind/` | POST | `agency_operator_required` | new — mint + bind location→tenant |
| `/dashboard/integrations/reconnect/` | POST | `agency_operator_required` | new — re-mint from agency token |
| `/dashboard/integrations/disconnect/` | POST | `agency_operator_required` | new — mark disconnected |
| `/dashboard/integrations/refresh-locations/` | POST | `agency_operator_required` | new — re-enumerate sub-accounts |
| `/connect/webhook/` | POST | csrf-exempt | existing stub — add signature verify + uninstall handling (adjacent; see §9) |

---

## 8. Scopes

Keep minimal: **`locations.readonly`** (already the default in `ghl_oauth.DEFAULT_SCOPES`) — sufficient to enumerate installed locations and read names. IBC requests 36 scopes because it syncs the full CRM; the CMS does not. Add scopes only when a concrete feature needs them, and never tick Sensitive-flagged scopes we don't call.

---

## 9. Security

- **Token encryption at rest** (§4) — a hard requirement now that we store *many* agencies' reusable refresh tokens.
- **State token** already HMAC-signed with a 30-min TTL (`ghl_oauth.sign_state`) — keep.
- **Webhook signature verification** — the current `webhook` view is a no-verify stub. Add shared-secret/Ed25519 verification before acting on `INSTALL`/`UNINSTALL` events; on `UNINSTALL`, mark the relevant installs `disconnected`. (Adjacent to this spec; include if cheap, else fast-follow.)
- **All new dashboard endpoints** use `agency_operator_required`.
- **Prerequisite / related debt:** prod currently runs `DJANGO_DEBUG=1`. With encrypted GHL tokens in play this is worse — a 500 debug page would expose env + tracebacks. Flip to `0` as part of shipping this (tracked separately as CMS debt).

---

## 10. Multi-agency scoping

- `GhlAgencyInstall.company_id` unique ⇒ **N agencies supported**; each installs once.
- Each `GhlInstall` (sub-account) FKs its `GhlAgencyInstall`; each drives one `Tenant`.
- **Deferred (non-goal):** operator isolation. Today any staff manages all agencies. When Shape 2 is needed, add an `Agency`/membership layer and scope the dashboard + `agency_operator_required` by it; the agency FK added here is the seam.
- **Open GHL question to verify before relying on it for *independent* agencies:** whether one shared marketplace app surfaces in each agency's *whitelabeled* GHL marketplace, or each independent agency needs its own app (→ per-agency client_id/secret stored on `GhlAgencyInstall`). Not required for the current Katalyst-operated whitelabels; flagged for when truly independent agencies onboard.

---

## 11. Testing

- **Unit** (mock httpx): `exchange_code` Company vs Location; `list_installed_locations` parse; `mint_location_token`; `ensure_fresh_token` (fresh / near-expiry / refresh failure); Fernet encrypt/decrypt round-trip; state sign/verify TTL.
- **View** (Django test client): callback Company branch (0 / 1 / >1 locations); bind endpoint (success, duplicate-location rejection, mint failure); reconnect; disconnect. Follow `core/tests/test_ghl_embed.py` conventions.
- **Migration**: apply cleanly on a fresh DB; existing `GhlInstall` rows survive.

---

## 12. Rollout

1. Migrations: `0019_ghlagencyinstall` + `GhlInstall` field additions.
2. New env var `GHL_TOKEN_ENCRYPTION_KEY` — set on the `cmsdashboard-sites-2ka9w7` compose (Dokploy MCP) before deploy; without it, encryption helpers must fail closed.
3. Ship code; deploy auto-applies migrations (entrypoint) — verify by curling `/dashboard/integrations/` (302→login when logged out) and re-running an agency install end to end on one real sub-account.
4. Backfill: existing manually-set `Tenant.ghl_location_id` sites keep working via embed; they gain a `GhlInstall` only when an agency install binds them.

---

## 13. Resolved decisions (confirmed 2026-07-10)

1. **Operator isolation** — **Shape 1** for now (Katalyst operates all agencies). The agency FK added here is the seam for Shape 2 later.
2. **Webhook signature verification** — **fast-follow**, not in this build. Keeps scope tight; the connect flow doesn't depend on it.
3. **Site-detail UI** — **replace** the raw location-ID field with a connected-sub-account **dropdown**; keep the raw field behind an "advanced" toggle as a fallback.
4. **Token encryption** — **add now.** Storing many agencies' reusable refresh tokens in plaintext is an unacceptable risk.
