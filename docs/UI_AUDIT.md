# CMS platform UI/UX audit

**Audit date:** 2026-08-14  
**Mode:** Operate  
**Scope:** Every template under `templates/dashboard/`, `templates/base.html`, all files under `static/css/` and `static/js/`, and the dashboard view paths that supply them.  
**Direction:** Redesign the visual and interaction system while preserving product truth, content, routes, permissions, and the locked-structure editing model.

## Executive summary

The current dashboard is functional but does not yet behave like one coherent product. Its strongest foundations are the schema-derived editor, visible keyboard focus in the base layer, useful empty states, defensive server validation, and the live preview bridge. The main problems are structural: the primary editor is desktop-only, system status is incomplete, interaction patterns are not consistently keyboard-operable, and three styling systems compete across the app (global CSS, page-local CSS, and 282 inline style declarations).

The editor complaints in the brief are all substantiated:

- **Shell:** a three-column grid remains fixed at narrow widths, while a crowded top bar contains up to ten actions and no overflow strategy.
- **Coding:** raw HTML is a plain textarea with no line numbers, highlighting, bracket matching, search, or standard editor shortcuts.
- **Efficiency:** the section navigation is mouse-only, common top-bar actions are visually equal, and the editor downloads 18 Google Font families when initialized.
- **Loading:** autosave retries forever without an actionable recovery control; preview frames initially show a blank surface; several long operations use estimated progress or text-only status that is not announced.

This is a **redesign of the visual world and interaction language, with preservation of product truth**. Keeping the current palette and Roboto is appropriate: the palette already communicates state clearly where it is applied correctly, and changing type would not solve the structural problems. The overhaul should replace—not decorate—the current component layer.

### Audit health score

| # | Dimension | Score | Key finding |
|---|---|---:|---|
| 1 | Accessibility | 1/4 | Core editor navigation and modals are not keyboard/screen-reader complete. |
| 2 | Performance | 2/4 | Assets are dependency-light, but editor initialization and font loading are unbounded and loading behavior is visually unstable. |
| 3 | Responsive design | 1/4 | The main editor has no structural breakpoint; tables have no narrow-screen strategy. |
| 4 | Theming | 2/4 | Good root tokens exist, but local tokens, inline colors, fallbacks, and legacy “purple” names cause drift. |
| 5 | Implementation integrity | 1/4 | The UI is split across global, local, and inline systems and repeats complex editor logic. |
| **Total** |  | **7/20 — Poor** | **A coherent overhaul is warranted before release.** |

### Finding count

- P0 blocking: 0
- P1 major: 8
- P2 minor: 8
- P3 polish: 2

## Implementation integrity verdict

**Fail.** The implementation expresses the product model well, but not a coherent product-specific interface system. `static/css/base.css:4-57` establishes useful tokens, yet `templates/dashboard/assistant_form.html:6-31`, `templates/dashboard/assistant_list.html:6-32`, and `templates/dashboard/site_created.html:5-145` each create a local visual system. Across dashboard templates there are 282 inline `style` attributes and 167 hard-coded color/function occurrences across templates and static assets. Similar actions therefore change shape, density, color, and feedback behavior by page.

The mechanical Impeccable detector reported six warnings: layout-property animation in `_html_source_editor.html:99` and `tenant_form.html:199`; empty image sources in `components/field.html:21` and `editor.html:366`; and one-sided warning accents in `editor.html:62` and `template_form.html:27`. The empty image sources are conditionally filled by JavaScript, so they are not always user-visible broken images, but they still create an avoidable invalid initial state. The layout-transition warnings are valid. The accent warnings are visual-system drift rather than functional defects.

## Findings by severity

### P1 — Major

#### 1. The main editor is not responsive below desktop width

- **Location:** `static/css/editor.css:4-28`, `templates/dashboard/editor.html:70-275`
- **Category:** Responsive / task completion
- **Failure scenario:** At 375px and 768px, the editor still requests `200px 336px 1fr` (or `0 400px 1fr` in compact mode). The form and preview overflow the viewport with no Write/Preview mode switch. A client on a phone cannot reliably reach or understand the preview pane.
- **Widths:** 375 fails structurally; 768 still cannot fit the standard editor; 1280 is usable but cramped with the 556px navigation/form reservation; 1920 has excess preview space but unchanged form density.
- **Standard:** WCAG 1.4.10 Reflow.
- **Recommendation:** Replace section-count-driven presentation with viewport-driven modes. Wide screens get sidebar/form/preview; medium screens get form/preview plus a collapsible section drawer; narrow screens get explicit Edit/Preview tabs and a sticky save/publish action area.

