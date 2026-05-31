# Blog Feature — Summary

A complete blogging feature for the multi-tenant CMS: clients write
structured posts (no raw HTML), pick a blog style, feature posts on their
homepage, and get auto-generated blog index + post pages on their live site.
Phases logged in `AUDIT.md` (investigation) and `PLAN.md` (design).

---

## What was built

### Data
- **`core.models.BlogPost`** — scoped to `Tenant`: title, slug (unique per
  site, auto from title, editable), cover image, excerpt, rich `body`,
  author, status (draft/published), publish date, per-post SEO
  (title/description/OG image), per-post template override, `featured` +
  `featured_order` for the homepage strip.
- **`Tenant.blog_settings`** (JSON) — site-wide blog style, blog title,
  strip on/off, strip count (1–6), strip heading.
- Migration `0010_tenant_blog_settings_blogpost`. Registered in admin.

### Backend
- **Public** (`core/views.py`): `blog_index` / `blog_detail` on the tenant
  host (`/blog/`, `/blog/<slug>/`) and `/site/<sub>/blog/...` fallback.
  Drafts are excluded from all public querysets; a draft detail page 404s
  for the public but renders (with a "draft preview" banner) for an editor.
  Pagination via `Paginator`.
- **Homepage strip** (`core/services/blog_render.py`): featured published
  posts are rendered in the chosen style and injected into the homepage —
  into a `[data-blog-strip]` marker if the template has one, else before the
  footer. No featured posts → nothing injected.
- **Dashboard** (`dashboard/views.py`): full CRUD, featured toggle, JSON
  reorder, and blog-settings save — each a shared helper with two thin
  wrappers (agency `sites/<pk>/blog/…` + tenant-host `blog/…`), matching the
  existing two-surface pattern and decorators.
- **Security:** `core/services/sanitizer.py` is a BeautifulSoup allowlist
  sanitizer (no new dependency). Post bodies are sanitized on **save** and
  again on **render** — `<script>`, event handlers, `javascript:` URLs,
  `style`/`class`, and unknown tags are stripped; external links get
  `rel="noopener noreferrer" target="_blank"`.
- **Reuse:** images (cover + inline) go through the existing
  `tenant_upload` / `MediaAsset` flow; per-post SEO reuses the Site-Settings
  head-injection (`renderer.apply_head_settings`), so blog pages inherit the
  site's GA snippet + custom head script with per-post title/description/OG
  layered on top.

### Frontend
- **Public templates** (`templates/blog/<style>/{index,detail,strip}.html`)
  for 3 styles, sharing `_doc.html` + `_pager.html`. Self-contained CSS; an
  accent color is pulled from the site's brand `--primary` token.
- **Dashboard**: `blog_list.html` (post table with status/date/featured
  star, status filter, strip-settings panel, drag-to-reorder featured list)
  and `blog_form.html` (rich-text editor, cover upload, SEO, template
  picker, publish controls). `blog_editor.js` (contenteditable + toolbar +
  uploads + auto-slug), `blog_reorder.js`, `static/css/blog.css`.
- Nav: "Blog" link on the client (tenant) nav; "Manage blog" on the agency
  site-detail page and a "Blog" button on the editor bar.

---

## Blog styles chosen (and why)

I picked 3 that cover the common range; each applies to index + detail +
strip for consistency:

1. **Minimal Reading (`minimal`, default)** — narrow single column, large
   readable type. The safe default and best for actual reading.
2. **Magazine (`magazine`)** — editorial: bold masthead, a featured lead
   post, dark full-bleed post hero, image cards. For content-forward brands.
3. **Card Grid (`cards`)** — responsive cover-thumbnail card grid. The most
   common "blog on a marketing site" look.

---

## How a client publishes a post + features it on the homepage

