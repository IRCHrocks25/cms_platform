# Agency Dashboard Redesign Plan

---

## Information Architecture

**No structural change to top-level nav.** The 5 items (Overview, Sites, Templates,
Domains, Users) each represent a distinct object type and the list is under 7.

### Consistent page-type patterns

| Page type | Pattern |
|-----------|---------|
| **List** | PageHeader (title + subtitle + CTA) → FilterBar → Table/Empty |
| **Detail** | Breadcrumb → PageHeader (title + metadata row + actions) → Sections |
| **Form** | Breadcrumb → PageHeader (title + subtitle) → Card with form |
| **Post-action** | PageHeader → Result card(s) → Next-step actions |

---

## Component Patterns

### 1. Breadcrumb
Every detail and form page gets a breadcrumb above the page title:
```
Sites / Acme Corp
Templates / Restaurant Landing
Users / alice
```
Simple text links, no icons. Replaces the mix of "← Back" buttons and inline
subtitle breadcrumbs. The breadcrumb IS the back navigation.

### 2. PageHeader metadata
Detail pages (tenant_detail, user_detail) move metadata (badges, subdomain, template)
out of the h1 into a dedicated `.page-meta` row below the title:
```
[breadcrumb]
# Acme Corp
Published · acme.localhost · Restaurant template
[Open editor] [View live site]
```

### 3. Section spacing
All card-sections on detail pages use a single CSS class (`.page-section`)
instead of inline `margin-bottom` styles.

### 4. Destructive actions
Consistently use the `<details>` expand pattern (already in tenant_detail's
danger zone). The delete on template_form should match this.

### 5. Action labels
Standardize across tables:
- Primary action per row: contextual verb ("Edit" for editable, "Open" for viewable)
- Secondary link to detail: always "Details" (not "View")

---

## Page-by-page Changes

### tenant_detail.html
- Add breadcrumb: `Sites / {name}`
- Clean up header: title on its own line, metadata as `.page-meta` row
- Add section headings that create visual hierarchy
- Replace inline margin with `.page-section` class
- Keep the existing `<details>` expand pattern for delete

### user_detail.html
- Add breadcrumb: `Users / {username}`
- Move badges out of h1 into `.page-meta` row
- Separate destructive actions visually from safe actions
- Remove "Last login" from subtitle (it's not actionable; move to meta row)

### template_form.html
- Add breadcrumb: `Templates / {name}` (or `Templates / New`)
- Move delete button into a proper danger zone section at the bottom,
  matching tenant_detail's pattern

### tenant_form.html
- Replace "← Back to sites" button with breadcrumb: `Sites / New client`

### custom_domain_list.html
- Rename "Overrides" column to "Actions" (matches every other table)

---

## CSS Additions

```css
.breadcrumb { ... }      /* Simple inline links above page title */
.page-meta { ... }       /* Metadata row (badges, links) below title */
.page-section { ... }    /* Consistent card section spacing */
```

---

## What's intentionally left alone

- **Overview page** — clean, balanced, no changes
- **Sites list** — solid table, good filters, good empty state
- **site_created page** — beautiful, don't touch
- **credentials page** — focused and secure
- **tenant_form** — long but well-structured, only breadcrumb change
- **custom_domain partial** — complex but functional
- **Editor (client-facing)** — out of scope per constraints
