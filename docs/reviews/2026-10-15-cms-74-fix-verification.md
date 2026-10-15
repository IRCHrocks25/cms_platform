# CMS-74 fix and staging verification

15 October 2026, Asia/Singapore. Follow-up to
`2026-10-15-cms-74-verification.md` (the earlier failing review baseline).

## Implemented behavior

- Independent **Title font** (`_global.titleFamily`) changes page/hero titles
  together while Heading and Body retain their own fonts. Templates may expose
  optional `--font-title`; templates without it can still use the picker.
- Title/Heading/Body can be assigned to another declared font slot. Explicit
  group assignment takes precedence over the group's family picker; resetting
  the mapping returns to its picker/template styling.
- Literal-font and inherited-font titles are recognized, including legacy
  templates; older Body/Heading overrides retain their existing rendering.
- CSS discovery uses pinned `tinycss2==1.4.0`, retains conditional contexts and
  declaration importance, keeps a selector's semantic group consistent across
  breakpoints, and excludes framework font slots/keyframes.
- The Fonts drawer is available for block templates even without font tokens.
  The Design tab also exposes global typography on plain editable templates.
- Blog article titles in minimal/cards/magazine layouts consume `--font-title`
  with the existing heading-font fallback. Article reading sizes are unchanged.
- Save validation and font loading include Title; server and preview apply the
  same mapping and resets. Browser helpers use the visible Fonts drawer/Design
  tab, and poll with Playwright event processing so autosave can complete.

The transcript's image-resizing, navigation, and blog layout/sorting requests
are separate features. This change addresses the CMS-74 typography behavior
and the user's explicit request for bulk Title font customization and mapping.

## Local evidence

Windows PowerShell, `.venv/Scripts/python.exe` **3.12.3**, Django **5.1.2**,
Playwright **1.63.0** with Chromium, isolated SQLite test databases. The full
run uses the normal project settings and password hashers.

```powershell
.venv/Scripts/python.exe manage.py collectstatic --noinput
.venv/Scripts/python.exe manage.py test core.tests.test_typography_tokens core.tests.test_typography_groups core.tests.test_renderer_styles core.tests.test_save_styles core.tests.test_product_copy_policy core.tests.test_annotate_preservation --noinput
```

Observed targeted result: **120 tests, 19.304 seconds, OK**. One additional
legacy Title regression was added subsequently and is included in the full
run below.

```powershell
.venv/Scripts/python.exe manage.py test core.tests.test_typography_browser --noinput --verbosity 2
```

Observed result: **6 tests, 63.093 seconds, OK**. Browser steps cover:

- Body Poppins, Heading Oswald: all intended classes update in the preview;
  save/reload and public desktop/mobile agree.
- Title Lora with Heading Oswald: titles change independently, persist after
  reload, and agree at public widths **1280** and **390** (height **900**).
- Map Title to Mono: titles become JetBrains Mono while headings stay Oswald;
  save/reload preserves it. Clear mapping and Title override: titles return to
  the existing Heading font.
- Body **20px**, Heading **64px**: computed preview sizes, saved controls, and
  desktop/mobile public output agree.
- Block-editor Fonts toolbar and reset to template defaults work.

Editor viewport: **1440×900**. These are local browser checks, not evidence of
the new code being deployed to staging.

Full normal-settings run:

```powershell
.venv/Scripts/python.exe manage.py test --parallel 4 --noinput --verbosity 2
```

Result: **pending**. `tblib==3.2.2` was installed only in the local virtual
environment for Django's parallel traceback handling, not added to production
requirements. The slow unfinished serial run was interrupted; it is not a
completed gate. No tests are excluded from the parallel run.

## Authenticated staging reproduction

User authorized staging testing and supplied an admin login. Credentials are
not included in evidence files. No existing client content was edited.

Created a dedicated site and annotated template through the New client UI:

- [Editor](https://staging.sites.katek.app/dashboard/sites/17/edit/)
- [Published fixture](https://cms74-typography-20261015-staging.sites.katek.app/)
- Name: **CMS-74 Typography Acceptance**
- Fixture source: `samples/cms-74-typography.html`

The site was published solely for staging public-endpoint checks and retained
for post-deploy verification. Typography overrides were reset to Default and
verified after save/reload. Existing staging site 16 was inspected read-only.

The deployed editor has Body font, Heading font, and Base text size controls,
but no Title picker, font-group mapping, or Heading size control, even on a
template declaring the semantic tokens. Consequently this tests the older
deployed behavior, not the new local implementation.

Steps: set Body to **Poppins**, Heading to **Oswald**, Base text size to **20px**;
wait for All changes saved; reload and verify all three selected values remain;
inspect computed styles in the preview and published desktop/mobile page.

| Element | Before | After saved overrides (desktop) |
|---|---|---|
| body | Inter, 16px | Poppins, 20px |
| `.hero-title` | Bebas Neue, 40px | Bebas Neue, 40px |
| `.h-title` | Bebas Neue, 40px | Bebas Neue, 40px |
| `.section-heading` | Bebas Neue, 32px | Bebas Neue, 32px |
| `.statement` | Inter, 32px | Inter, 32px |
| `.btn` | Inter, 16px | Inter, 20px |

Preview viewport at the baseline: **810×551**. Published checks: **1280×900**
and **390×844**, Codex in-app browser. The same font inconsistency is present
on both. `.mobile-title` inherits Poppins/20px on desktop, but its mobile-only
rule uses JetBrains Mono/64px at 390px, providing a responsive acceptance case.

This reproduces the deployed class-font issue: changing low-specificity global
element rules does not change the variables/class rules that the template uses.
The independent Title control is also absent. It does not substitute for
reproduction in the real IAM installed account, which is not present among the
available staging sites.

Screenshots:

- `2026-10-15-cms74-staging-before.jpg`: initial Design panel, missing Title.
- `2026-10-15-cms74-staging-overrides.jpg`: selections saved, class fonts unchanged.
- `2026-10-15-cms74-staging-public-desktop.jpg`: published 1280px result.
- `2026-10-15-cms74-staging-public-mobile.jpg`: published 390px result.
- `2026-10-15-cms74-staging-restored.jpg`: defaults restored and reloaded.

## Deployment and remaining acceptance

`deploy/STAGING.md` says staging tracks `main` with automatic deployment, while
production deployment is manual. The working feature branch is
`feat/cms-74-typography-tokens`. No feature deployment or merge has been
performed during this verification. A branch deployment through Dokploy, or a
reviewed merge to main, is needed before the new controls can be tested here.

Keep CMS-74 open until the required local gates pass, the new branch is deployed
and rechecked on the fixture, and IAM installed-account/post-rollout acceptance
is recorded. Staging credentials authorize the CMS dashboard; they do not
provide Dokploy access or access to the production IAM account.
