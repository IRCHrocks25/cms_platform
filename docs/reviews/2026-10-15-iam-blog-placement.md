# IAM Marketable blog placement and styling

15 October 2026 (Asia/Singapore). **Keep ticket open.** Production inspected
read-only. User explicitly prohibited production changes after providing access.
No production content, settings, HTML, or publish controls were edited.

## Report and installed account

The same [Loom report](https://www.loom.com/share/3d60ce1b573c4ddb98feaf4fcd86c9a3)
as CMS-74 was opened in the Codex in-app browser and its Transcript tab read.
The opening segment reports that updating the blog leaves the expected area
unchanged and puts content at the bottom. The following segments describe
different fonts/styling. The transcript does not name the exact edited field;
do not infer a body-editor placement failure from this wording.

Authenticated inspection:

- Tenant **41**, Kenneth, subdomain `kenneth`, published domain
  [www.iammarketable.co](https://www.iammarketable.co/).
- Owned template **41**, Kenneth. It is already a **block shell**, with
  `data-region="main"`; the blog content is not in its shell HTML.
- `content.regions.main` contains `{id:"blog", type:"blog", fields:...}`.
  Its fields are manually entered teasers for Growth, AI, Strategy. The
  editor shows 15 fields, including `blog.post1.title` etc.
- [Blog manager](https://sites.katek.app/dashboard/sites/41/blog/): two
  published, featured posts, IDs **15** and **16**. Minimal Reading blog style,
  Cards strip, enabled, count 3, heading "From the blog".
- [Reported post editor](https://sites.katek.app/dashboard/sites/41/blog/16/):
  title "Why Thinking More Customers Is Costing You Money", Published,
  featured, author Kenneth, site-default layout. Opened without editing.

## Observed placement (before)

Codex in-app browser, desktop **1280 × 720**, DOM position and computed-style
inspection. Homepage has **zero `[data-blog-strip]` mounts** and one generated
`[data-cms-blog-strip]`. Document top positions at this viewport:

| Surface | Position | Content |
|---|---:|---|
| `#blog[data-section=blog]` | 8317px | Three manually entered teaser cards |
| `#book` final CTA | 10096px | Final CTA |
| Generated featured strip | 11073px | Published posts 15 and 16 |
| Footer | 11835px | Site footer |

This establishes the duplicate **homepage surface** in the installed tenant.
The generated strip uses the before-footer fallback; the authored blog block
has no connection to BlogPost records. It is not evidence that the article
body or blog index moves to the homepage bottom during Save. Exact edit/save/
publish replay in production was **not run**, per the user's restriction.

The [article](https://www.iammarketable.co/blog/why-thinking-more-customers-is-costing-you-money/)
renders its body in the article area, with no generated homepage strip.
The [/blog/ index](https://www.iammarketable.co/blog/) lists both published
posts, with no generated homepage strip.

| Element | Computed font | Size / line height |
|---|---|---|
| Homepage body | Inter, sans-serif | 16px |
| Original blog section heading | Bebas Neue, sans-serif | 64px |
| Original teaser title | Bebas Neue, sans-serif | 24px |
| Generated strip heading | Fraunces, Georgia, serif | 40px |
| Generated strip title | Fraunces, Georgia, serif | 21px |
| Article title | Fraunces, Georgia, Times New Roman, serif | 46px / 51.52px |
| Article prose | Roboto, system sans-serif | 18.5px / 33.3px |
| Article h2 | Fraunces, Georgia, Times New Roman, serif | 30px / 37.5px |
| Index title | Fraunces, Georgia, Times New Roman, serif | 42px |

A browser viewport override was attempted for production mobile inspection,
but DOM `innerWidth` remained 1280. **Do not count that as mobile evidence.**

## Prepared local change (not installed)

`samples/iam-blog-mounted.html` is the explicit replacement for template 41's
**owned BlockType with key `blog`**, not the template shell. It preserves the
`#blog` navigation anchor and the block instance's position in `regions.main`,
replaces manual teaser cards with `[data-blog-strip]`, and retains the site's
`sec`/`sec-light`/`sec-in` spacing. No tenant content migration was executed.
Existing obsolete teaser field values can remain stored; the new schema no
longer renders them. Back up the block HTML before any eventual installation.

Exact eventual target: `BlockType.objects.get(template_id=41, key="blog")`.
Replace only its `html_source` with the reviewed sample and call `save()` to
derive its schema. Do not add a mount to the shell after `data-region="main"`:
that would still place the strip after the CTA. Do not change the region order
or BlogPost bodies to correct this homepage binding. This is a deployment
instruction for a later authorized rollout, **not an executed change**.

Reusable changes:

- Four active strip layouts consume CMS-74's semantic group font aliases
  `--cms-font-body`/`--cms-font-heading`, then the base `--font-*` contract,
  retaining Roboto/Fraunces fallbacks. Article/index use the same shared
  content styles; CMS-74 supplies article Title mappings.
- Accent consumes site `--primary` with the previous accent fallback.
  Article reading size consumes `--text-body` with 18.5px fallback; optional
  `--blog-space` controls outer spacing without copying oversized hero sizes.
- Strip preview gets the rendered site's styles/font aliases and body
  attributes, including the explicit mount's ancestor wrappers and block CSS,
  instead of a fixed-font-only shell. Site scripts are excluded from this
  standalone strip preview.
- Article/index chrome now assembles block instances before typography
  discovery. Browser testing exposed that reading only a converted template's
  shell missed heading aliases whose elements lived inside the blocks.
  Index page titles also consume the CMS-74 Title alias.
- Repeated strip composition removes only the prior generated strip before
  replacing it; authored sections are never selected heuristically as mounts.
  Unmarked existing sites retain the legacy placement fallback.

## Local checks

Windows PowerShell, `C:\Users\josea\cms_platform`, `.venv/Scripts/python.exe`,
Python **3.12.3**, Django **5.1.2**, Playwright **1.63.0**, isolated SQLite test
databases. No production database is used for these tests.
Branch `feat/cms-74-typography-tokens`, HEAD `a0bd42e0` plus the uncommitted
CMS-74 and blog changes. No commit, push, merge or deployment was performed.

```powershell
.venv/Scripts/python.exe manage.py test core.tests.test_blog --noinput
```

Baseline: **84 tests, 35.046s, OK**. After placement and parity regressions:
**87 tests, 33.033s, OK** (`.iam-blog-targeted-test.log`). This result precedes
the additional browser test and the final group-alias fallback adjustment.
The regression reproduces the observed block-shell separation, then installs
the proposed block locally, saves a draft, publishes corrected title/body,
and verifies the single intended strip and article preview/public parity.

```powershell
.venv/Scripts/python.exe manage.py test core.tests.test_blog.InstalledBlogBrowserTests --noinput
.venv/Scripts/python.exe manage.py test --parallel 4 --noinput
```

Final targeted result: **88 tests, 60.187s, OK**, including the Chromium
browser test. Editor/public viewports **1280 × 900** and **390 × 900**.
Steps: edit title/body, save Draft, confirm no public strip entry, reopen,
publish, refresh, compare article preview/public computed font families,
check index title, and verify exactly one strip inside `#blog` at both widths.
Observed computed families: Inter body, Bebas Neue heading/title; no horizontal
overflow. These test declared/computed families; the reduced fixture does not
prove external webfont downloads or visual parity with the full installed site.

Fixture screenshots (local, not production):

- [Desktop](iam-blog-local-1280.png)
- [Mobile](iam-blog-local-390.png)

An additional strip-preview/public padding, font and accent comparison was
added after that run loaded its tests. Supplemental final browser command
` .venv/Scripts/python.exe manage.py test core.tests.test_blog.InstalledBlogBrowserTests --noinput`
completed: **1 test, 15.898s, OK**. It verifies equal strip-preview/public
computed font, padding and accent at both widths.
Full suite result: pending (`.iam-blog-full-test.log`).
The initial sandboxed browser run stalled in Python's asyncio socketpair setup
before launching Chromium (diagnosed using `faulthandler.dump_traceback_later`).
It was interrupted and retried with local socket access. The first completed
browser run failed on the missing heading alias, prompting the block-rendering
fix above. Interrupted full-suite attempts are not passing gates.

## Remaining acceptance

- Complete and record browser and full-suite outcomes below.
- Install/recheck the prepared owned-block replacement in an approved
  non-production copy with the new code.
- Production after-change positions/computed styles: **NOT RUN**; production
  is unchanged. Required live edit/save/publish acceptance remains pending
  under the user's explicit no-production-changes instruction.
- Do not close the ticket based on local fixtures or the read-only inspection.
