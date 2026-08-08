# CMS-9 MCP JSON-RPC Endpoint Implementation Plan

> **For agentic workers:** Execute task-by-task with TDD. Spec contract:
> `docs/superpowers/specs/2026-08-07-cms-mcp-endpoint-design.md` (PR #8 / branch
> `docs/cms9-mcp-endpoint-spec`). Section 13 acceptance criteria are definition of done.

**Goal:** Mount Streamable HTTP MCP at `POST/GET /mcp` with four read-only tools.

**Architecture:** Plain Django view (not ninja). Package `api/mcp/` with transport,
dispatch, tools, content/etag, errors. Auth via `resolve_access_token` directly.
Audit is `McpAuditLog` via `record_mcp_call` (CMS-6 absorbed 2026-08-08).

**Tech Stack:** Django 5.1.2, Python 3.12, django-oauth-toolkit, existing models.

---

### Task 1: Rename `ResolvedAuth.scopes` → `tenant_scopes`

**Files:** `api/auth.py`, `api/tests/test_token_resolution.py`

- [ ] Update field + all references in `api/`
- [ ] Run token resolution tests
- [ ] Commit

### Task 2: MCP acceptance tests (red)

**Files:** Create `api/tests/test_mcp_endpoint.py` (+ helpers as needed)

- [ ] Cover §13 criteria 1–34 as tests (32/34 may be assertion helpers)
- [ ] Run and confirm failures
- [ ] Commit

### Task 3: Implement `api/mcp/` + wire-up

**Files:**
- Create: `api/mcp/{__init__,views,dispatch,tools,content,errors}.py`
- Modify: `cms_platform/urls.py`, `cms_platform/settings.py`, `core/models.py`

- [ ] Implement until suite green
- [ ] `collectstatic`, full suite, `makemigrations --check --dry-run`
- [ ] Commit

### Task 4: PR + Plane handoff

- [ ] Push, open PR, comment on CMS-9, move to In Review, STOP
