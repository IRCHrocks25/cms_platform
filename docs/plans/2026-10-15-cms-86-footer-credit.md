# CMS-86 Footer Credit Implementation Plan

**Goal:** Let a tenant hide the complete `footer.builtBy` credit from every page, while leaving existing sites unchanged by default.

**Design:** Store `show_footer_credit` as a boolean in `Tenant.site_settings`, defaulting to true when absent. Expose a native checkbox in Navigation → Footer only when the template has `footer.builtBy`. Send a narrow authenticated PATCH to the existing site-settings endpoint so other settings cannot be overwritten. The renderer removes the credit wrapper and adjacent separator when false; home, inner-page preview/public, and blog chrome use the same tenant setting. The existing link/label fields remain editable.

**Tests first:**

1. Add renderer tests for normalized unchanged output when the setting is absent or true, hidden wrapped text plus link, adjacent separator cleanup, and preservation of unrelated footer content.
2. Add endpoint tests for default true, tenant-scoped PATCH, invalid values, persistence, and settings POST preservation. Add editor GET assertions for the labelled checkbox.
3. Run the targeted tests red. Implement the renderer and settings path, then the checkbox and preview reload. Run targeted tests green.
4. Run repository tests and checks, inspect the diff, commit with personal identity, push, open a PR, and wait for hosted CI. Record local and unrun staging/production evidence in the PR and Plane.

**No migration:** `site_settings` is already a JSON field; absent key means shown.
