# KPILOT-346 Staging Tenant Hosts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give staging tenants valid-TLS public and login hosts so External MCP OAuth can complete, while leaving production tenant hosts unchanged.

**Architecture:** Keep the shared tenant base domain at `sites.katek.app` and add an optional tenant-label suffix. Staging sets the suffix to `-staging`, producing one-level hosts such as `acme-staging.sites.katek.app`, which are covered by the existing wildcard certificate and DNS. Dedicated high-priority Traefik routers send only suffixed hosts to staging. Production leaves the suffix empty and continues to use `acme.sites.katek.app`. Agency-host login preserves a validated OAuth authorization `next` path for tenant members so the consent flow does not get lost in a tenant-login redirect.

**Tech Stack:** Django, Django test runner, Docker Compose, Traefik labels, Markdown operations documentation.

### Task 1: Specify tenant host generation and resolution

**Files:**
- Modify: `core/tests/test_middleware.py`
- Modify: `core/tests/test_tenant_url_custom_domain.py`

1. Add a failing middleware test that resolves `acme-staging.sites.example.test` to tenant `acme` when `TENANT_SUBDOMAIN_SUFFIX=-staging`.
2. Add failing negative tests proving unsuffixed and nested staging tenant hosts do not resolve through the suffix contract.
3. Add a failing URL-helper test that generates `https://acme-staging.sites.example.test/`.
4. Run the targeted tests under `flock ~/.cache/katalyst-build-gate.lock` and record the expected failures.

### Task 2: Specify OAuth login continuity

**Files:**
- Modify: `core/tests/test_scoped_logins.py`

1. Add a failing agency-login test for a non-staff tenant member with a safe `/authorize/` next path.
2. Assert the user is authenticated on the agency host and redirected to the original authorization path, preserving its query string.
3. Keep an orphan user refused even when they submit a next path.
4. Run the targeted test under the flock lock and record the expected failure.

### Task 3: Implement the staging host contract

**Files:**
- Modify: `cms_platform/settings.py`
- Create: `core/tenant_hosts.py`
- Modify: `core/middleware.py`
- Modify: `core/urls_helpers.py`
- Modify: `core/auth_views.py`
- Modify: `.env.example`

1. Read the optional `TENANT_SUBDOMAIN_SUFFIX` setting, defaulting to an empty string.
2. Centralize tenant-label composition and parsing.
3. Apply the suffix to canonical non-local tenant URLs and tenant middleware resolution.
4. Preserve local-development behavior and production's empty-suffix host shape.
5. Preserve safe same-host authorization destinations during agency-host login for users who belong to a tenant.
6. Run the targeted test set under the flock lock until green.

### Task 4: Specify and implement staging ingress

**Files:**
- Create: `core/tests/test_staging_tenant_hosts.py`
- Modify: `docker-compose.staging.yml`
- Modify: `core/tests/test_compose_env_passthrough.py`

1. Add contract tests that require the staging base domain and `-staging` suffix environment values.
2. Require dedicated HTTP and HTTPS staging tenant routers for `^[a-z0-9-]+-staging\\.sites\\.katek\\.app$`, using staging's service names and priority 200.
3. Assert the production compose contract retains its existing unsuffixed priority-10 tenant routers and does not configure a suffix.
4. Run the compose contract tests under the flock lock, observe red, update compose, and rerun to green.

### Task 5: Document operations and acceptance

**Files:**
- Modify: `deploy/STAGING.md`

1. Document the agency and tenant staging host patterns.
2. Explain why the one-level pattern works with the existing Universal/Origin wildcard certificate and wildcard DNS.
3. Document router priority isolation from production and state that custom domains remain disabled on staging.
4. Document the live acceptance sequence: TLS check, tenant login, External MCP OAuth consent, and successful MCP connection.

### Task 6: Verify and hand off

1. Run focused Django and compose tests under the flock lock.
2. Run the repository's broader appropriate test/lint gates under the flock lock.
3. Review the diff and confirm production compose host rules are unchanged.
4. Commit atomically, then push once through the flock lock.
5. Open a PR without merging it.
6. Add a Plane comment headed **Testing / acceptance evidence** with commands, environment, observed results, and explicit unrun staging checks.
7. Move KPILOT-346 to In Review and stop. KPILOT-318 stays blocked until the staging TLS/login/OAuth checks pass after deployment.
