# Action Icons Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add consistent, accessible Lucide action icons to dashboard menus and labelled action buttons without adding a dependency or changing control sizing.

**Architecture:** Hand-inline the same 24×24 stroked Lucide SVG convention already used by `templates/base.html`. Add one shared 16px `.menu-icon` rule and flex alignment for menu items, then reuse exact sidebar paths for matching actions and the existing danger token for destructive actions.

**Tech Stack:** Django templates, shared dashboard CSS/Tailwind asset build, existing vanilla-JavaScript action menus.

---

### Task 1: Lock the shared icon treatment

**Files:**
- Modify: `frontend/dashboard.css`
- Test: `static/css/dashboard.css`

1. Add `.menu-icon` beside `.nav-icon` at 16×16 with a non-shrinking flex basis.
2. Change action-menu links and buttons from block layout to aligned flex rows with the existing padding and line-height.
3. Keep row-menu triggers at 18×18 and remove the filled-icon override.
4. Run `npm run build:css`; expect the generated dashboard stylesheet to update successfully.

### Task 2: Add icons to the specified action surfaces

**Files:**
- Modify: `templates/dashboard/tenant_detail.html`
- Modify: `templates/dashboard/page_list.html`
- Modify: `templates/dashboard/tenant_list.html`
- Modify: `templates/dashboard/blog_list.html`
- Modify: `templates/dashboard/editor.html`
- Modify: `templates/dashboard/blog_form.html`

1. Replace every filled-circle row trigger with a standard 24×24 stroked Lucide ellipsis.
2. Add decorative, `aria-hidden="true"` inline SVGs to action-menu items and labelled action buttons, preserving their text labels.
3. Reuse exact paths from `templates/base.html` for Editor/Edit, Pages, Blog, Preview site/View site, and Team.
4. Use `file-code`, `upload`, `eye`, `settings`, `history`, `image`, `user-plus`, `arrow-right`, `copy`, and `trash-2` where the brief maps those actions.
5. Keep confirmations and danger text intact, with `trash-2` inheriting the existing danger color.

### Task 3: Extend repeated actions consistently

**Files:**
- Modify matching files under: `templates/dashboard/`

1. Search other dashboard templates for the same action labels.
2. Add the same exact SVG for repeated labelled buttons such as Details, Remove, Delete, Preview, and Edit HTML source.
3. Grep all added action SVGs and verify each includes `aria-hidden="true"`; verify every action retains visible text.

### Task 4: Verify behavior and rendered output

**Files:**
- Create: `docs/ui-audit/action-icons-*.png`

1. Run `python manage.py test`; expect all tests to pass and record the new count.
2. Run `npm run check:assets`; expect no uncommitted generated-asset drift.
3. Start the local app with seeded data and capture 375px, 768px, and 1280px screenshots for an open row menu, the open editor Tools menu, and the sites-list trigger.
4. Inspect the screenshots together for unchanged menu row height, no wrapping at 375px, coherent stroke weight, and danger styling; make at most one correction pass.
5. Run the Impeccable detector once across changed UI files.
6. Run `docker compose -f docker-compose.yml build`; expect a successful build.

### Task 5: Ship and verify staging

**Files:**
- Commit all implementation, generated assets, screenshots, and this plan.

1. Review `git diff` for accidental churn.
2. Commit the scoped changes without opening a pull request.
3. Push `feat/cms-ui-followups` so staging redeploys.
4. Check the deployed staging paths and confirm icons, triggers, menus, accessibility labels, and responsive behavior.
5. Run the required `herdr-say.sh --state done --task CMS-UI-4` report with one concrete result sentence.
