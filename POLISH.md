# Blog visual polish — investigation, plan, summary

Date: 2026-05-29

## Phase 1 — Findings

### 1. How the client chrome constrains width
The ai-consultant template centers everything with one container pattern:

```
.container, .nav-inner { max-width: 1200px; margin: 0 auto; padding: 0 48px; }
```

The navbar lives in `.nav-inner` (1200px, 48px gutters). Inner content
sections use narrower measures (`.about-inner` 860, `.process-header` 720,
`.final-cta-inner` 800…). `body` is normal block flow (not flex/grid), with a
global reset `* { box-sizing: border-box; margin: 0; padding: 0; }`.

### 2. THE root cause — the blog stylesheet was being dropped entirely
`wrap_in_site_chrome` injects the rendered fragment like this:

```python
frag = BeautifulSoup(inner_html, "lxml")
nodes = list(frag.body.children)   # <-- only the BODY
```

`_content.html` starts with `<style>…blog CSS…</style>` then the `.cms-blog`
div. **lxml hoists a leading `<style>` into `<head>`**, so `frag.head`
contains the stylesheet and `frag.body` contains only the div + scripts.
Iterating `frag.body.children` therefore **dropped the entire blog `<style>`
block**. Verified: the rendered detail page contains none of `.cms-blog{`,
`.blog-narrow{`, `max-width:720px`, `margin:0 auto` — i.e. `.cms-blog` had
*zero* CSS. That's why it has no container, no max-width, no gutters, and no
typography: the content rendered completely unstyled and butted the viewport
edge. (The earlier chrome tests checked the nav/footer/post text survived,
but never asserted the blog's own CSS survived — so this slipped through.)

### 3. Template visual issues (compounded by #2, but real)
- `.cms-blog` wrapper had only vertical padding — no `max-width`/gutters — so
  even with CSS present it wouldn't align to the site's 1200/48 container.
- Reading column `.blog-narrow` (720) and `.blog-wrap` (1080) used 24px
  gutters vs the site's 48px → misaligned with the navbar.
- Cover images, byline, and the "← Blog" backlink had minimal treatment;
  spacing wasn't on a consistent scale.

### 4. Are the three templates distinct?
On the **index** yes (minimal list / magazine lead+grid / cards grid). On the
**detail** page minimal and cards were close (centered serif title + cover +
prose); only magazine (dark hero band) stood out. Needs real differentiation.

## Phase 2 — Plan

1. **Fix the dropped stylesheet (critical):** in `wrap_in_site_chrome`, move
   `frag.head` children (the blog `<style>`) into the chrome's real `<head>`,
   and the `frag.body` children into the content slot. This both restores the
   CSS and puts it where it belongs.
2. **Container alignment:** detect the site's dominant centered container
   (largest rule with `margin:0 auto` + `max-width` + horizontal padding) and
   expose it to the blog as CSS vars `--site-cw` / `--site-gut` (fallback
   1100px / 24px). `.cms-blog` becomes the aligned outer container; the reading
   column sits at a ~720px editorial measure centered within it, so the blog's
   outer bounds line up with the navbar.
3. **Visual design (scoped under `.cms-blog`, conservative/editorial):**
   consistent 4px-based vertical rhythm, clear hierarchy (dominant serif
   title, subdued uppercase byline, ~68ch body measure, 1.75 line-height),
   proper `figure/figcaption`, links/lists/blockquotes/code/pre, and a real
   cover-image treatment (aspect-ratio, rounded, constrained width). The
   "← Blog" backlink styled as an intentional pill/affordance.
4. **Distinct templates:**
   - **minimal** — left-aligned, hairline rules, restrained, max whitespace,
     no cover frame; reading-first.
   - **magazine** — bold editorial: colored/ink hero band, oversized title,
     wide full-bleed cover, kicker; widest measure.
   - **cards** — centered, "boxed" feel: cover in a rounded card on a tinted
     surface, centered title, pill meta; the designed/marketing look.

## Constraints honored
No global `body/html/*` rules — everything stays under `.cms-blog`. Single
render path (`wrap_in_site_chrome`), no fork. No data changes.

## Phase 3 — Implemented

1. **Critical: restored the dropped stylesheet.** `wrap_in_site_chrome` now
   moves the fragment's `<head>` children (the hoisted `<style>`) into the
   chrome `<head>` before inserting the body nodes. The blog CSS is back.
   (`import re` was also missing and is now added.)
2. **Container alignment.** New `_detect_site_container(shell_html)` finds the
   client's widest centered container (`margin:0 auto` + `max-width` +
   horizontal padding) — for ai-consultant that's `1200px / 48px`. The values
   are written as `--site-cw` / `--site-gut` onto the `.cms-blog` wrapper;
   the scoped CSS uses them (fallback `1100px / 24px`). `.blog-wrap` now spans
   the site container (aligns with the navbar); `.blog-narrow` is a ~720px
   reading measure centered within it, with matching gutters.
