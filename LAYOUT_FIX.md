# Layout fix — blog pages now render inside the client's site chrome

Date: 2026-05-29

## Symptom

`/blog/` and `/blog/<slug>/` on a client site rendered as standalone basic
pages — no client navbar, no footer, none of the site's global styling.
Visitors felt like they'd left the site.

## Phase 1 — Diagnosis (root cause)

**Where the site chrome comes from:** there is no reusable layout component.
A client site is a single full HTML document pasted by the agency and stored
in `Template.html_source`. The navbar, footer, fonts, and global CSS all live
inside that one document. The homepage is rendered by
`core/views.py::_render_tenant` → `core/renderer.py::render_site`, which
substitutes content into `[data-edit]` nodes and returns the whole document.
By convention every block is a `[data-section]`: the navbar is
`[data-section="nav"]`, the footer `[data-section="footer"]`, and the page
body is the other `[data-section]` blocks (verified on the real
`ai-consultant` template and `samples/restaurant.html`).

**Why blog pages skipped it:** the blog templates were built as **full
standalone documents**. `blog/_doc.html` had its own `<html>/<head>/<body>`,
its own Google-Fonts links, a global `<style>` (with bare `body`, `html`,
`:root`, `*` rules), and even its own `.blog-bar` navbar + `.blog-foot`
footer. `blog/<style>/{index,detail}.html` extended it. So a blog page was a
completely separate page that knew nothing about the client's template — root
cause **#3** (full-page blog templates) compounded by **#2** (the site layout
is baked into the pasted HTML, with no wrapper to inherit).

## Phase 2 — The fix (approach + why)

Single source of truth: the client's template **is** the layout, so wrap blog
content in it rather than forking a "blog layout".

1. **Blog templates became content fragments.** New `blog/_content.html` —
   no `<html>/<head>/<body>`, no blog navbar/footer. It carries the blog
   typography CSS **scoped under `.cms-blog`** (the global `body`/`html`/`:root`/`*`
   resets were removed/scoped so they can't override the site's chrome), wraps
   the content in `<div class="cms-blog cms-blog--<style>">`, and includes the
   editor preview-bridge script when previewing. The six
   `blog/<style>/{index,detail}.html` templates now `{% extends
   "blog/_content.html" %}` (one-line change each); their per-style CSS blocks
   are unchanged and inherit the scoped CSS vars. `blog/_doc.html` was deleted
   (no longer referenced — no lingering fork).

2. **`blog_render.wrap_in_site_chrome(tenant, fragment)`** renders the client
   site via the *same* `render_site` path the homepage uses, then:
   - removes the homepage's content sections (`[data-section]` except the nav
     and footer) — so the hero/about/etc. don't show on a blog page;
   - keeps the `<head>` (global CSS, brand tokens), the navbar, the footer,
     and any site scripts;
   - drops the blog content fragment where the sections were (before the
     footer);
   - injects the Fraunces/Roboto web fonts into `<head>` if the template
     doesn't already load them (so blog typography still looks right).
   Falls back to a minimal standalone shell only if the template has no
   usable `<body>`.

3. **Routing.** `render_detail` (used by both the public detail view *and* the
   dashboard live preview) now returns the wrapped HTML. `core/views.py`'s
   `_blog_index` renders via the new `render_index` helper, also wrapped. Both
   then layer per-page SEO on with `apply_head_settings` — the wrap renders the
   chrome with `site_settings=None` so GA/custom-head scripts aren't injected
   twice; per-page `<title>`/description/OG win.

**Layering guarantee:** the site's `<head>`, navbar, and footer sit *outside*
`.cms-blog`, so they keep the site's fonts/colors. The blog's typography
(scoped under `.cms-blog`) layers on top for the post body/index only — it
can't bleed into the chrome, and the chrome can't flatten the blog styling.

**Preview parity:** because the dashboard single-post preview goes through the
same `render_detail` → `wrap_in_site_chrome`, the live preview now shows the
post inside the client's real chrome too — it won't drift from the public
render.

## Files

