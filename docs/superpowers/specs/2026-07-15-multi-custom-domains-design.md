# Multiple custom domains per tenant (list management)

Date: 2026-07-15
Status: Approved (design)

## Problem

The per-tenant "Custom Domain" panel in the agency dashboard manages a single
domain. It reads `tenant.custom_domains.order_by("-created_at").first()` in
`_render_custom_domain_partial`, `tenant_custom_domain_verify`, and
`tenant_custom_domain_delete`. Adding a second domain creates the row (the
`add` view and the route-syncer both handle N domains fine) but the panel only
ever surfaces the newest one, so the older domain becomes invisible and cannot
be verified or removed from the site page.

This surfaced with Lemec Advisors: `lemecadvisors.com` (apex) is a verified
`CustomDomain` and serves correctly, but `www.lemecadvisors.com` has no row, so
Traefik has no router/cert for it and answers `www` with its default
self-signed cert (browser cert error). The fix for that instance is to add
`www` as a second domain, which the current single-slot UI cannot manage.

## Goal

Turn the per-tenant panel into a list so an operator can add, verify, and
remove any number of domains per tenant independently.

## Non-goals (explicitly out of scope)

- Auto-creating a `www` sibling when an apex is added (or vice versa).
- A canonical/primary domain concept.
- 301 redirects between domains. Every verified domain keeps serving the site
  directly (HTTP 200), as it does today.

## What already supports multiple domains (unchanged)

- `core/models.py::CustomDomain` — FK to `Tenant` (`related_name="custom_domains"`),
  `domain` is globally `unique=True`. Already many-per-tenant.
- `core/services/traefik_routes.py` — the route-syncer regenerates the dynamic
  file from **all** verified `CustomDomain` rows, one Traefik `Host()` router +
  `certResolver=letsencrypt` each. No change needed.
- `dashboard/views.py::custom_domain_list` — the agency-wide "all domains across
  all tenants" page already lists every row. No change needed.

## Changes

All changes are confined to the per-tenant custom-domain surface.

### 1. `dashboard/views.py`

- `_render_custom_domain_partial(request, tenant, *, error=None, info=None)`:
  pass a **list** of the tenant's domains instead of a single `custom_domain`.
  Each list item carries its own DNS record name computed by
  `_dns_name_for_domain(domain.domain)`. Build e.g.
  `domains = [{"obj": cd, "dns_name": _dns_name_for_domain(cd.domain)} for cd in
  tenant.custom_domains.order_by("created_at")]`.
- `tenant_custom_domain_verify(request, pk, domain_pk)` and
  `tenant_custom_domain_delete(request, pk, domain_pk)`: resolve the target with
  `get_object_or_404(CustomDomain, pk=domain_pk, tenant=tenant)` instead of
  `.first()`. The tenant-scoped lookup also blocks acting on another tenant's
  domain by pk (returns 404).
- `tenant_custom_domain_add`: logic unchanged (it already creates a new row and
  rejects globally-duplicate domains). It now renders into the list.

### 2. `dashboard/urls.py`

Add the domain pk to the two per-domain actions:

- `sites/<int:pk>/custom-domain/<int:domain_pk>/verify/` → `tenant_custom_domain_verify`
- `sites/<int:pk>/custom-domain/<int:domain_pk>/delete/` → `tenant_custom_domain_delete`

`sites/<int:pk>/custom-domain/` (section) and `.../add/` are unchanged.

### 3. `templates/dashboard/partials/custom_domain.html`

Render a list inside the existing `#custom-domain-section` swap target:

- For each domain: an Active (verified) / Pending badge, the domain string, and
  its own actions — verified → "Visit ↗" + "Remove"; pending → "Check
  verification" + "Remove". Pending rows show that domain's specific A-record
  name (`dns_name`).
- The "Add a domain" form and the general DNS-setup instructions stay
  permanently visible below the list. The empty state (no domains) is just the
  instructions + add form, reusing today's copy.
- Section-level `error`/`info` banner stays as-is (verify/add messages already
  name the domain). Each per-domain form still targets `#custom-domain-section`
  via `data-fetch-form`, so `static/js/section-fetch.js` is unchanged (no JS
  work).

### 4. Tests (TDD)

Write failing tests first, then implement.

- Update existing single-domain dashboard tests to the new URL signature and
  list markup.
- Add: two domains render independently; verifying domain B leaves domain A's
  state untouched; removing domain A leaves domain B; verify/delete with a
  `domain_pk` belonging to another tenant returns 404; add still rejects a
  globally-duplicate domain.

## Data / migrations

None. No model fields change; `domain unique=True` already enforces global
uniqueness.

## Risk

Low. No routing, middleware, model, or redirect changes. The route-syncer and
public request path are untouched; only the agency-facing management UI changes.

## Rollout

Feature branch `feat/multi-custom-domains`. After review + green tests, merge to
`main` (auto-deploys to prod on Dokploy). First real use: add
`www.lemecadvisors.com` to the Lemec Advisors tenant and verify it.
