# Dashboard UX Audit — Changes

Resolved: 2026-05-27

---

## Issues resolved

### HIGH

**1. Breadcrumb inconsistency** — All detail/form pages now use a shared `.breadcrumb`
component (left-aligned text trail with `/` separators). Replaces the inconsistent
mix of "← Back" buttons and inline subtitle breadcrumbs. Applied to: `tenant_detail`,
`user_detail`, `template_form`, `tenant_form`, `site_created`.

**2. tenant_detail overload** — Header cleaned up: title is a standalone h1, metadata
(badges, subdomain, template, custom domain) lives in a `.page-meta` row below.
Activity feed now shows the first 10 items with a "Show all" button (client-side
expand). All section cards use `.page-section` for consistent spacing.

### MEDIUM

**3. user_detail header noise** — Badges and metadata moved out of the h1 into a
`.page-meta` row. Last login kept in the meta row (useful for support context,
low visual weight).

**4. template_form delete placement** — Delete button moved into a `<details>`-based
danger zone section matching `tenant_detail`'s pattern. Consistent and harder to
accidentally trigger.

### LOW

**5. Inline style cleanup** — Extracted repeated inline patterns into CSS classes:
- `margin-top: 24px` on table cards and empty states → `.content-block`
- `padding: 0` on table-wrapping cards → `.card-flush`
- `margin-bottom: 32px` on detail-page sections → `.page-section` (already existed)
- `margin: 0` on h2 inside flex containers → `.row-between > h2` rule
- Danger zone h2/p colors → `.danger-zone > h2`, `.danger-zone > p` rules

**6. Action label vocabulary** — Standardized to "Details" for row-to-detail-page
links, "Edit" for editor-route links. User list "View" → "Details".

### Visual issues

**V1. tenant_detail header** — Fixed via `.page-meta` (see HIGH #2).

**V2. user_detail action buttons** — Destructive actions (Deactivate) moved to a
separate danger zone card with red styling. Safe actions (Reset password, Promote)
stay in a normal "Actions" card. Removed the `.action-grid` that equalized them.

**V3. template_form warning card** — Custom inline colors (`--color-warn-soft`,
`#fff7ed`, etc.) replaced with `.warn-card` class backed by new design tokens
(`--color-warn`, `--color-warn-soft`, `--color-warn-strong`).

**V4. custom_domain_list column header** — "Overrides" → "Actions" to match all
other table headers.

---

## New CSS patterns (canonical)

| Class | Purpose | Use when |
|-------|---------|----------|
| `.breadcrumb` | Inline breadcrumb links above page title | Every detail/form page |
| `.breadcrumb-sep` | `/` separator between breadcrumb items | Inside `.breadcrumb` |
| `.page-meta` | Metadata row (badges, code, text) below page title | Detail page headers with badges/metadata |
| `.page-section` | Consistent `margin-bottom` for stacked sections | Every card section on detail pages |
| `.content-block` | `margin-top: 24px` gap after filter bars | Table cards and empty states on list pages |
| `.card-flush` | `padding: 0` for cards wrapping data-tables | Table cards on list pages |
| `.warn-card` | Warning card using `--color-warn-*` tokens | Validation warnings, no-sections-detected |
| `.danger-zone` | Red-bordered card with `> h2` and `> p` styling | All destructive action sections |

### Design tokens added

```css
--color-warn: #f59e0b;
--color-warn-soft: #fff7ed;
--color-warn-strong: #92400e;
```

### Dead CSS removed

- `.action-grid` — no longer used after user_detail restructure.

---

## Deliberately scoped out

- **tenant_detail card hierarchy** (tabs, two-column, collapse): The 5 cards now have
  differentiated _behavior_ (Members/Settings are primary, Activity has pagination,
  Danger Zone has expand-to-confirm) which creates implicit hierarchy. A visual
  restructure (tabs or two-column) would be higher scope and risks regression in the
  member-add flow, settings form, and HTMX custom-domain section. Revisit when the
  page gets a 6th card.
- **h2/p `margin: 0` inside stack containers**: Most removed, but a few remain where
  they're genuinely context-specific (empty-state messages as last children of stacks,
  where the margin would add unwanted space before the card's bottom padding).
- **Overview page, Sites list, Editor, credentials page**: Explicitly not touched per
  audit ("leave alone").
- **home.html inline styles**: The overview page uses some `margin: 0` on headings
  inside flex/stack containers. Not touched since the page is flagged as "leave alone."
- **Inline styles in overlay/compare UI** (template_form, tenant_form): These are
  complex modal components with position/inset/flex layout that are genuinely
  one-off. Extracting them into classes would create single-use CSS with no reuse
  benefit.
