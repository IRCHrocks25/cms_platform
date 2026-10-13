# CMS-71 MCP Blocks Implementation Plan

**Goal:** Expose block-instance fields through Sites MCP and provide a safe instance HTML edit contract.

**Architecture:** Keep shell fields in the existing flat schema and enumerate active allowlisted block schemas recursively from the stored region tree. Address a field as `<instance-id>.<local-field>`. Add instance-scoped HTML read/write that changes only text or rich-text values while rejecting structural differences. Reject `push_page` for existing block-backed pages before template cloning or writes.

**Tech Stack:** Django, BeautifulSoup, MCP JSON-RPC, Django TestCase.

## Tasks

1. Add JSON-RPC regression tests for shell and repeated/nested instance fields, read/write, stale etags, permission boundary, form protection, HTML edits, `push_page` rejection, and classic pages. Run them red.
2. Add recursive field lookup and instance write helpers in `api/mcp/content.py`; use live allowlisted block schemas and preserve existing shell behavior.
3. Lock the editable row for MCP content writes, verify `if_match` inside the transaction, and save through content versions. Validate GHL embed changes through the existing shared service.
4. Add `get_block_html` and `push_block_html`. Require exact instance ID and current content etag; accept text/richtext changes only when all noneditable markup and attributes match the current rendered fragment. Save through the same content write path.
5. Reject whole-page `push_page` for existing block-backed pages before template ownership or version changes. Document tool descriptions and contract.
6. Run targeted and full Django suites, local build, and hosted PR CI; report unrun deployment checks.

## PM review follow-up

7. Reproduce the fieldless-layout discovery gap through MCP JSON-RPC and the tool manifest before changing the handler.
8. Add read-only `list_blocks(site, page?)` with preorder instance IDs, type, parent ID, region, zero-based position, depth, and allowlist availability. Include fieldless and nested instances, preserve classic-page behavior, and reject ambiguous trees.
9. Run targeted SQLite and Postgres JSON-RPC tests, the full Django suite, and exact-head hosted CI. Keep structural connector mutation in CMS-73.