#### 2. Editor section navigation is mouse-only

- **Location:** `templates/dashboard/editor.html:90-101`, `static/js/editor.js:886-899`
- **Category:** Accessibility / efficiency
- **Failure scenario:** Section entries are `<div class="sidebar-link">` elements with click handlers. They are not in the tab order and expose neither link nor button semantics, so keyboard and assistive-technology users cannot jump between sections.
- **Standard:** WCAG 2.1.1 Keyboard; 4.1.2 Name, Role, Value.
- **Recommendation:** Render semantic buttons or anchored links, expose the current section, support Enter/Space natively, and retain focus when switching panels.

#### 3. Custom modals do not manage focus or announce themselves

- **Location:** `templates/dashboard/editor.html:277-392`, `static/js/editor.js:942-1029`, `static/js/editor.js:1032-1126`, `static/js/editor.js:1128-1527`
- **Category:** Accessibility / keyboard
- **Failure scenario:** Settings, history, and gallery are generic `<div>` overlays without `role="dialog"`, `aria-modal`, labelled relationships, focus placement, focus containment, or focus restoration. A keyboard user can tab behind an open modal and may lose their place when it closes.
- **Standard:** WCAG 2.1.1 Keyboard; 2.4.3 Focus Order; 4.1.2 Name, Role, Value.
- **Recommendation:** Use native `<dialog>` where practical or a single accessible dialog primitive with initial focus, Escape, focus trap, inert background, and return-focus behavior.

#### 4. Autosave can silently overwrite newer edits and offers no real recovery

- **Location:** `static/js/editor.js:57-94`
- **Category:** Loading / error recovery / data integrity
- **Failure scenario:** Every save serializes the mutable global `content`, requests are not sequenced or aborted, and a failure recursively retries every two seconds forever. The message says “retrying” but offers no retry/cancel action, does not announce status, and cannot distinguish offline, authentication, validation, or server failure. Concurrent requests can resolve out of order.
- **Standard:** Nielsen visibility of system status and error recovery; WCAG 4.1.3 Status Messages.
- **Recommendation:** Implement an explicit state machine (`idle`, `dirty`, `saving`, `saved`, `failed`, `offline`), one in-flight request, revision tokens or queued latest-state saving, `aria-live`, and a visible retry action that preserves edits.

#### 5. Preview frames have a blank, unstable initial state

- **Location:** `templates/dashboard/editor.html:259-272`, `templates/dashboard/blog_form.html:175-191`, `static/css/editor.css:416-495`
- **Category:** Loading / perceived performance
- **Failure scenario:** On a slow response the preview pane is an empty white frame with a “Live preview” label before the iframe bridge is ready. Users cannot tell whether it is loading, broken, or empty; the frame can change dimensions when viewport modes are applied.
- **Recommendation:** Reserve the final frame geometry, render a skeleton and “Loading preview” status over it, clear it only after iframe `load` plus bridge `ready`, and expose timeout/retry guidance.

#### 6. The raw-HTML coding surface is below the expected standard

- **Location:** `templates/dashboard/_html_source_editor.html:5-44`, `templates/dashboard/_html_source_editor.html:122-470`
- **Category:** Coding surface / efficiency / error prevention
- **Failure scenario:** Operators edit production HTML in a 22-row textarea without syntax highlighting, line numbers, bracket matching, indentation, search, diagnostics, or undo-aware comparison. Large documents and AI output are difficult to inspect, making structural mistakes likely.
- **Recommendation:** Adopt modular CodeMirror 6 with HTML language support, line numbers, bracket matching, history, search, lint-ready extension points, accessible labels, and textarea form synchronization. Do not ship unrelated language modes.

#### 7. Tables and dense action clusters have no mobile alternative

- **Location:** `static/css/base.css:756-805`; representative tables at `tenant_list.html:40-95`, `tenant_detail.html:51-89`, `page_list.html:146-201`, `integrations.html:81-164`
- **Category:** Responsive / cognitive load
- **Failure scenario:** Seven-column site and user tables retain their full width, while `.row-actions` refuses wrapping. At 375px and 768px content either overflows or compresses into unreadable cells; destructive and routine actions remain adjacent.
- **Standard:** WCAG 1.4.10 Reflow; touch-target guidance.
- **Recommendation:** Add a responsive table wrapper at tablet widths and product-specific stacked rows/cards at phone widths. Preserve true tables for wide screens and screen readers.

