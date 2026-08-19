# Phase 13 Consistency, Copy, and Sidebar Design

## Scope

Phase 13 closes three inconsistencies without changing the Locked CMS visual world:

1. The page-list creation card moves its primary action from the bottom of the source editor to the card heading.
2. Product-authored em dashes are removed with sentence-specific punctuation and protected by a regression test.
3. The staff-only “New client” action moves from the end of agency navigation to the top of the sidebar.

## Card action structure

The page list is a multi-card screen, so neither creation card receives a sticky header. The “New page” card uses a compact heading row with the title on the left and an externally associated `Add page` submit button on the right. The form receives a stable `id`, and the button uses `form=` so the existing card and form structure remain intact. The old bottom `.source-form-actions` row is removed.

The sibling-import card adopts the same heading/action topology. Its existing JavaScript button moves beside the title while the URL input, explanation, and asynchronous status remain in their natural reading order below. Both action rows wrap at narrow widths, preserve DOM and focus order, and use the existing spacing, button, color, and radius tokens.

Alternatives rejected:

- Moving each heading inside its form would entangle card grouping with form padding and does not apply cleanly to the JavaScript-driven import action.
- Reusing the sticky source-page header would make a local card action look page-global and add another sticky layer to a list screen.

## Sidebar structure

For agency operators, `New client` becomes a standalone primary action immediately after the brand and before the collapse control and primary navigation. It stays outside `<nav>` so it reads as creation rather than destination selection. The existing staff/tenant condition is preserved exactly. The same link, icon, `aria-label`, and `data-sidebar-label` preserve expanded, collapsed-tooltip, keyboard, and mobile-drawer behavior.

The CTA keeps the incumbent blue button treatment. Its spacing becomes a scoped top-action rhythm rather than the old `margin-top` that separated it from the final nav item. No active-nav state is added.

## Em-dash policy

The guard scans product-owned source under `dashboard/`, `core/`, and `templates/` for the Unicode em dash. It includes tests, comments, docstrings, template comments, JavaScript embedded in templates, email text, and demo copy. Replacements are chosen sentence by sentence: commas for continuations, periods for independent clauses, colons for explanations or labels, and parentheses for genuine asides. Legal templates receive punctuation-only changes.

Named exceptions are recorded in the guard rather than hidden by directory-wide exclusions:

- Explicit test fixtures that faithfully represent fetched client pages, pasted third-party documents, or model output.
- String payloads inside historical migration operations. Migration comments and docstrings remain in scope.
- Documentation and plan directories, which are outside the scanned roots.

The target after the approved exceptions is zero unexplained occurrences. The test reports the exact path and line for any regression.

## Verification and release

Render tests establish the card header/form association, absence of the bottom action row, aligned sibling-import action, and sidebar DOM order/visibility before implementation. The copy guard starts red against the current source and turns green after the reviewed rewrites. Focused tests, the full Django suite, JavaScript syntax, the Impeccable detector, and `npm run check:assets` form the local gate.

Staging receives one batched browser round covering the page-list card at desktop and mobile, expanded and collapsed desktop sidebars, the mobile drawer, and regression captures of the two existing source-editor headers. At most one consolidated correction round follows. Production deploys only after staging passes, with `ca3c3c767499b22b1123f76199ea8708731a9414` recorded as the rollback baseline and no production data writes.