1. On their site, open **Blog** in the top nav (agency staff: a site's
   **Manage blog** button / the editor bar's **Blog** button).
2. Click **+ New post**. Enter a title (slug auto-fills, editable), write
   the body in the rich-text editor (headings, bold/italic, lists, quotes,
   links, inline images), optionally add a cover image, excerpt, and SEO.
3. Set **Status → Published** and **Save post**. It immediately appears at
   `/blog/` and `/blog/<slug>/`. (Draft = saved but never public.)
4. To feature it on the homepage: tap the **☆ star** next to the post in the
   list (or tick "Feature on homepage strip" in the editor). In the
   **Homepage strip** panel, pick the blog **style**, set how many posts the
   strip shows, and **drag featured posts to reorder** them. The strip then
   renders on the homepage in the chosen order.

---

## What to test first

1. **Publish flow:** create a post (Published) on a tenant → visit
   `/blog/` and `/blog/<slug>/` on that subdomain; confirm it renders.
2. **Draft privacy:** create a Draft → confirm `/blog/<slug>/` 404s when
   logged out, and shows a "draft preview" banner when logged in as the
   site's member/owner.
3. **Homepage strip:** feature 2–3 posts, set a count, reorder them →
   reload the homepage and confirm the strip appears in order; unfeature all
   → strip disappears.
4. **Styles:** switch the blog style (minimal/magazine/cards) in strip
   settings → confirm index, detail, and strip all change.
5. **XSS:** insert content with a `<script>` or an `onerror` image into the
   body, save, view the public post → confirm it's stripped.
6. **Both surfaces:** repeat create/edit from the agency dashboard
   (`/dashboard/sites/<pk>/blog/`) and confirm cross-tenant isolation (you
   can't open another site's post).
7. **Images:** upload a cover image and an inline body image → confirm both
   persist and render.

Automated coverage: `core/tests/test_blog.py` (37 tests — sanitizer, model
slug/SEO, public index/detail/draft gating, strip ordering/injection,
dashboard CRUD/auth/reorder/settings). Run with a local DB:
`DATABASE_URL="sqlite:///test_blog.sqlite3" python manage.py test core.tests.test_blog`
(the committed `.env` points at a remote Postgres; override it for fast,
isolated test runs — and run `collectstatic` once so `{% static %}` resolves
under the manifest storage).

---

## Notes / non-goals
- No new pip dependencies (sanitizer + editor built on the existing stack).
- Locked-structure promise intact: clients author posts via structured
  fields only; they cannot edit the site's section structure or raw HTML.
- Scheduled publishing UI is minimal (a publish-date field exists and the
  public query respects `publish_date <= now`); there's no separate
  scheduler/queue.

---

# Live Blog Preview — Summary

While editing a post, the client sees it rendered **exactly** as it will
appear publicly — their chosen blog style, fonts, layout, chrome — updating
as they type, with no save and no publish. Phases logged in `AUDIT.md` and
`PLAN.md` (each has a "Live Blog Preview" section).

## Approach chosen: Option B — iframe through the real render route

The blog "templates" are **full HTML documents** (own `<head>`, Google
Fonts, a global `<style>` resetting `html, body`) — Django templates, not
transplantable React components. In-app component rendering (Option A) was
never viable: there's nothing to import, and their global CSS would collide
with the dashboard. The brief's hard rule — *preview must match the public
render* — is only guaranteed by rendering through the **same code path** the
public detail page uses (`core/services/blog_render.render_detail`). So the
preview is an **iframe** served by a real preview route, with a single
source of truth for templates, CSS, and sanitization.

To stay smooth, the iframe is **server-rendered once** and then **patched in
place** via a `postMessage` bridge as the user types (the same
server-renders / client-patches pattern the main site editor uses). A full
reload happens only when the *structure* changes — i.e. the style picker
switches templates.

## How the pipeline works

1. **Editor** (`blog_form.html`) — split view: form left, preview iframe
   right. The iframe `src` is `dashboard:blog_preview[_self]` (or
   `…_preview_new…` for an unsaved post).
2. **Preview view** (`_blog_preview`) renders `render_detail(…,
   preview_bridge=True)`, which injects `blog_preview.js` (the in-iframe
   bridge) and emits `[data-blog-edit]` markers on title, body, cover,
   author, and date.
3. **As the user edits** (`blog_editor.js`):
   - **Body** is debounced ~300ms, POSTed to `blog_sanitize[_self]`
     (`{html: sanitize_html(body)}`), and the **sanitized** HTML is pushed to
     the iframe — so the preview body is byte-identical to the public render.
   - **Title / author / date / cover** are pushed instantly (~150ms) and
     patched via `textContent` / `setAttribute` (never `innerHTML`), so they
     need no round-trip. Date is formatted client-side to match Django's
     `"F j, Y"`.
   - **Style picker** change reloads the iframe with `?style=…`; the live
     (unsaved) content re-applies once the new doc's bridge says `ready`.
4. **Layout controls** (editor bar): a 3-way **Write / Split / Preview**
   toggle hides the preview (Write), splits (Split), or expands it
   full-width (Preview); the choice persists in `localStorage`. On screens
   ≤900px the "Split" option disappears and the toggle becomes **Write /
   Preview tabs**. A **desktop / tablet / mobile** device toggle resizes the
   iframe. A status label shows "Updating…" during a body round-trip.
5. **Homepage strip preview (bonus)** — on `blog_list.html`, an iframe under
   the strip settings renders the chosen style's `strip.html` via
   `blog_strip_preview[_self]`, honoring **unsaved** style/heading/count/on-off
   (query params) and the current featured order. It refreshes on settings
   `input` (debounced) and after a drag-reorder. Matches the public strip
   (published-featured posts only).

## The bug this also fixed

The preview was scaffolded earlier but **never wired into `urls.py`** —
`_blog_nav_urls` reversed `blog_preview*` routes that didn't exist, so
**every blog dashboard page 500'd** with `NoReverseMatch` (9 test errors at
baseline). Adding the four routes (plus the two new ones) restored the whole
blog dashboard and made the preview functional.

