# Katek central identity in CMS

The CMS can act as a default-off OpenID Connect relying party for Katek customer identity. Password login remains available and unchanged. Django continues to own users, tenant memberships, roles, sessions, and site permissions. The existing django-oauth-toolkit MCP authorization server is unchanged; central identity can satisfy only its interactive login step.

## Client library

The relying party uses `mozilla-django-oidc==5.0.2`. It is a maintained, focused authorization-code client that supplies state, nonce, PKCE, callback handling, and JWKS verification without adding a second account or social-account model. `django-allauth` was not selected because its account, provider, adapter, signup, and linking surfaces would overlap the CMS's existing tenant-aware Django login.

The CMS backend adds the policy that the library does not own:

- RS256 only, with JWKS `kid` matching
- exact issuer, CMS client audience, authorized party, expiry, and nonce validation
- immutable `(issuer, subject)` lookup, with no email-only sign-in
- fresh linking from a local password session less than 15 minutes old
- fresh IdP authentication less than five minutes old
- equal verified local and IdP email addresses
- local tenant membership and active registry entitlement before admission

## Account linking

An operator first records a `VerifiedUserEmail` for the local user through the Django admin or a future verified-email workflow. The user signs in with the existing site username and password, then visits `/auth/katek/link/`. That route requires the live recent local session and forces the IdP to authenticate again with `prompt=login&max_age=0`.

The callback creates a `CentralIdentityLink` only after all proofs pass. Unknown central subjects cannot create a Django user and cannot find one by email. A subject can belong to one local user, and one local user can have one subject for an issuer. Link deletion is deliberately unavailable in the admin because unlink/recovery policy is outside this slice.

## Tenant registry boundary

`Tenant.global_tenant_id` is the immutable business UUID. Only the future provisioning/reconciliation service writes registry bindings; callbacks and CMS requests are read-only consumers. A global tenant id is never reused or reassigned.

The CMS calls:

```http
GET /v1/businesses/{globalTenantId}/product-entitlements/cms
Authorization: Bearer <application credential>
Accept: application/json
```

The response must be active and must match the tenant's stable local primary key. Missing, suspended, revoked, malformed, mismatched, timed-out, and failed responses deny access. A successful decision is stored in the host-scoped Django session for at most five minutes. Every session load after that limit rechecks the registry. Moving the same session to another tenant host also forces a check, even inside the five-minute window.

## Configuration

Set `KATEK_OIDC_ENABLED=true` only with every required setting present. Partial enabled configuration fails startup and names missing variables. Disabled mode registers no authentication backend, shows no Katek action, and the Katek routes return 404.

Required settings are documented in `.env.example`. All issuer, endpoint, and registry URLs must use HTTPS. Each enabled public host needs its exact callback registered:

```text
https://<host>/auth/katek/callback/
```

CMS tenant and registered custom-domain sessions remain host scoped according to the existing cookie middleware. Wildcard redirect URIs are outside the contract.

## Acceptance still required

Local fixtures verify the protocol and policy, but they do not replace an installed-account check. Before enabling the flag in a shared environment, run a real IdP sign-in and link, registry suspension/revocation, tenant isolation, custom-domain callback, and External MCP consent flow. Check the login page at desktop and 390px. No shared environment or identity-provider resource is changed by this implementation.
