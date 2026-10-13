# CMS-41: branded page for unknown hosts

## Problem

`TenantResolverMiddleware` sets `request.tenant = None` for four different
cases: an app host (`127.0.0.1`), the bare base domain, a reserved subdomain,
and a host that did not resolve at all. Downstream code cannot tell the last
case apart, so an unclaimed subdomain such as
`definitely-not-a-tenant.sites.katek.app` serves platform surfaces: the
marketing page at `/`, the staff login at `/login/`, the platform legal pages at
`/privacy/` and `/terms/`, and (with a staff session, because the session cookie
is scoped to `.sites.katek.app`) the agency dashboard.

## Decision

The middleware classifies every request as `request.host_kind`:

| `host_kind` | When |
|---|---|
| `tenant` | Subdomain of a configured base domain that matches a tenant, or a verified custom domain. |
| `unknown` | Subdomain of a configured base domain with no matching tenant, or a host with a `CustomDomain` row that is not verified yet. |
| `agency` | Everything else: app hosts, bare base domains, reserved subdomains, a label without this environment's suffix (staging's agency host `staging.sites.katek.app`), and hosts we know nothing about. |

`request.tenant` keeps its current meaning.

Hosts we know nothing about stay `agency` on purpose. Traefik only forwards the
apex, the tenant wildcard, and verified custom domains, so in production such a
host is the internal health probe (`localhost:8000/healthz`) or the Django test
client (`testserver`). Classing them `unknown` would fail the container health
check.

On an `unknown` host the middleware answers **every path** itself, before any
view runs, with the branded page and status **404**. Static files are served by
WhiteNoise earlier in the stack and are unaffected.

## Page

Standalone template `templates/unknown_host.html` in Katek branding (white Katek
logo, navy `#030d2a`, blue `#0040FF`, Segoe UI / system font, matching
`templates/marketing/home.html`).

Copy:

* Heading: "There's no website here yet"
* Body: "`<host>` is a Katek Sites address, but no website has been published
  here."
* Pending custom domain variant: "`<host>` is pointed at Katek Sites and we can
  see it. The domain is still being verified, so the site will appear here once
  that finishes."
* Call to action: "Get a website built" linking to the Katek Sites home page on
  the agency host (`MONITOR_BASE_HOST`, else `TENANT_BASE_DOMAIN`).
* `<meta name="robots" content="noindex">`.

No login link, no legal links, no tenant data.

## Readers of `request.tenant`

The short-circuit means no view sees an unknown host, so most readers need no
change. The PR lists each one. Readers that would have misbehaved on an unknown
host and are now covered by the short-circuit: `root_redirect`, the login and
password-reset views, `/privacy/` and `/terms/`, `dashboard_root` and the agency
decorators, `McpView`, and `handler404`.

## Open for the owner

Copy, call-to-action target, and the 404 status are defaults chosen from the
ticket. Change them here before merge if marketing wants something else.

## Testing

* Middleware: `host_kind` for tenant, unknown subdomain, unverified custom
  domain, bare base, app host, reserved subdomain, staging agency host with a
  suffix configured, and an unrelated host (`testserver`).
* Unknown host: `/`, `/login/`, `/privacy/`, `/terms/`, `/dashboard/` (anonymous
  and staff) all return 404 with the branded page and no login form.
* Unverified custom domain shows the pending variant.
* Agency host, reserved subdomain, and a resolved tenant are unchanged.
