# CMS-95: layout-aware classic-to-block conversion

## Problem and goal

The current splitter chooses the parent with the most root sections, removes roots under other parents, and puts one `main` slot at the first root. On the Lisa Oates home, twelve single-section wrapper divs share `<main>` with an interleaved fixed div. Flattening those roots loses their wrappers and sibling order. The read-only production baseline is 47 classic homes: 6 equal renders, 11 structural mismatches, and 30 with no extractable blocks. Preserve each eligible page's designed DOM order and keep the existing render-equality refusal gate. CMS-93 head-space work is separate.

## Chosen model

Add `Template.block_layout` as a versioned JSON field (Django schema migration, default `{}`). Version 2 records an ordered sequence of `{kind: "slot", id, mode, container_path}` and `{kind: "fixed", id}` descriptors. `container_path` is the slot parent's element path (tag and element-sibling index from the document root); it locks each slot to the wrapper or region where conversion found it. The template's `html_source` remains the source of fixed markup; fixed segments are never copied into tenant content or offered as movable blocks. `Tenant.content` and `Page.content` keep the existing `regions` map but gain one stable key per body slot, for example `body-001`, `body-002`; each value is an ordered instance list. The converter assigns IDs once and stores the sequence. It rejects duplicate/missing markers or a content region absent from the layout. Existing converted templates have `block_layout={}` and continue using `regions.main` and the current renderer/editor path without a backfill.

Use the original wrapper as a `data-region` slot when it contains only the extractable root section run and ignorable whitespace. This keeps its tag, class, attributes, and direct-child relationship. When a parent interleaves blocks and fixed children, delimit each contiguous block run with inert comment anchors and leave fixed nodes in place; the renderer mounts instances as siblings between anchors. Never add a layout-changing container solely to make a slot. Unsupported or ambiguous structures fail closed and remain classic. Fixed descriptors represent the untouched DOM spans between slot anchors; they contain no copied HTML. The layout sequence describes the slot/fixed order for the editor; the shell DOM defines the actual nesting and fixed HTML.

## Rendering and editing

For version 2, render each declared slot exactly once, preserving wrapper attributes or comment-delimited sibling positions. Validation requires balanced empty ranges, declared slot order and fixed gaps, and every slot in its original `container_path`; it also rejects slots inside header or footer chrome. A missing path fails closed. Keep fixed siblings and any editable fields in the shell. The editor lists slots in layout order, shows fixed segments as locked separators, and lets clients edit shell fields without offering fixed nodes for remove/reorder. Insert and reorder stay within the selected slot; cross-slot moves are refused unless a later design explicitly permits them. Preview drag targets for comment ranges are temporary overlays only; public HTML has no extra element. Server-side region validation accepts only declared slot IDs and the existing allowlisted block types and per-page cap. Legacy `main` pages retain their current controls and save shape.

## Conversion and safety

The preview and apply command, automatic editor upgrade, and client HTML opt-in must use the same splitter and candidate model. For each affected home and inner page, compare classic and candidate rendered output under the current whitespace normalization. Version 2 must preserve real wrappers and fixed nodes in the comparison, strip only conversion markers, and require exact normalized equality; it does not use the legacy glue-insensitive fallback. Any mismatch, ambiguous ownership, missing block, or slot validation error refuses the entire conversion. Apply writes the template layout, block catalog, and every affected content document in one transaction, retaining `_classic` for rollback. Old content-version snapshots must either be converted on restore or rejected explicitly; they must not silently render as empty slots. No installed production template is converted by this patch.

## Verification and rollout boundary

After PM approval, start with failing synthetic fixtures for Lisa's wrapper/fixed-sibling shape, same-parent fixed interleaving, direct-child CSS, nested chrome, unsupported ambiguity, editor ordering, server validation, rollback, and legacy `regions.main`. Require a mutation that drops a wrapper or fixed node to fail the gate. Re-run the read-only 47-home survey with mutually exclusive counts: equal, structural mismatch, no extractable blocks, and errors. Target Lisa plus at least 6 of the 11 previous mismatches becoming equal, with zero false-equal cases on manual DOM/CSS spot checks; report any remaining classes rather than forcing them through. Open a PR and wait for CI. Installed conversion requires separate owner approval.

## Alternatives considered

- One flattened `main` slot is the present failure: it cannot preserve wrapper and fixed-sibling placement.
- Making each wrapper or fixed segment a movable block would put locked design structure into client content and make deletion/reordering possible.
- Adding a div slot at every section is simpler but changes direct-child selectors and can pass a normalized HTML comparison while changing the live layout.
