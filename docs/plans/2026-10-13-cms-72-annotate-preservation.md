# CMS-72 Annotate Preservation Implementation Plan

**Goal:** Preserve rendered content, links, and field identities through annotation and block conversion.

**Architecture:** Keep existing markers authoritative when applying model suggestions. Compare composed output before writes and refuse unsafe conversion. Keep source content untouched on rejection and report the mismatch to the operator.

**Tech Stack:** Django, BeautifulSoup, Django TestCase.

## Steps

1. Add failing tests for repeated annotation retaining existing section and field IDs, text, link attributes, and rejecting an image marker on a text wrapper.
2. Make annotation application additive for existing markers and validate field type against the element before accepting a model suggestion.
3. Add failing tests for a stored stale override collision and explicit mismatch rejection before saving annotated HTML; implement the comparison in the template save path and wire annotation callers.
4. Add failing tests for flat to block conversion and two repeat runs preserving composed DOM and records; enforce the existing conversion preview and remove the convert despite diff path.
5. Run targeted tests, full Django suite, build and CI checks. Record local and unavailable staging/installed-account evidence on CMS-72. Open a separate PR for CMS-72.

## Edge cases

- Existing blank link overrides are meaningful and must remain visible to comparison.
- New fields may be added, but old field IDs and their DOM owners must remain stable.
- A failed conversion must leave template HTML, content, and block records unchanged.
- Existing flat pages may retain old block records; inspect production read-only before proposing cleanup.

## Flat workaround investigation and repair plan

The repository's bundled SQLite database has four tenants and no `kieran-haughey` row. It cannot establish whether Nik's production flat v5 left block instances or whether that client's visual editor works. No production or staging connection is configured in this worktree.

On a read-only replica or authorized copy, locate the tenant by `subdomain`, then inspect its current template HTML and version history, `content.regions.main`, `content._classic`, and `BlockType` rows scoped to the template. Check each inner page separately. Compare the flat template's `data-edit` IDs against stored classic and region fields, and open both flat and block editor views in the authorized copy. Record whether block records are referenced by any current `regions` instance before classifying them as orphaned.

If blocks are orphaned, take a versioned copy of the template, block rows, and tenant/page content first. Reconcile overrides by field ID against the flat page and verify the composed DOM and editor on the copy. Only after the PM approves a client-specific migration should the stale `regions`/block associations be removed; retain the snapshot for rollback. Do not delete block rows based solely on absence from the rendered flat page, since another page or version may still reference them.
