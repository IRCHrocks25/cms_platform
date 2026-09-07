# CMS SEO Routing Cleanup Design

## Goal

Preserve existing extensionless, no-trailing-slash public URLs when sites move to the CMS, while keeping sitemap and robots signals aligned with the final verified domain.

## Routing

Tenant inner pages use one canonical path: `/<slug>`. That path returns the published page directly. `/<slug>/` and `/<slug>.html` permanently redirect to `/<slug>`, preserving query strings. `/index.html` permanently redirects to `/`. Unknown paths and unknown `.html` aliases remain real 404 responses.

CMS-generated navigation, editor link choices, dashboard live links, and API response URLs use the same canonical no-trailing-slash paths. Imported site HTML can keep legacy slash links because those remain valid permanent aliases, but newly generated links do not add avoidable redirect hops.

The agency-host fallback keeps its existing `/site/<subdomain>/<slug>/` behavior because it is an operator preview route rather than the public canonical URL. Blog URLs keep their current trailing-slash behavior because changing them is outside the Nolan migration scope and could affect existing tenants.

## Sitemap and robots

Sitemap page URLs use the same no-trailing-slash paths served by the canonical tenant routes. Only published, indexable pages appear. A page is excluded when its rendered HTML contains a robots meta directive with `noindex`; this covers branded error pages without relying on a special slug. Draft pages stay excluded as they are today.

The canonical base host continues to come from the first verified custom domain. Until verification, the temporary tenant hostname remains the only routable canonical base available to the CMS. Once a custom domain is verified, both `sitemap.xml` locations and the `robots.txt` sitemap reference switch to that domain.

## Compatibility and safety

Permanent redirects use HTTP 301 and retain query strings. Existing application routes such as `/dashboard/`, `/login/`, `/api/`, crawler files, and blog routes stay ahead of the tenant page catch-all. The new no-slash route is restricted to one slug segment so it cannot capture nested application paths.

## Verification

Tests cover direct no-slash page rendering, trailing-slash redirects, legacy `.html` redirects, `/index.html`, query-string retention, unknown aliases, unpublished pages, sitemap path shape, exclusion of rendered `noindex` pages, and verified-domain sitemap and robots output. The existing SEO, page-rendering, legal-page, middleware, and URL helper tests provide regression coverage.
