# CMS-73 MCP Structural Edits Implementation Plan

**Goal:** Let a tenant-scoped MCP caller make bounded block and richtext structure changes on block-backed pages.

**Architecture:** Expose explicit add, remove, move, and richtext append tools. Resolve the page under tenant scope, lock it, compare the stored content etag, validate an isolated proposed tree, run the shared GHL guard, and save through content versions. Use stable IDs from `list_blocks`; never accept raw block HTML or arbitrary content trees.

**Tech Stack:** Django, MCP JSON-RPC, BeautifulSoup, existing block renderer and content version service.

## Tasks

1. Add failing JSON-RPC tests in `api/tests/test_mcp_structure.py` for fieldless listing, repeated/nested insertion, safe paragraph/list append, move/removal, etag conflicts, cap/depth/allowlist, tenant denial, published GHL guard, undo, and classic rejection. Run each targeted test before implementing its operation.
2. Add pure tree operations and validation in `api/mcp/structure.py`. Validate all IDs before mutation, resolve top-level body and declared child slots, enforce `MAX_BLOCKS_PER_PAGE` and `MAX_BLOCK_DEPTH`, and reject retired targets and unsupported moves. Return a new content dict without mutating input.
3. Add transactional handlers in `api/mcp/tools.py` for `add_block`, `remove_block`, `move_block`, and `append_block_richtext`. Lock the editable, compare `if_match`, enforce client-editable template, run GHL validation and `save_editable_content(..., source=SOURCE_MCP)`. Register exact input/output schemas and explicit errors.
4. Document accepted operations, region/position semantics, richtext limits, etags, and undo in `docs/mcp-block-contract.md`. Keep header navigation and arbitrary HTML outside this contract.
5. Run targeted and full Django suites; run the JSON-RPC tests with an isolated PostgreSQL 17 database. Verify persisted regions, composed DOM, unique IDs, and version restore. Push through normal gates, await exact-head hosted CI, update PR and Plane evidence, then move CMS-73 to In Review.

Staging browser and real installed-account checks remain UNRUN until an authorized environment is provided. Do not merge or mark Done.
