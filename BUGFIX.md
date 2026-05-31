# Bugfix — Homepage blog strip shows "No published featured posts" despite published+featured posts

Diagnosed: 2026-05-29 (corrected after inspecting production data)

## Symptom

The homepage strip preview renders the empty state — *"No published
featured posts yet. Publish a post and tap the ☆ star to feature it here."*
— even though the site has a post that is **Published** and **Featured**.
That copy lives only in `core/services/blog_render.py::render_strip_doc`.

## What the production database actually contains

I queried the live DB (read-only). The tenant (`ai-consultant`) has exactly
one post:

```
title="Test Title"  status=published  featured=True  featured_order=1
publish_date = 2026-05-29 02:51:00+00:00
```

So the row **does** have `status=published` AND `featured=True` AND a
non-NULL `publish_date`. The featured flag *is* saved. The query *is*
scoped correctly. The empty-state condition is correct. The earlier
hypothesis (published post with NULL `publish_date`) does **not** apply to
this data.

The decisive fact: at the time of the report, `timezone.now()` was
**2026-05-28 20:34 UTC**, but the post's `publish_date` is
**2026-05-29 02:51 UTC — ~6.3 hours in the future**.

## Where it breaks (root cause)

`core/services/blog_render.py::published_posts()` gates visibility on:

```python
.filter(status=PUBLISHED, publish_date__isnull=False, publish_date__lte=timezone.now())
```

The `publish_date__lte=timezone.now()` clause treats `publish_date` as a
hard **go-live time**: a published post whose date is even slightly in the
future is excluded from the index, post pages, **and** the homepage strip.
`featured_posts()` builds on `published_posts()`, so the future-dated post is
filtered out → `featured_posts()` returns `[]` → the strip shows its empty
state. Confirmed live: `featured_posts(tenant, 6)` → `[]`, while
`blog_posts.filter(featured=True)` → the post is right there.

### Why the date is in the future (the trigger)

`TIME_ZONE = "UTC"`, `USE_TZ = True`. The post body editor publishes the
date via `<input type="datetime-local">`, which is **timezone-naive** — it
submits the user's *local wall-clock* string with no offset. `_blog_save`
does `make_aware(parsed, get_current_timezone())`, and the active zone is
UTC, so a client in ~UTC+6 who publishes "now" (their local ~02:51) has it
stored as **02:51 UTC** — about 6 hours ahead of real UTC "now". The client
sees a green **Published** badge and stars the post, but the public/strip
query hides it until that UTC instant passes.

This is a **semantic mismatch**: the UI presents `publish_date` as an
optional display date and treats **status** as the publish control (drafts
hide, published shows — exactly the acceptance criteria for this task), but
the query treats `publish_date` as a scheduling gate. With timezone-naive
input, that gate routinely and silently hides freshly-published posts.

## Ruled out (this time, against real data)

- **Featured flag not saving** — DB shows `featured=True`.
- **NULL publish_date** — DB shows a real `publish_date` (was a *different*,
  latent issue; hardened anyway — see below).
- **Site scope / wrong tenant** — query is correctly scoped; the post is in
  the tenant's `blog_posts`.
- **Empty-state condition / array-treated-as-empty** — `featured_posts()`
  genuinely returns `[]`.
- **Caching** — the `[]` is the live query result, not a stale cache.

## Fix (Phase 2)

Visibility is governed by **status**, not by a naive future-date gate.

