# Kieran CMS Causality Test Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run a disposable control-versus-staging experiment that identifies the first CMS pipeline stage, if any, that damages mixed-style headings or displaces repeated field content.

**Architecture:** Serve one immutable synthetic fixture from a disposable Cloudflare Pages project, then feed that exact URL through the isolated staging CMS. Capture the control, post-import, no-op-save, and targeted-edit states as normalized DOM, computed-style records, and desktop/mobile screenshots; corroborate the result with the checkout's focused parser/renderer regression tests. Keep evidence in a temporary local directory, publish a concise repository research report, then remove every external throwaway resource.

**Tech Stack:** Static HTML, Cloudflare Pages/Wrangler, Django 5.1 CMS staging, BeautifulSoup renderer/parser, Chromium DevTools Protocol, shell/JQ/ImageMagick

---

### Task 1: Establish the isolated test boundary

**Files:**
- Read: `deploy/STAGING.md`
- Read: `docker-compose.staging.yml`
- Create temporarily: `/tmp/kieran-cms-causality/preflight.txt`

**Step 1: Verify public control deployment access**

Run `npx wrangler whoami` from a temporary fixture directory.

Expected: an authenticated Cloudflare account without printing credentials. If authentication is unavailable, use an existing approved Cloudflare connector; do not expose tokens.

**Step 2: Verify staging availability**

Run `curl -I https://staging.sites.katek.app/login/`.

Expected: HTTP 200 or an authentication redirect from the isolated staging application.

**Step 3: Identify staging runtime access and release**

Use the configured Dokploy/SSH access to locate only the staging CMS web container and record its image/container creation time and Git/release identifier when present.

Expected: one staging container using `cms-platform-staging:latest`; no production container mutation.

**Step 4: Record the local checkout**

Run `git rev-parse HEAD`, `git branch --show-current`, and `git status --short`.

Expected: the experiment documentation commits plus the pre-existing `.claude/settings.local.json` modification, which remains untouched.

### Task 2: Build and validate the immutable fixture

**Files:**
- Create temporarily: `/tmp/kieran-cms-causality/control/index.html`
- Create temporarily: `/tmp/kieran-cms-causality/control/_headers`

**Step 1: Create the fixture**

Create a complete HTML document containing:

```html
<section data-section="hero" data-label="Hero">
  <h1 data-edit="hero.kicker" data-type="richtext" data-label="Kicker">
    Navigate menopause. <span class="accent">Naturally, powerfully.</span>
  </h1>
  <h2 data-edit="hero.title" data-type="richtext" data-label="Title">
    The old you <span class="accent">is still here.</span>
  </h2>
</section>
<section data-section="facts" data-label="Facts">
  <article><p data-edit="facts.stat_1" data-type="text">STAT-ONE</p><p data-edit="facts.source_1" data-type="richtext">SOURCE-ONE</p></article>
  <article><p data-edit="facts.stat_2" data-type="text">STAT-TWO</p><p data-edit="facts.source_2" data-type="richtext">SOURCE-TWO</p></article>
  <article><p data-edit="facts.stat_3" data-type="text">STAT-THREE</p><p data-edit="facts.source_3" data-type="richtext">SOURCE-THREE</p></article>
</section>
<section data-section="accordion" data-label="Accordion">
  <button data-edit="accordion.label_1" data-type="text">QUESTION-ONE</button>
  <div data-edit="accordion.answer_1" data-type="richtext"><p>ANSWER-ONE</p></div>
  <button data-edit="accordion.label_2" data-type="text">QUESTION-TWO</button>
  <div data-edit="accordion.answer_2" data-type="richtext"><p>ANSWER-TWO</p></div>
</section>
```

Add responsive CSS that makes `.hero-title` 82px on desktop and 44px on mobile, and `.accent` italic with `rgb(196, 113, 75)`.

**Step 2: Validate sentinel uniqueness and annotation IDs**

Run `rg -o 'data-edit="[^"]+"|STAT-[A-Z-]+|SOURCE-[A-Z-]+|QUESTION-[A-Z-]+|ANSWER-[A-Z-]+' /tmp/kieran-cms-causality/control/index.html | sort`.

Expected: every field ID and sentinel appears exactly once.

**Step 3: Validate HTML parsing**

Run a read-only BeautifulSoup check that loads the fixture, asserts unique `data-edit` values, and confirms both accent spans retain a preceding text-node space.

Expected: PASS.

### Task 3: Capture the local parser/renderer baseline

**Files:**
- Read: `core/tests/test_annotator_backfill.py`
- Read: `core/tests/test_renderer_preserves_unedited.py`
- Read: `core/tests/test_renderer_styles.py`
- Create temporarily: `/tmp/kieran-cms-causality/local-baseline.json`

**Step 1: Run focused existing regression tests**

Run:

```bash
uv run python manage.py test \
  core.tests.test_annotator_backfill.BackfillCatchesUnmarkedBodyTextTests.test_heading_with_inline_children_gets_richtext_type \
  core.tests.test_renderer_preserves_unedited.NoOpApplyPreservesOriginalHtmlTests.test_unedited_richtext_preserves_inline_classes \
  core.tests.test_renderer_styles.TextFieldSelectionStyleTests
```

Expected: PASS on the current checkout.