## Fidelity limits (and how to fix later)

- **Inline images mid-upload:** an inline body image is inserted only after
  its upload resolves (it returns a URL first), so the preview never shows a
  broken/half-uploaded image — but there's no inline spinner. Fine for now;
  add an optimistic placeholder if desired.
- **Body round-trip latency:** the sanitized body lags typing by the ~300ms
  debounce + a network hop. If the request fails, the last good sanitized
  body is kept (preview never goes blank). Acceptable; a JS sanitizer would
  remove the hop but would fork the allowlist (rejected — single source of
  truth).
- **Strip preview height** is a fixed scroll box (420px); a very tall
  "magazine" strip scrolls inside it rather than matching exact homepage
  flow. The *fragment* is identical to production; only the surrounding
  viewport differs.
- The preview shows the post detail page; it doesn't render the post inside
  the full homepage. That's intentional — the detail page is what
  `/blog/<slug>/` serves.

## What to test first

1. **Parity:** edit a post, then publish it and open `/blog/<slug>/` — the
   published page must look identical to the preview (fonts, spacing, style).
2. **Live fields:** type a title, set an author and a publish date, swap the
   cover image — each updates in the preview; clear author → the separator
   dot disappears.
3. **Body sanitize:** paste `<script>` / an `<img onerror=…>` into the body —
   the preview body must show it stripped (same as public), not execute it.
4. **Style switch:** change the per-post template — the preview reloads into
   the new style and your unsaved edits survive.
5. **Toggle + responsive:** Write/Split/Preview toggle; shrink the window
   below 900px and confirm it becomes Write/Preview tabs; device toggle
   resizes the frame.
6. **Strip preview:** feature 2–3 posts, change style/heading/count and
   reorder — the strip preview on the list page tracks the unsaved settings
   and the new order; disable the strip → "turned off"; unfeature all →
   "No published featured posts".
7. **Both surfaces:** repeat from the agency dashboard
   (`/dashboard/sites/<pk>/blog/<post>/`) — preview, sanitize, and strip work
   identically and stay tenant-scoped.