1. `core/services/blog_render.py::published_posts()` — drop the
   `publish_date__lte=timezone.now()` clause. A post is public once
   `status=published` (still requires a non-NULL `publish_date` for stable
   ordering/display; the model guarantees one). This is the root-cause fix:
   the reported post (and any "published now but stored slightly in the
   future" post) now appears.
2. `core/models.py::BlogPost.is_live` — align it the same way (drop the
   `<= now` clause), so the blog **detail** page doesn't show a misleading
   "draft preview" banner for a published post whose date is future.

Retained from the earlier pass (defense-in-depth, still correct):
- `BlogPost.save()` stamps `publish_date` when a post is published without
  one, so `publish_date__isnull=False` always holds for published posts.
- Migration `0011` backfills any legacy published rows that had NULL dates.

### Trade-off (documented, intentional)

This removes **naive future-date scheduling** (a "Published" post with a
future date used to stay hidden until that instant). That behavior was
already broken for clients outside UTC and conflicts with the status-based
visibility the product/acceptance criteria expect. Real scheduled publishing
should be a deliberate feature: a separate `scheduled` status (or a
tz-aware "go live at" captured with the browser's timezone), not an implicit
side effect of an optional, timezone-naive date field. Not in scope here.

## Regression guard

`core/tests/test_blog.py`:
- `test_published_future_dated_post_is_visible` — a published post with a
  publish_date in the future appears in `featured_posts()` and the public
  index (locks in the fix).
- Existing strip/public/draft tests still assert drafts are hidden and
  unpublished sites 404 (status gate intact).
- `BlogPreviewTests.test_strip_e2e_…` covers toggle/untoggle/unpublish/empty/
  ordering end-to-end through the real views.

---

# Bugfix — blog detail: leaked template comment + navbar overlap

Diagnosed: 2026-05-29

## Issue 1 — Django template comment leaking as literal text

`templates/blog/_content.html` opened with a **multi-line** `{# … #}`
comment. Django's template lexer (`tag_re = ({%.*?%}|{{.*?}}|{#.*?#})`,
**no** `re.DOTALL`) only matches `{# … #}` when it stays on one line — a
multi-line `{# … #}` is **not** recognized as a comment and renders as
literal text. Proven:

```
single-line: '{# x #}AFTER'  -> 'AFTER'
multi-line : '{# x\ny #}AFTER' -> '{# x\ny #}AFTER'   # leaks
```

The fragment IS rendered through Django (`render_to_string` → wrap), so the
engine *should* strip a real comment — the bug is purely that a multi-line
`{# #}` isn't a real comment. Only `_content.html` had a multi-line one; all
other `{# #}` in the codebase are single-line (fine).

**Fix:** use `{% comment %}…{% endcomment %}` (multi-line safe) for the doc
block. Regression test asserts rendered blog HTML contains neither `{#` nor
`#}`.

## Issue 2 — Client navbar overlaps blog content

The ai-consultant template pins its navbar out of normal flow:

```
.nav { position: absolute; top: 0; left: 0; right: 0; z-index: 50; padding: 28px 0; }
```

`position: absolute` (like `fixed`) removes the nav from flow, so `.cms-blog`
starts at the top of the page and the nav floats over the blog title / "←
Blog" link. In-flow navs (`static`/`relative`/`sticky`) don't have this
problem and must NOT get dead space.

**Fix:** a tiny blog-only script (`static/js/blog_chrome_offset.js`, loaded
from `_content.html`) measures the chrome nav at runtime: if its computed
`position` is `fixed` or `absolute` AND it sits at the top, it sets
`.cms-blog` `padding-top` to the nav's real height + a gap; otherwise it
leaves the base padding untouched (no dead space). Re-runs on resize. Scoped
to blog pages (only `_content.html` loads it), single render path, no fork.

---

# Bugfix — chrome interactivity on blog pages (anchor links + scripts)

Diagnosed: 2026-05-29

## Issue 1 — nav/footer anchor links don't navigate from blog pages
Rendered blog-page chrome keeps the homepage's anchor hrefs verbatim:
`['#', '#about', '#process', '#programs', '#faq', '#cta']`. Those target
homepage section ids that don't exist on `/blog/<slug>/`, so clicking does
nothing. The script has **no** `preventDefault`/`scrollIntoView` — anchor
scrolling is native browser behavior — so the fix is purely the href: rewrite
anchor-only hrefs to point at the homepage (`#about` → `/#about`, or
`/site/<sub>/#about` on the agency fallback). The bare `#` (logo / JS handlers)
and the language switcher (uses JS, not hrefs) must be left alone. The footer
carries the same anchor links, which is why it "feels dead" too.

## Issue 2 — scripts: already included; hardening
For the ai-consultant tenant the client's single inline `<script>` (23.5 KB)
**is** present on blog pages (it's a body-level tag, kept), is byte-intact
(not corrupted by re-serialization), and has **no references to removed
homepage elements** (`.hero` etc.), so it runs without throwing. Head
`<link>` stylesheets/fonts are identical between homepage and blog page.
`querySelectorAll('.reveal' | '.faq-item')` simply returns empty on blog pages
and no-ops. So the chrome JS does initialize (lang switch, mobile-menu toggle,
reveal observer). The menu-toggle (`#menuToggle`, the "dropdown trigger") and
its `#mobileMenu` (a body-level sibling) are both present.

The residual risk the spec calls out: a tenant who puts a `<script>` (or
`<link>`/`<style>`) **inside a content section** would lose it when
`wrap_in_site_chrome` decomposes that section. ai-consultant has 0 such tags,
but to guarantee "all site scripts/styles included on blog pages" generally,
the fix **rescues** scripts/styles/links out of to-be-removed sections (scripts
→ end of body, styles/links → head) before decomposing.

## Fix
- `wrap_in_site_chrome` rewrites chrome anchor-only hrefs to the homepage URL
  (derived from `blog_base`); skips `#`, `http(s):`, `/…`, `mailto:`, `tel:`,
  and anything inside `.cms-blog` (post content keeps its own links). Only
  runs in the wrapper (blog pages) — the homepage path never calls it, so its
  in-page anchors are untouched.
- `wrap_in_site_chrome` rescues `<script>/<style>/<link>` from removed sections
  so site JS/CSS is always present on blog pages.

## Regression tests
Blog chrome anchors become `/#…`; homepage anchors stay `#…`; bare `#`/http/
absolute/mailto unchanged; blog scripts ⊇ homepage scripts; second tenant
anchors rewrite too.
