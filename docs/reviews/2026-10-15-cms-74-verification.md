# CMS-74 verification — 15 October 2026

Status: **keep open**. This is a review of HEAD `a0bd42e0` plus the existing
uncommitted typography/group/editor changes. Application source was not changed
by this review. No push or rollout was performed.

## Customer report

The transcript supplied by the user is the report. At approximately 00:16 and
00:33, the customer says the blog font and styling differ from the site. At
01:55–02:22, the customer asks about decreasing **image size**, not text size.
The existing CMS-74 design spec's statement that this segment reports an
inability to make text smaller is inaccurate. Image resizing, navigation
renaming, image metadata, and blog layouts/categories/sorting need their own
scope; CMS-74's body/heading size contract remains a ticket requirement.

The implementation explanation in the design spec is still a hypothesis about
the installed account until that account is used for reproduction.

## Findings

1. **Framework variables are exposed as extra font controls.**
   `core/typography.py::_font_slots` adds every used root font-stack variable
   whose name matches `SLOT_NAME_RE`, without filtering framework names.
   Reproduction below returns slots `font-body` and `tw-font-sans`, including a
   user-editable `font:tw-font-sans` key. This contradicts the explicit scope
   requirement to avoid arbitrary framework variables. Role inference also
   needs the same policy rather than relying solely on font-value syntax.

   ```html
   <style>
   :root { --tw-font-sans: Arial; --font-body: Inter; }
   body { font-family: var(--font-body); }
   .framework { font-family: var(--tw-font-sans); }
   </style>
   <body><p class="framework">Copy</p></body>
   ```

2. **Font-group overrides discard conditional CSS scope.**
   `_RULE_RE` discovers nested rules, `_text_groups` stores selectors without
   their enclosing media/supports/layer context, and `typography_css` emits
   those selectors globally. For the example below, changing Body font to
   Poppins emits `body,.caption{font-family:var(--font-body)}` outside the media
   query. A mobile-only font rule now applies on desktop too, and the original
   heading-font use is changed to body-font on mobile. Preserve the original
   scope or avoid selector remapping for rules whose scope cannot be retained.

   ```html
   <style>
   :root { --font-body: Inter; --font-heading: Georgia; }
   body { font-family: var(--font-body); }
   @media (max-width:600px) {
     .caption { font-family: var(--font-heading); }
   }
   </style>
   <body><p class="caption">Copy</p></body>
   ```

3. **The automated acceptance gate is failing.** Three non-browser group
   assertions expect separate selector rules while the implementation combines
   selectors; these failures alone do not prove a rendering bug. The browser
   test `test_design_fonts_reach_every_token_use_in_preview_and_public_site`
   expects `.tag` to stay Inter although the new Body mapping changes it to
   Poppins. Another browser test times out waiting for a visible Body font
   control after the helper force-clicks the Design tab. The fixture can enter
   the block editor, whose Fonts toolbar opens the drawer; investigate the
   helper/editor-mode mismatch before treating that timeout as a product bug.
   Reconcile the intended behavior and tests, then rerun acceptance.

## Environment and automated evidence

Workspace: `C:\Users\josea\cms_platform`; Windows PowerShell; Python **3.12.3**
from `.venv\Scripts\python.exe`; Django **5.1.2**; Playwright **1.63.0**;
SQLite isolated Django test databases. Dates are Asia/Singapore.

```powershell
.venv/Scripts/python.exe manage.py collectstatic --noinput
```

PASS: 1 copied, 138 unmodified, 390 post-processed.

```powershell
.venv/Scripts/python.exe manage.py test core.tests.test_typography_tokens core.tests.test_typography_groups core.tests.test_renderer_styles core.tests.test_save_styles core.tests.test_annotate_preservation --noinput
```

FAIL: **114 tests**, 50.055 seconds, **3 failures** / no reported errors.
Failing tests: `test_body_font_reaches_body_group`,
`test_group_can_switch_to_another_template_font`,
`test_heading_font_reaches_title_and_heading_groups` in
`core.tests.test_typography_groups.RenderTests`. The other 111 tests passed,
including parser discovery, annotation strip/restore, unsafe-value rejection,
legacy rendering, colors, save/public/preview integration, and blog contract
checks covered by these modules.

The first combined 119-test run and first full-suite run stalled in the
sandbox and were interrupted. They are not counted as completed results.

```powershell
.venv/Scripts/python.exe manage.py test core.tests.test_typography_browser --noinput --verbosity 2
```

Executed outside the sandbox after the stalled browser run. FAIL: **5 tests**,
94.635 seconds, **1 failure, 1 error**; 3 passed:

- Block-editor Fonts toolbar: PASS; changing Body/Heading reaches preview and
  is saved through the dashboard path.
