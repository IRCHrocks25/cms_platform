# CMS SEO Routing Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Serve tenant pages at extensionless no-trailing-slash URLs, preserve legacy redirects, and emit a sitemap containing only canonical indexable URLs.

**Architecture:** Add explicit canonical and legacy-alias routes before Django's trailing-slash page route, with small redirect views that resolve only valid tenant pages. Reuse the existing rendered-page path to identify `noindex` pages while building the sitemap, and retain the verified-custom-domain host selection already shared by sitemap and robots.

**Tech Stack:** Django URL routing and views, Django test client, Python HTML parsing/regular expressions, XML sitemap responses.

---

### Task 1: Canonical tenant page routes and legacy redirects

**Files:**
- Modify: `cms_platform/urls.py`
- Modify: `core/views.py`
- Create: `core/tests/test_seo_page_routes.py`

**Step 1: Write failing route tests**

Cover `GET /about` returning 200, `/about/` and `/about.html` returning 301 to `/about`, `/index.html` returning 301 to `/`, query-string retention, and unknown or unpublished aliases returning 404.

**Step 2: Run the focused tests and confirm failure**

Run: `.venv/bin/python manage.py test core.tests.test_seo_page_routes -v 2`

Expected: canonical no-slash routes are unresolved and current slash redirects point in the wrong direction.

**Step 3: Implement the route views**

Add a canonical no-slash tenant page route before the slash route. Make the existing slash route a permanent redirect after validating tenant and page visibility. Add a single-segment `.html` alias route and `/index.html` home alias. Preserve `request.GET.urlencode()` on redirects and reuse `_render_page` for the canonical response.

**Step 4: Run the focused route tests**

Run: `.venv/bin/python manage.py test core.tests.test_seo_page_routes -v 2`

Expected: PASS.

**Step 5: Commit**

Commit message: `fix: preserve canonical tenant page URLs`

### Task 2: Canonical sitemap paths and noindex exclusion

**Files:**
- Modify: `core/seo_views.py`
- Modify: `core/tests/test_seo_sitemap.py`

**Step 1: Write failing sitemap tests**

Change the expected page location to `/about` and add a published page whose rendered head includes a case-insensitive, order-independent `robots` meta directive containing `noindex`; assert that URL is absent.

**Step 2: Run the focused sitemap tests and confirm failure**

Run: `.venv/bin/python manage.py test core.tests.test_seo_sitemap -v 2`

Expected: page locations still end in `/`, and the published noindex page is listed.

**Step 3: Implement indexability filtering**

Build tenant page URLs without a trailing slash. Render each published page through the existing block renderer and exclude it when its robots meta content contains the `noindex` token. Keep draft filtering, ordering, last-modified dates, blog behavior, XML escaping, and verified-domain selection unchanged.

**Step 4: Run the focused sitemap and robots tests**

Run: `.venv/bin/python manage.py test core.tests.test_seo_sitemap core.tests.test_seo_robots -v 2`

Expected: PASS.

**Step 5: Commit**

Commit message: `fix: align sitemap with indexable canonical pages`

### Task 3: Regression verification

**Files:**
- Test only

**Step 1: Run related regressions**

Run: `.venv/bin/python manage.py test core.tests.test_seo_page_routes core.tests.test_seo_sitemap core.tests.test_seo_robots core.tests.test_bug_review_fixes core.tests.test_host_scoped_legal_pages core.tests.test_middleware core.tests.test_page_live_url -v 2`

Expected: PASS.

**Step 2: Run the full core test suite**

Run: `.venv/bin/python manage.py test core.tests -v 1`

Expected: PASS.

**Step 3: Inspect the final diff and commit any test-only adjustment**

Confirm the changes remain limited to routing, SEO sitemap behavior, tests, and these plan documents.