#### 8. Muted text fails WCAG AA at its actual sizes

- **Location:** `static/css/base.css:14`, `static/css/base.css:200-203`, `static/css/base.css:522-548`, `static/css/editor.css:434-440`
- **Category:** Accessibility / contrast
- **Failure scenario:** `#8a8fa3` on white is **3.21:1**, but it is used for 11–14px helper text, timestamps, labels, and statuses. Users with low vision lose instructions and state information. White on the danger hover `#ef4444` is **3.76:1** for 14px button text.
- **Standard:** WCAG 1.4.3 Contrast (Minimum), 4.5:1 for normal text.
- **Recommendation:** Darken the muted semantic token and the danger interactive background or use dark text where appropriate. Recheck every semantic state, not just the brand colors.

### P2 — Minor

#### 9. Editor top bars expose too many equal-priority actions

- **Location:** `templates/dashboard/editor.html:33-57`, `templates/dashboard/blog_form.html:11-34`
- **Category:** Information architecture / cognitive load
- **Failure scenario:** The site editor presents save state plus View, Pages, Blog, Team, History, Gallery, Settings, and Publish in one row. On smaller laptops this wraps or compresses, and users must scan nine competing choices before the primary publish action.
- **Recommendation:** Keep context, save status, preview mode, and publish visible; group secondary site tools in a labelled overflow/menu or contextual drawer.

#### 10. Page management is separated from the primary site object

- **Location:** `templates/dashboard/tenant_detail.html:14-40`, `dashboard/views.py:1047-1097`, `dashboard/views.py:1713-1779`
- **Category:** Information architecture
- **Failure scenario:** The site detail page shows members, domains, activity, settings, integration, and danger actions but not the site's pages. Operators must leave the client context for `/sites/<pk>/pages/`, then mentally reconnect page status and site identity.
- **Recommendation:** Make Pages a first-class section of site detail, with status, URL, edit, view, publish, and agency-only create controls. Preserve the existing list route as a redirect or compatible focused view.

#### 11. Long-running AI progress is explicitly fictitious

- **Location:** `_html_source_editor.html:181-205`, `_html_source_editor.html:308-393`; duplicated at `tenant_form.html:513-571`, `tenant_form.html:651-751`
- **Category:** Loading / trust
- **Failure scenario:** A timer asymptotically advances toward 95% without server progress. A 120-second operation can appear nearly complete for most of its duration, undermining trust, and status updates are not in a live region.
- **Recommendation:** Use staged, honest progress (“queued”, “processing”, elapsed time, “still working”) derived from job status. Use indeterminate motion only when its meaning is explicit and provide retry after a concrete failure.

#### 12. The same source-editor workflow is implemented twice

- **Location:** `_html_source_editor.html:46-470`, `tenant_form.html:108-219`, `tenant_form.html:371-770`
- **Category:** Implementation integrity
- **Failure scenario:** Fetch, preview, compare, polling, retry, and progress logic are duplicated with different copy and timeout behavior. A bug fix in one path can leave new-client creation inconsistent with template/page HTML editing.
- **Recommendation:** Extract one HTML-editor component/template and one JS module, with configuration supplied through data attributes.

#### 13. Client-authored rich text is exposed to global resets

- **Location:** `static/css/base.css:59-109`, `static/css/blog.css:183-204`, `templates/dashboard/blog_form.html:62-82`
- **Category:** Theming / content safety
- **Failure scenario:** Blog content is placed directly in a dashboard `contenteditable`. A global reset or unscoped Tailwind Preflight can erase heading, list, and link semantics inside real client content.
- **Recommendation:** Disable Preflight globally and scope a deliberate reset to dashboard chrome, or add a strong content boundary whose typography rules explicitly restore headings, lists, links, media, and blockquotes. Verify with representative content before and after.

#### 14. Drag-and-drop ordering has no keyboard or touch equivalent

- **Location:** `blog_list.html:181-197`, `static/js/blog_reorder.js:18-76`
- **Category:** Accessibility / mobile
- **Failure scenario:** Featured posts can only be reordered with HTML5 drag events. Keyboard-only and many touch users cannot perform the task.
- **Standard:** WCAG 2.1.1 Keyboard.
- **Recommendation:** Add visible Move up/Move down controls with position announcements; retain drag as an accelerator.

#### 15. Many touch targets are below 44×44px

