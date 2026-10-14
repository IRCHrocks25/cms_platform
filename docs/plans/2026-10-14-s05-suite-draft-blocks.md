# S05 suite draft block edits

## Contract

Extend the S03 tenant-scoped service `draft` grant to existing bounded block HTML and structural edits on unpublished Sites resources. Reuse the same annotation, content etag, undo, row-lock, and tenant guards as human MCP writes. A service principal never gains a Django user role. Public publish and domain effects stay disabled even if the credential carries those scope names.

## Testing / acceptance evidence (defined before implementation)

- JSON-RPC: service A may read and edit A's unpublished home and inner-page blocks; it cannot mutate B; `push_block_html`, add/remove/move and plain-text richtext append preserve IDs, shell and undo snapshots.
- JSON-RPC: stale etag and published home/page deny without content/version change; publish/domain tools deny even with `publish`/`domain` scopes; existing OAuth block tests still pass.
- Local: targeted SQLite, full SQLite, and isolated disposable PostgreSQL targeted tests under the shared build lock. Hosted CI must pass at the exact PR head.
- Browser staging and real installed-account/live acceptance remain UNRUN until deployed; keep KPILOT-379 open.

## Steps

1. Add the service JSON-RPC tests and observe the missing draft block-write permission fail.
2. Admit only the five existing bounded block mutation tools through the S03 draft guard. Make their editability check safe for a principal without a Django user.
3. Run targeted and full gates, inspect diff, open a default-off PR, and record exact results plus UNRUN acceptance on Plane.

## Decisions still open

The owner must decide credential recovery/lifetime, bound approval executor for public effects, and raw HTML intake policy. None is implemented here.