Automated coverage: `core/tests/test_blog.py` grew from 37 → **55 tests**
(new `BlogPreviewTests`: preview routes + bridge for saved/unsaved/both
surfaces, `?style=` override, author/date markers, body-sanitize parity +
auth + method gating, strip-preview overrides/empty/disabled/draft-exclusion/
auth). Run isolated:
`DATABASE_URL="sqlite:///test_blog.sqlite3" python manage.py test core.tests.test_blog`.

> Note: 4 unrelated failures in `core/tests/test_middleware.py`
> (`TenantResolverProductionDomainTests` / forwarded-host) are **pre-existing
> and out of scope** — they raise `DisallowedHost` because those classes
> override `TENANT_BASE_DOMAIN` without overriding `ALLOWED_HOSTS` for the
> current env. No blog/preview code touches middleware or settings. See
> `BLOCKERS.md`.

---

# Bugfix — strip shows "No published featured posts" despite featured posts

Full diagnosis in `BUGFIX.md`.

## Root cause (confirmed against the live database)

I queried the production DB read-only. The featured post is genuinely
`status=published`, `featured=True`, **with** a `publish_date` of
`2026-05-29 02:51 UTC` — but the current time was `2026-05-28 20:34 UTC`, so
that date is **~6 hours in the future**.

`blog_render.published_posts()` gated visibility on
`publish_date__lte=timezone.now()`, treating `publish_date` as a hard
**go-live time**. A published post dated even slightly in the future is
excluded from the index, post pages, and the homepage strip →
`featured_posts()` returns `[]` → the strip shows its empty state.

Why the date is in the future: `TIME_ZONE=UTC`, and the post editor's
`<input type="datetime-local">` is timezone-naive — it submits the client's
*local* wall-clock string, which `_blog_save` makes aware as **UTC**. A
client in ~UTC+6 publishing "now" stores it ~6 h ahead. They see a green
**Published** badge and star the post, but the query hides it. The product's
visibility contract is **status-based** (drafts hide, published shows — the
task's own acceptance criteria), so a `publish_date <= now` gate on an
optional, timezone-naive display field is the defect.

> Note: an earlier pass of this fix mis-diagnosed the cause as a NULL
> `publish_date` (a real but *different*, latent issue). Inspecting the
> actual data corrected it — the row has a date; it's just in the future.

## The fix (root cause, not symptom)

1. **`core/services/blog_render.py::published_posts()`** — drop the
   `publish_date__lte=timezone.now()` clause. Visibility is governed by
   `status=published`. (`publish_date IS NOT NULL` is still required for
   stable ordering/display.) The reported post now appears immediately.
2. **`core/models.py::BlogPost.is_live`** — aligned the same way, so a
   published post with a future date isn't flagged as a "draft preview" on
   its detail page.

Retained as defense-in-depth (still correct):
- `BlogPost.save()` stamps a `publish_date` when a post is published without
  one, so `publish_date IS NOT NULL` always holds for published posts.
- Migration `0011` backfills any legacy published rows that had NULL dates.

**Trade-off (intentional, documented):** this removes naive future-date
*scheduling* — which was already broken for any client outside UTC and
conflicts with the status-based visibility the product expects. Real
scheduled publishing should be an explicit, tz-aware feature later (a
`scheduled` status / a captured browser timezone), not a side effect of an
optional date field. Empty-state copy unchanged; auth/site scoping untouched.

## Verified

- **Against live data (read-only):** with the fix, `featured_posts(tenant)`
  now returns the real post and `render_strip_doc(...)` renders it instead of
  the empty message.
- **`core/tests/test_blog.py` (61 tests, all green).** Key regression:
  `test_published_future_dated_post_is_visible` — a published+featured post
  dated in the future appears in `featured_posts()`, while a draft (even with
  a past date) stays hidden. Plus `BlogPreviewTests.test_strip_e2e_…` covers,
  end-to-end through the real views: (1) publish + star → appears;
  (2) unstar → disappears; (3) unpublish → disappears (featured draft must
  not show); (4) zero featured → empty state; (5) manual `featured_order`
  respected.

The 4 unrelated `test_middleware` errors above are unchanged.
