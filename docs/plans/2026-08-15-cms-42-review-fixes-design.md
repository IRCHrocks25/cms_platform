# CMS-42 Review Fixes Design

## Context

PR #34 introduced phase-one GoHighLevel form embed slots. Independent review
found that a non-empty form value authored directly into a template becomes a
schema default, bypassing the tenant-aware content write paths. The same review
found that the new global `frame-src` directive can break existing third-party
iframes and that preview submission prevention needs a structural browser
control plus a visible shield.

## Template boundary and legacy data

GHL embed selections are tenant content, never template defaults. The parser
continues to validate the `form:<id>` value shape, then rejects every non-empty
`ghl-embed` default with a clear error. This applies uniformly to MCP
`push_page`, dashboard template writes, imports, and template-version restores
because all of them eventually build the schema.

`push_page` maps parser failures to a normal MCP tool error so malformed HTML
does not become a transport error. Same-tenant and cross-tenant-looking form
IDs receive the same rejection because template parsing has no tenant context.

Existing database rows may predate the parser rule. Default merging therefore
forces every `ghl-embed` schema default to the empty value before stored tenant
content is applied. Explicit, tenant-validated content still wins. Rendering
also treats a missing embed content value as empty, ensuring raw text baked into
legacy template HTML cannot become a live iframe. Public rendering removes that
slot; preview rendering shows the empty state.

## CSP compatibility

The middleware returns to emitting only its existing `frame-ancestors`
directive. Because this policy has no `default-src`, omitting `frame-src`
permits GHL frames and preserves the pre-CMS-42 behavior for existing YouTube,
Maps, Calendly, Vimeo, and other template-authored iframes. Tests prove both
that `frame-ancestors` remains constrained and that no new child-frame
restriction is introduced.

## Preview submission shield

Preview form iframes receive `sandbox="allow-scripts"`; omission of
`allow-forms` makes native form submission structurally unavailable. The
existing `inert`, negative tabindex, and pointer-event controls remain as
defense in depth.

The embed slot becomes a positioned preview container with a full-slot overlay
above the real form. Preview-injected CSS gives the overlay an opaque,
high-contrast notice and intercepts pointer input across the complete form
area. Public renders receive neither the sandbox nor preview note/overlay.

## Tests

Tests are added before implementation for:

- parser rejection of every populated embed default;
- MCP `push_page` rejection for values resembling both the current tenant's
  and another tenant's form IDs, with no template mutation;
- fail-closed rendering of legacy populated defaults;
- refusal to restore an archived template version with a populated default;
- `_current_schema` protection in both `list_embed_slots` and
  `set_embed_slot`;
- populated public output omitting every preview-only control;
- CSP retaining `frame-ancestors` without adding `frame-src`; and
- preview sandbox and full-slot shield markup/style.

The two small reviewer-noted robustness fixes also ride along:
`GhlFormsUnavailable` retains normal exception identity/hash behavior, and
`set_embed_slot` handles an absent template defensively.
