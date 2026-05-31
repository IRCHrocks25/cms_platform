# Chrome interactivity on blog pages — anchor links + scripts

Date: 2026-05-29 · diagnosis in `BUGFIX.md`

## Issue 1 — nav/footer anchor links didn't navigate

**Cause.** `wrap_in_site_chrome` injects the homepage navbar/footer verbatim,
so their in-page anchor hrefs (`#about`, `#process`, …) stayed as-is. On
`/blog/<slug>/` those section ids don't exist, so clicking did nothing. (The
client script has no `preventDefault`/`scrollIntoView` — anchor scrolling is
native — so it was purely the href.) The footer carries the same links, which
is why it "felt dead".

**Fix.** `_rewrite_chrome_anchors` (run inside `wrap_in_site_chrome`, i.e.
blog pages only) repoints anchor-only hrefs at the homepage:
`#about` → `/#about`, or `/site/<sub>/#about` on the agency fallback (the
homepage URL is derived from `blog_base`). It **skips** bare `#` (logo / JS
handlers), `http(s)://`, absolute `/…`, `mailto:`/`tel:`, and any link inside
`.cms-blog` (the post's own content). The homepage never calls the wrapper,
so its anchors are untouched and in-page scrolling still works. No edit to the
client's `html_source`.

## Issue 2 — chrome scripts/styles

**Finding.** For the ai-consultant tenant the client's scripts and stylesheet
`<link>`s were already fully present and intact on blog pages and ran without
error (no `.hero` references to throw; `.reveal`/`.faq-item` queries just
no-op). So the chrome JS *does* initialize. The footer "feeling static" was
the dead anchor links (Issue 1).

**Hardening.** The one real gap: a tenant who places a `<script>` / `<style>`
/ `<link>` **inside a content section** would lose it when the wrapper
decomposes that section. `wrap_in_site_chrome` now **rescues** those assets
before decomposing — scripts → end of `<body>`, styles/links → `<head>` — so
all of the site's JS/CSS is present on blog pages regardless of where the
agency placed it, and the chrome behaves identically to the homepage.

## Verified

Against real ai-consultant + the test tenants:
- Blog nav/footer anchors → `/#about` (tenant) / `/site/acme/#about` (agency);
  bare `#`, `/contact`, `mailto:`, `https://…` untouched; **homepage** anchors
  stay `#about`.
- Client `<script>` present + intact on blog pages; a script placed inside a
  removed section is rescued and present.
- `core/tests/test_blog.py` → **79 tests** green, incl. new
  `test_chrome_anchor_links_rewritten_on_blog`,
  `test_chrome_non_anchor_and_bare_hash_untouched`,
  `test_homepage_anchors_not_rewritten`, `test_agency_fallback_anchor_prefix`,
  `test_chrome_scripts_present_on_blog`. Full suite green except the 4
  unrelated `test_middleware` ALLOWED_HOSTS errors (`BLOCKERS.md`).

Prior work intact: `wrap_in_site_chrome` layout, container/typography polish,
`.cms-blog` scoping, masthead backdrop, EN/DE switcher.

### Manual checks
1. `/blog/test-title/` → click About / The Process / Programs / FAQ → each
   goes to the homepage and scrolls to the section.
2. EN/DE switcher + "Book a session" still work; menu toggle opens the mobile
   menu; footer links navigate.
3. Homepage in-page anchors still scroll (not rewritten).