**Step 2: Run the fixture through `build_schema` and a no-op `render_site`**

Record each field's inferred type/default and the rendered heading HTML in `local-baseline.json`.

Expected: mixed-style headings remain `richtext`, accent spans survive, spaces survive, and repeated sentinels remain paired.

**Step 3: Run one intentional plain-text overwrite locally**

Set `hero.title` to a plain edited value and record that the expected user edit replaces its contents without moving any neighboring field.

Expected: only `hero.title` changes.

### Task 4: Deploy and capture the Cloudflare Pages control

**Files:**
- Read: `/tmp/kieran-cms-causality/control/index.html`
- Create temporarily: `/tmp/kieran-cms-causality/control-capture/`

**Step 1: Create one disposable Pages project**

Use a unique name beginning `kieran-cms-causality-` and deploy only the control directory.

Expected: one public HTTPS URL; save the project ID/name and URL without saving credentials.

**Step 2: Verify byte-stable control content**

Fetch the public response and compare normalized DOM with the local fixture.

Expected: no text, attribute, element-order, or inline-style differences.

**Step 3: Capture control evidence**

At 1440×1000 and 390×844, capture screenshots plus JSON containing each test element's text, `innerHTML`, bounding rectangle, font family, font size, font style, font weight, and colour.

Expected: both accent phrases are italic orange, whitespace is present, and all sentinels remain paired.

### Task 5: Create the staging CMS throwaway and capture import state

**Files:**
- Create temporarily: `/tmp/kieran-cms-causality/staging-import/`

**Step 1: Create isolated staging records**

Through authenticated staging UI/API/management shell, create one uniquely named template and tenant from the Pages URL. Use no production database and no client data.

Expected: template schema contains the fixture fields and the tenant remains staging-only.

**Step 2: Record stored schema/default/content values**

Export only the throwaway template/tenant identifiers, field types, defaults, and content dictionary.

Expected: headings are `richtext`; every sentinel maps to its matching field.

**Step 3: Capture the immediate editor preview**

Capture normalized DOM, computed styles, and desktop/mobile screenshots before any content update.

Expected: exact semantic parity with the Pages control. Any divergence identifies import/annotation as the first failing stage.

### Task 6: Test no-op save and targeted edits

**Files:**
- Create temporarily: `/tmp/kieran-cms-causality/staging-noop/`
- Create temporarily: `/tmp/kieran-cms-causality/staging-edited/`

**Step 1: Perform a no-op save**

Save the throwaway site without changing field values, then capture stored content and rendered preview again.

Expected: no semantic or computed-style differences. A divergence identifies merge/save/render behavior as causal.

**Step 2: Perform bounded targeted edits**

Change only:

- `hero.title` to mixed inline HTML preserving an accent span;
- `facts.source_1` to `SOURCE-ONE-EDITED`;
- `facts.source_2` to `SOURCE-TWO-EDITED`;
- `accordion.answer_1` to `<p>ANSWER-ONE-EDITED</p>`.

Expected: exactly four fields change; field order and neighboring values remain intact.

**Step 3: Capture the edited state**

Capture stored values, normalized DOM, computed styles, and desktop/mobile screenshots.

Expected: accent markup and whitespace survive; no sentinel moves. Any first divergence identifies editor serialization/update behavior as causal.

### Task 7: Analyze causality and document the result

**Files:**
- Create: `docs/research/2026-08-25-kieran-cms-causality-results.md`

**Step 1: Produce stage-by-stage diffs**

Compare control → import → no-op save → targeted edit for element order, field IDs, text, HTML, styles, and stored values.

Expected: a compact diff identifying the first divergent stage or confirming none.

**Step 2: Apply the decision rules**

State one conclusion:

- current importer/annotator causal;
- current save/merge/renderer causal;
- current editor/update logic causal; or
- current staging engine did not reproduce, supporting historic production release, bulk revision, or corrupted Kieran content as the cause.

Separate direct evidence from inference and record checkout/staging release identifiers.

**Step 3: Write the research report**

Include test URLs only while live, timestamp, fixture description, results table, screenshots/evidence paths, conclusion, limitations, and recommended next diagnostic or repair action. Do not include credentials or cookies.

**Step 4: Commit the report**

Run:

```bash
git add docs/research/2026-08-25-kieran-cms-causality-results.md
git commit -m "docs: report Kieran CMS causality test"
```

Expected: only the report is committed; `.claude/settings.local.json` remains unstaged.

### Task 8: Remove all throwaway external state

**Files:**
- Read: `/tmp/kieran-cms-causality/`

**Step 1: Confirm evidence is complete**

Verify the committed report includes enough data to reproduce the conclusion before deleting anything.

Expected: report committed and external resource identifiers recorded locally.

**Step 2: Delete staging records**

Delete only the uniquely named throwaway tenant and template created by this experiment.

Expected: records no longer exist; no client or production record changed.

**Step 3: Delete the Pages project**

Delete only the uniquely named Pages project created in Task 4.

Expected: the control URL stops resolving or returns the provider's removed-project response.

**Step 4: Verify final repository state**

Run `git status --short` and `git log --oneline -n 3`.

Expected: only the pre-existing `.claude/settings.local.json` modification remains; experiment design and results commits are present.