3. **Editorial polish (scoped under `.cms-blog`).** 8px-ish vertical rhythm,
   serif headings with a clear hierarchy, 1.8 body line-height, real
   `figure/figcaption`, styled lists/blockquote/`pre`/inline-`code`/`hr`,
   `.blog-cover` (aspect-ratio + rounded + surface bg), `.blog-meta` as a
   subdued uppercase byline, and `.blog-back` as an intentional pill.
4. **Three genuinely distinct detail layouts:**
   - **minimal** — left-aligned narrow column, hairline rule when no cover,
     restrained.
   - **magazine** — full-bleed ink hero band (kicker + oversized title),
     21:9 wide cover, wider measure, drop-cap on the first paragraph.
   - **cards** — centered title/meta, big rounded cover with a soft shadow,
     centered reading column.

## Phase 4 — Verified

- Real ai-consultant render: `--site-cw: 1200px; --site-gut: 48px` on
  `.cms-blog`; `.blog-prose`/`.blog-wrap`/`.blog-narrow` CSS present (no longer
  dropped); cover uses `.blog-cover`; backlink uses `.blog-back`. All three
  styles render with their distinctive markup (`min-title` / `mag-hero` /
  `card-cover`).
- `core/tests/test_blog.py` → **72 tests** green, incl. new:
  - `test_blog_stylesheet_survives_wrapping` — the exact regression: scoped
    CSS (`.cms-blog`, `.blog-prose`, `blog-wrap`) is in the rendered output.
  - `test_container_detected_and_applied` — detected `--site-cw/--site-gut`
    (1000px/40px for the test template) are applied to the wrapper.
- Full suite green except the 4 unrelated `test_middleware` ALLOWED_HOSTS
  errors (`BLOCKERS.md`).
- Chrome untouched: all blog CSS stays under `.cms-blog`; navbar/footer keep
  the site's styling (existing chrome tests still pass). Single render path,
  no fork; no data changed.

### Manual checks
1. Reload `/blog/test-title/` — content is contained, gutters match the navbar
   (1200/48), title dominant, byline subdued, body ~720px measure.
2. Switch the blog page style (minimal / magazine / cards) in the dashboard —
   each detail page looks meaningfully different.
3. Cover image sits at a sensible aspect ratio, not full-bleed-at-random.

---

## Follow-up (2026-05-29): navbar text faded on blog pages

### Diagnosis — it was NOT a CSS-selector bleed
Verified the blog stylesheet emits **no unscoped selectors** (every rule is
under `.cms-blog`), and the navbar is a **sibling** of `.cms-blog` in the DOM
(no inheritance path). So nothing "bled" via selectors.

The real cause: the client's navbar is **transparent with light text**
(`.nav-logo {color:var(--paper-soft)}`, `.nav-links a {color:rgba(245,239,227,.8)}`)
designed to sit over the **dark hero** (`.hero {background:var(--night)}`,
`--night:#1a1610`). `wrap_in_site_chrome` strips the hero on blog pages, and
the polish pass gave `.cms-blog` a solid light background — so the light nav
text now sits over a light backdrop and is nearly invisible. The EN/DE toggle
and "Book a session" button have their own backgrounds, so they stayed
visible. (Homepage unaffected — it still has the dark hero behind the nav.)

### Fix — restore the navbar's backdrop (no client-CSS edit, no nav override)
`wrap_in_site_chrome` now captures the hero's background **before** stripping
it (`_detect_masthead_bg` resolves `background`/`var()` from the client CSS)
and sets `--cms-masthead` on `.cms-blog`. A scoped `.cms-blog::before` band
(height = the navbar's height, set by the existing offset script via
`--cms-navh`; color = `--cms-masthead`) recreates the hero-colored backdrop
*behind the navbar only* — so the unchanged navbar renders exactly as on the
homepage. In-flow navbars / undetectable heroes get `--cms-navh:0` /
transparent → no band, no change. Magazine's dark hero is matched to the
masthead color and bridged so there's no seam.

This is environmental (restoring the context the navbar was built for), not a
"make the navbar visible" override, and touches no client CSS.

### Verified
- Real ai-consultant render: `--cms-masthead: #1a1610` on `.cms-blog`,
  `.cms-blog::before` band present, `--cms-navh` set by the offset script →
  navbar text sits on the dark band, readable like the homepage.
- `core/tests/test_blog.py` → **74 tests** green, incl. new:
  - `test_blog_css_is_fully_scoped` — asserts the blog emits **no** selector
    outside `.cms-blog`/`:root` (the exact regression guard).
  - `test_navbar_masthead_backdrop_applied` — the hero bg (`#112233` in the
    test template) is reproduced as `--cms-masthead` + the band.
- Homepage untouched (no `.cms-blog`, so no band/offset). Container + typography
  + image polish from the prior pass preserved. Full suite green except the 4
  unrelated `test_middleware` ALLOWED_HOSTS errors (`BLOCKERS.md`).