- **Location:** `static/css/base.css:182`, `static/css/editor.css:540-578`, `static/css/editor.css:866-997`, `static/css/blog.css:5-16`
- **Category:** Responsive / accessibility
- **Failure scenario:** 20–32px visibility, style, close, star, and sidebar controls are difficult to hit one-handed at 375px and can be activated accidentally.
- **Standard:** WCAG 2.5.8 Target Size (Minimum) guidance.
- **Recommendation:** Guarantee at least 44px hit areas on touch layouts, even when glyphs remain visually compact.

#### 16. Async status is inconsistently announced and sometimes disappears

- **Location:** `section-fetch.js:49-72`, `blog_editor.js:197-232`, `tenant_form.html:276-300`, `page_list.html:80-140`
- **Category:** Loading / error states
- **Failure scenario:** Domain fetch swaps disable buttons but do not show loading status; blog sanitization suppresses failures and displays stale content as “Live preview”; subdomain network failure clears the status; page import writes HTML into a non-live container.
- **Recommendation:** Standardize pending, success, failure, and recovery behavior in shared status components with `aria-live` and disabled/busy states.

### P3 — Polish

#### 17. UX vocabulary and capitalization drift

- **Location:** `tenant_detail.html:38` (“Open editor”), `tenant_list.html:87` (“Edit”), `page_list.html:175-185` (“Edit”/“Publish”), `assistant_form.html:135-149` (“Script tag”/“Direct iframe”/“Widget URL”), `editor.html:281-326` (“Site Settings”/“Save Settings”)
- **Category:** UX copy / consistency
- **Failure scenario:** Equivalent actions and sentence/title casing differ, increasing the small translation cost on every screen.
- **Recommendation:** Establish a concise vocabulary: Edit site, View site, Publish/Unpublish, Settings; use sentence case throughout.

#### 18. Decorative motion is not always tied to state

- **Location:** `site_created.html:134-143`, `assistant_list.html:16-17`, `static/css/base.css:676-682`
- **Category:** Motion / implementation integrity
- **Failure scenario:** Staggered page-entry animation and hover lift add motion to routine Operate surfaces without communicating a state change. The global reduced-motion rule removes everything rather than providing intentional alternatives.
- **Recommendation:** Keep 150–250ms transitions for state, selection, and disclosure; remove choreographed entry and decorative lift.

## Information architecture and cognitive load

### Agency shell

The agency navigation has seven top-level destinations plus a separate New client call to action (`templates/base.html:61-95`). Grouping “Automation” helps, but Sites remains the primary business object while Pages, Blog, domains, members, and integrations are scattered between global and per-site screens. The redesign should make the site detail the operational hub and keep global navigation for cross-site collections only.

### Client shell

The tenant sidebar is appropriately small, but the editor replaces it with a separate top-bar shell. That context switch is useful for screen space but loses the product's standard navigation and overloads one horizontal row. A consistent editor frame should retain site identity, page switching, save state, preview control, and one primary action.

### Editor cognitive-load checklist

| Check | Result | Evidence |
|---|---|---|
| Single focus | Fail | Form, preview, section nav, formatting bubble, and up to nine top actions compete. |
| Chunking | Partial | Sections and tabs help, but Design exposes 24 color swatches per choice (`editor.js:180-185`). |
| Grouping | Partial | Content/Nav/Design grouping is sound; secondary top-bar tools are ungrouped. |
| Visual hierarchy | Fail | Secondary tools share button weight with task-critical actions. |
| One thing at a time | Partial | Live preview supports the task, but several overlays and toolbars compete. |
| Minimal choices | Fail | Top bar and color palettes exceed the four-item working-memory guideline. |
| Working memory | Fail | Separate Pages and site-detail routes require context switching. |
| Progressive disclosure | Pass | Style panels, template instructions, and danger-zone confirmations use disclosure. |

Five failures indicate high avoidable cognitive load.

## Responsive behavior by target width

| Width | App shell | Editor | Data/detail pages |
|---:|---|---|---|
| 375px | Sidebar becomes a drawer, but toggle is 40px. | Fixed grid overflows; top bar cannot fit; many 20–32px targets. | Tables overflow; page padding consumes 64px; action rows wrap without priority. |
| 768px | Drawer behavior is active. | Still fixed three-column; no edit/preview tabs. | Detail grid collapses, but tables and wide forms remain problematic. |
| 1280px | Stable sidebar and content area. | Usable but form is fixed at 336px and action bar is crowded. | Good base layout; inline styles produce inconsistent density. |
| 1920px | Content pages stop at 1180px, which is readable. | Preview receives most extra space while form remains 336px; no resizable panes. | Large unused gutters are acceptable for reading, less efficient for dense operations. |