- **New:** `templates/blog/_content.html`.
- **Deleted:** `templates/blog/_doc.html`.
- **Edited:** `templates/blog/{minimal,magazine,cards}/{index,detail}.html`
  (extends line); `core/services/blog_render.py` (`wrap_in_site_chrome`,
  `render_index`, `_find_chrome`, `_wrap_minimal`, `render_detail`,
  `_BLOG_FONTS_LINK`); `core/views.py` (`_blog_index`); `core/tests/test_blog.py`.

## Phase 3 — What was verified

Against the **real** `ai-consultant` tenant (read-only render): blog detail &
index contain the site navbar (`UNLAYER …`) and footer, the site's global
`<head>` styles, the post content, and the editable markers — and the
homepage hero section is **gone**. Output is a single document (not nested).

`core/tests/test_blog.py` (now **68 tests**, green). New `BlogChromeTests`:
- index & detail render the site navbar + footer + global CSS, include the
  post, and exclude the homepage section;
- each client gets **its own** navbar (multi-tenant isolation);
- per-post SEO title lands in the wrapped `<head>`;
- exactly one `<body>` (guards against re-nesting the fragment refactor).
Existing public/draft/strip/preview tests still pass (drafts hidden,
sanitization intact, strip unchanged, preview shows `data-blog-edit` markers).

### Manual checks to run
1. `/blog` on a client → site navbar + footer present, styled to match.
2. Click a post → `/blog/<slug>/` also wrapped; navbar links work (home/other
   pages navigate normally).
3. Homepage strip links land on properly-wrapped blog pages.
4. Dashboard blog preview shows the post inside the site chrome.
5. A second client with a different template shows *their* navbar/footer.

## Notes / limits
- Content removal targets `[data-section]` blocks. If a template puts page
  content *outside* any `data-section` (unusual; the parser/editor model
  expects sections), that stray content could remain on blog pages. Real
  annotated templates wrap everything in sections, so this is a non-issue in
  practice.
- The 4 pre-existing `test_middleware` failures are unrelated (ALLOWED_HOSTS;
  see `BLOCKERS.md`).

---

## Follow-up (2026-05-29): leaked comment + navbar overlap

Two issues surfaced on the wrapped blog pages from the work above. Full
diagnosis in `BUGFIX.md`.

### Issue 1 — leaked Django comment
`_content.html` opened with a **multi-line** `{# … #}`. Django only strips
*single-line* `{# #}` (its lexer regex has no `re.DOTALL`), so the multi-line
one rendered as literal text at the top of every blog page. Fixed by switching
the doc block to `{% comment %}…{% endcomment %}` (multi-line safe). The
fragment was always rendered through Django (`render_to_string`); the engine
was working — the comment just wasn't a valid comment.

### Issue 2 — fixed/absolute navbar overlap
Client templates may pin the navbar out of normal flow (ai-consultant uses
`.nav { position: absolute; top:0 }`), so `.cms-blog` started underneath it.
Fixed with a small **inlined** script in `_content.html` that, at runtime,
checks the chrome nav's computed `position`; if it's `fixed`/`absolute` and
anchored at the top, it pads `.cms-blog` by the nav's real height + 24px,
otherwise leaves the base padding (no dead space for in-flow/`sticky` navs).
Re-runs on resize. Inlined (not an external `{% static %}` file) so the
public render stays self-contained and doesn't add a `collectstatic`/manifest
dependency to public pages — consistent with the fragment already inlining its
CSS. Single render path, blog-scoped, no fork.

### Verified
- Real ai-consultant render: no `{#`/`#}`/"CONTENT fragment" text; offset
  script present; nav is the absolute-positioned first body element.
- `core/tests/test_blog.py` → **70 tests** green, incl. new
  `test_no_leaked_template_comment_syntax` (rendered HTML has no `{#`/`#}`) and
  `test_chrome_offset_script_present`. Full suite green except the 4 unrelated
  `test_middleware` ALLOWED_HOSTS errors (`BLOCKERS.md`).
- Manual: blog title and "← Blog" link clear the navbar on a fixed/absolute-nav
  site; an in-flow-nav site gets no dead space (create a minimal template whose
  nav is normal flow to confirm).
