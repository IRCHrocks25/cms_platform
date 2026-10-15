# CMS-92 blog page layouts implementation plan

**Goal:** Add explicit no-picture, cards (2/3 columns), Featured page layouts while preserving unset output and CMS-76 orientations.
**Architecture:** Extend JSON setting index_layout choices, route only new choices to a page-only template. Reuse existing query/pager/filter/sort and scoped site tokens. Featured groups the currently filtered/sorted page, first1 large/next2 small/rest grid; no separate featured-post query or duplicate items. Missing covers use text only. Keep strip templates untouched; coordinate model/render hunks with w21/CMS93.
**Stack:** Django templates, Python3.12, SQLite, Chromium.

Approved PM ticket supplies layouts and acceptance. Existing native select remains keyboard/label accessible; preview is the existing authenticated unsaved GET flow. No migration, dependencies or live data changes.

1. Add failing-first backend tests for saved choices, public structure/order, covers, pagination/filter/sort/tenant scope, unsaved preview and auth. Capture old default byte hashes with fixed synthetic fixtures before changes.
2. Extend only BLOG_INDEX_LAYOUT_CHOICES, INDEX_PAGE_SIZE, index_template_path; add isolated page_layout template and update picker explanation. Leave old style/templates and shared strip items unchanged.
3. Browser acceptance at390/1280 for all layouts: no overflow, actual card/featured positioning, keyboard select + labels, unsaved iframe preview; site-style inheritance and no-cover behavior. Fix discovered issues in one batch.
4. Mutation revert new template dispatch, assert regression fails, restore. Full SQLite suite, asset gates under shared lock. Push PR/CI; ticket In Review; staging/IAM installed-account checks explicitly UNRUN, no deploy.
