# CMS-84 implementation plan

1. Add synthetic failing-first tests for auto-to-stable and auto-to-renumbered moves, unmappable elements, conflicting destinations, tenant/page content versions, and rollback. Run targeted red test under the shared build lock.
2. Add a narrow element-path mapping helper that uses the renderer's automatic annotation routine to identify old and candidate slots. Migrate only stored automatic overrides, then run the existing rendered-output comparison using the migrated content.
3. Lock affected tenant/page rows, write migrated content through the versioned save path inside the template transaction, and keep specific rejection errors for unresolved IDs.
4. Resolve the owner's separate template-defaults mismatch from an owner-provided sanitized before/after example. Do not bypass the guard or claim this migration fixes that case.
5. Run targeted tests, full required Django suite, and a controlled synthetic staging browser save/reload check under the shared build lock. Push a small PR and wait for hosted CI. Production browser and deploy remain unrun for PM review.
