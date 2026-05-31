# Blockers — Live Blog Preview

No blockers stopped this work; it shipped end to end. Two items are recorded
here for transparency.

## 1. Pre-existing, out-of-scope test failures (not introduced by this work)

Running the **full** suite shows 4 errors, all in
`core/tests/test_middleware.py`:

- `TenantResolverProductionDomainTests.test_production_subdomain_resolves`
- `TenantResolverProductionDomainTests.test_bare_production_domain_resolves_to_none`
- `TenantResolverProductionDomainTests.test_other_domain_resolves_to_none`
- `TenantResolverCustomDomainForwardedHostTests.test_custom_domain_uses_x_forwarded_host_when_host_is_proxy`

All raise `DisallowedHost: Invalid HTTP_HOST header: 'acme.yourdomain.com'`
(and similar). Cause: those test classes use
`@override_settings(TENANT_BASE_DOMAIN="yourdomain.com")` (and the
forwarded-host one a `katek.app` host) **without** putting those hosts in
`ALLOWED_HOSTS`, so `request.get_host()` rejects them under the current
environment's `ALLOWED_HOSTS`.

This is unrelated to the blog/preview feature — no code in this task touches
`core/middleware.py`, `cms_platform/settings.py`, or
`core/tests/test_middleware.py`. The blog suite (`core/tests/test_blog.py`,
55 tests incl. the new preview tests) is fully green. Fix (separate task):
add `ALLOWED_HOSTS=[...]` to those `@override_settings` decorators.

## 2. Test/static caveats (worked around, not blocking)

- Tests must run against a **local DB** — the committed `.env` points at a
  remote Postgres. Override per run:
  `DATABASE_URL="sqlite:///test_blog.sqlite3" python manage.py test core.tests.test_blog`.
- `BlogPreviewTests` mirrors the existing `BlogDashboardTests`
  `@override_settings`, which swaps `ManifestStaticFilesStorage` for plain
  `StaticFilesStorage`, so the tests don't require `collectstatic`. For a
  real dev/prod run, the new `static/js/blog_strip_preview.js` (and the
  existing blog JS) must be collected: `python manage.py collectstatic`.