## Positive findings to preserve

- `dashboard/views.py:2176-2182` rebuilds the editor schema and then calls `merge_with_defaults()`, preserving canonical content behavior.
- `static/css/base.css:80-89` provides a visible global focus treatment, and most form fields have real labels.
- `dashboard/views.py:1749-1756` explicitly preserves the locked-structure promise for pages.
- The preview protocol remains clearly separated and uses the required `cms-editor`/`cms-preview` source strings (`static/js/editor.js:96-119`, `static/js/editor.js:381-400`).
- Empty states generally explain the next action instead of only saying “No results,” for example `tenant_list.html:96-107` and `blog_list.html:101-112`.
- Destructive site deletion requires typing the subdomain (`tenant_detail.html:304-321`), which is proportionate error prevention.

## Phased implementation plan

### Phase 1 — Build pipeline

1. Add pinned Tailwind CSS, Basecoat UI, Alpine, and modular CodeMirror packages with a lockfile.
2. Disable or tightly scope Preflight before any generated CSS reaches client-authored content.
3. Emit a committed dashboard stylesheet into `static/` so Python-only developers and tests do not require Node.
4. Add a Node builder stage to `Dockerfile`, copy only built assets into the final Python 3.12 image, then run `collectstatic`.
5. Verify hashed CSS in the static manifest and build the unchanged production compose configuration.

### Phase 2 — Design system and app surfaces

1. Create one semantic token and component layer mapped to Basecoat: buttons, fields, selects, badges, alerts, cards, tables, dialogs, tabs, status, skeletons, empty states, and responsive page shells.
2. Rename the visual use of legacy purple classes to primary/accent semantics while retaining compatibility only as necessary during migration.
3. Migrate every dashboard template and the auth shell; remove page-local style blocks and replaced CSS.
4. Add shared responsive table/list patterns and standardized async/form-submit feedback.

### Phase 3 — Editors

1. Rebuild the editor shell around viewport-driven modes and a section drawer; preserve compact/standard/dense as content-density hints, not breakpoint substitutes.
2. Implement accessible tabs, dialogs, section navigation, touch targets, and keyboard actions.
3. Add the autosave state machine, preview skeleton/readiness state, offline/error recovery, and honest long-operation status.
4. Replace the raw textarea with a lean CodeMirror 6 HTML configuration and reuse it in template, page, and inline new-client workflows.
5. Protect rich-text content from Preflight and verify headings, lists, links, blockquotes, and images before/after.

### Phase 4 — Consolidated site and pages view

1. Make the site the primary object and add a first-class Pages section near its operational overview.
2. Include page status, canonical path, last edit, edit/view/publish actions, and agency-only creation without transplanting the entire old page.
3. Preserve `/sites/<pk>/pages/` deep links with a compatible focused route or redirect.
4. Keep clients able to edit/publish existing pages but never add/remove them.

### Phase 5 — Verification

1. Run the full Python suite on Python 3.12 and report the actual count and any intentional test changes.
2. Capture before/after editor, site detail, and sites-list screenshots at 375, 768, and 1280 in `docs/ui-audit/`.
3. Run keyboard-only and WCAG AA contrast passes.
4. Build production compose, deploy this branch to staging, check `/healthz`, and complete the first-run editor smoke test.

## Explicit non-goals

- No React, shadcn React components, htmx, or a second client application.
- No staging redesign or changes to `docker-compose.yml` or the three staging isolation protections.
- No section or page add/remove controls for clients.
- No client raw-HTML editing.
- No schema-storage change, alternate content source, per-tenant user model, or plaintext credential handling.
- No public-site redesign; client markup is only protected from dashboard styles.
- No custom-domain routing test on staging; tenant public renders are verified locally as documented.
- No unrelated backend feature work, role expansion, email delivery, image pipeline, or version-history expansion for inner pages.

## Audit method and current limitations

The audit combined complete source inspection, route/context tracing, deterministic pattern counts, WCAG contrast calculations, and the Impeccable detector. Runtime Django checks could not run in the initial audit environment because dependencies were not installed; the repository correctly selected Python 3.12.11. Browser screenshots and timed interaction measurements are deferred to the required bounded before/after verification pass after the local environment is seeded. Static asset baselines and interaction timings will be recorded before optimization so efficiency claims remain evidence-based.