- Contract sizes: PASS; set Body to **20px** and Heading to **64px**, verify
  iframe computed sizes, save/reload, and public computed sizes at widths
  **1280** and **390**, height **900**.
- Reset Heading font: PASS; Oswald resets to template Bebas Neue in preview
  and the saved `_global` clears.
- Font consistency: FAIL at `.tag` (computed Poppins, expected Inter); stops
  before that test's remaining reload/public checks.
- Three text groups: ERROR waiting 30 seconds for the Body select to be
  visible; group acceptance is incomplete.

Browser: Chromium driven by Playwright; editor viewport **1440×900**.
No browser screenshots were saved by these existing tests.

Full-suite rerun:

```powershell
.venv/Scripts/python.exe manage.py test --noinput --verbosity 2 *> .cms74-full-test.log
```

This default-settings run was interrupted because authentication fixture
creation was slow. Its partial log is retained as
`docs/reviews/2026-10-15-cms-74-default-test.log`; it is not a completed gate.

The completed full run used:

```powershell
.venv/Scripts/python.exe manage.py test --settings=_cms74_review_settings --noinput --verbosity 2 *> .cms74-full-test.log
```

Temporary `_cms74_review_settings.py` imported the normal settings and changed
only `PASSWORD_HASHERS` to `["django.contrib.auth.hashers.MD5PasswordHasher"]`
for isolated authentication fixtures. A guard refused commands other than
`manage.py test`. The temporary file was removed after the run. No application
or deployment settings were changed; no tests were excluded.

FAIL: **1,512 tests**, **356.048 seconds**, **7 failures, 2 errors, 2 skipped**.
The completed log is retained as
`docs/reviews/2026-10-15-cms-74-full-test.log`. Hosted CI was not run.

All seven failures and both errors were in the typography acceptance tests or
the product-copy policy check on the new typography source:

- Three group CSS assertions, as in the targeted run.
- `.tag` expecting Inter but receiving Poppins, as in the isolated browser run.
- Size-control autosave never persisted the expected `_global` within 10
  seconds; the reset test also did not persist the cleared `_global`. Both
  passed in the isolated browser run, so acceptance is order/configuration
  dependent and needs investigation. These full-run failures must not be
  silently dismissed as evidence of working persistence.
- `test_product_owned_source_has_no_unapproved_em_dashes` rejects the new
  docstring line at `core/typography.py:273`.
- Two browser errors: timed out locating the Fonts drawer control and the
  Design-tab button. In the isolated run, the Fonts toolbar passed and the
  three-group test found a hidden Body select instead. The full browser gate
  is not passing; no root cause for these discrepancies is concluded here.

The complete default-settings full-suite gate still remains owed. The completed
test-only-hasher run provides broad regression evidence but is not a claim
that the original default-settings command passed.

## Live evidence

Read-only inspection of the [reported IAM article](https://www.iammarketable.co/blog/why-thinking-more-customers-is-costing-you-money/)
in the Codex in-app browser at **1280×720**, on 15 October 2026. Method:
`getComputedStyle` on body/headings/paragraph/footer and the document root.

| Element | Computed font family | Computed size |
|---|---|---|
| body | Inter, sans-serif | 16px |
| h1 `.min-title` | Fraunces, Georgia, Times New Roman, serif | 46px |
| h2 | Fraunces, Georgia, Times New Roman, serif | 30px |
| h3 | Fraunces, Georgia, Times New Roman, serif | 23px |
| paragraph | Roboto, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif | 18.5px |
| footer `.tf` | Inter, sans-serif | 16px |

Root values: `--body: 'Inter',sans-serif`, `--disp: 'Bebas Neue',sans-serif`;
`--font-body`, `--font-heading`, `--text-body`, `--text-heading` are empty.
The current live article still differs from the site's typography. This does
not establish which code revision is deployed.

Visiting `https://www.iammarketable.co/dashboard/` redirects to
`https://www.iammarketable.co/login/?next=/dashboard/`: there is no authenticated
installed-client session in the available browser. No client content was
changed. **Installed-account reproduction and live before/after rollout
acceptance remain blocked on client login/access and a rollout.** Synthetic
tests are not a substitute. Post-rollout mobile live acceptance is also owed.

## Remaining acceptance work

- Resolve framework exposure and conditional-rule remapping.
- Align the group tests and browser helper with intended editor behavior;
  complete all required local gates.
- Correct the transcript interpretation in the original design spec.
- Reproduce in the real installed account, then verify computed fonts/sizes
  before/after rollout on a tokenized site and IAM desktop/mobile.
- Coordinate the blog integration ticket: font contract consumption exists;
  blog sizes and layout/sorting/image requests are separate work.
- Keep CMS-74 open until required checks pass.
