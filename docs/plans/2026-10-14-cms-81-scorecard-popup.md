# CMS-81 Scorecard Popup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose Jill Gentry's three Scorecard popup role labels and URLs in the Kate editor.

**Architecture:** A guarded management command adds field markers to the installed site's stored template. The existing parser, editor, renderer, and template version service handle editing and publishing. The command is dry-run by default and applies one versioned, rendered-content-preserving template change only with `--apply`.

**Tech Stack:** Django, BeautifulSoup, Django TestCase, existing template service.

---

### Task 1: Define the markup contract in a failing test

**Files:**
- Create: `core/tests/test_annotate_jill_scorecard_popup.py`
- Create: `core/management/commands/annotate_jill_scorecard_popup.py`

1. Build a small but structurally faithful modal fixture: `#scorecardModal`, `.roles`, three `a.role[data-quiz]` entries with SVG, `.r-title`, `.r-sub`, and existing `auto.n9`/`auto.n12` fields outside the roles wrapper.
2. Test the pure annotation function returns exactly three URL fields and three label fields in a new `scorecard_popup` section. Check old markers, hrefs, SVG, subtitles, and trigger attributes remain intact.
3. Test that `build_schema` exposes six fields with distinct default values, and that `render_site` independently updates a role label and URL while preserving other roles and markup.
4. Run `python manage.py test core.tests.test_annotate_jill_scorecard_popup` with test settings. Confirm the tests fail because the command/function does not exist.

### Task 2: Implement guarded annotation

**Files:**
- Modify: `core/management/commands/annotate_jill_scorecard_popup.py`

1. Implement `annotate_scorecard_popup(html)` to locate exactly one modal and role container, validate the three known `data-quiz` anchors and their `.r-title` descendants, and add `data-section`, `data-label`, `data-type`, and `data-edit` attributes. Keep the annotation limited to attributes the rendered-content preservation guard already treats as metadata.
2. Reject missing/duplicate roles and conflicting markers with `CommandError`. Return the original HTML when already correctly annotated; avoid reserializing a whole page on the idempotent path.
3. Run the focused test again and make it pass.
4. Commit this focused test and implementation.

### Task 3: Add safe installed-template update

**Files:**
- Modify: `core/management/commands/annotate_jill_scorecard_popup.py`
- Modify: `core/tests/test_annotate_jill_scorecard_popup.py`

1. Add command tests using an installed-domain tenant and owned template. Assert default dry run writes nothing, `--apply` creates one `TemplateVersion`, preserves stored content and public output, and a second apply creates no new version. Assert wrong domain or unexpected markup cannot write.
2. Implement lookup by exact `CustomDomain.domain`; refuse zero or multiple matches, shared/library templates, and HTML changes between dry run and write. Use the existing versioned service with `preserve_rendered=True` and a clear version label.
3. Run focused tests red/green and commit.

### Task 4: Verify and hand off

**Files:**
- Modify: CMS-81 Plane testing section and comment (external)

1. Run `python manage.py test core.tests.test_annotate_jill_scorecard_popup` and `python manage.py test` in the isolated worktree. Run `npm ci && npm run build` if the repo's gate requires it. Record exact environment, commands, results, and any unrun live checks on CMS-81.
2. Review `git diff`, commit, push with the repo's pre-push gate, open a PR, and move CMS-81 to In Review. Do not mark Done.
3. After deployment, run the command without `--apply` against the real Jill installed account, inspect the diff and existing overrides, then apply and perform browser/live save, reload, and published-popup checks. Keep the ticket open until these actually pass.
