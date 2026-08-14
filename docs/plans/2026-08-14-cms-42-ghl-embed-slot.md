# CMS-42 GHL Embed Slot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a tenant-safe `ghl-embed` form slot that renders GHL forms, is editable through both dashboard modes, and is fully operable through MCP.

**Architecture:** Extend the annotation schema and renderer with an allowlisted `form:<id>` contract. Put GHL HTTP/token behavior behind a shared tenant-scoped service, then expose it through protected dashboard and MCP adapters so authorization and form validation cannot drift.

**Tech Stack:** Python 3.12, Django 5.1, BeautifulSoup/lxml, httpx, server-rendered Django templates, vanilla JavaScript, Django TestCase.

---

### Task 1: Parser and renderer contract

**Files:**
- Modify: `core/parser.py`
- Modify: `core/renderer.py`
- Create: `core/tests/test_ghl_embed_slots.py`

1. Write parser tests proving `ghl-embed` fields retain `ghl_kind="form"`,
   defaults must be empty, and populated defaults plus missing/unknown kinds
   raise at parse time.
2. Run `uv run python manage.py test core.tests.test_ghl_embed_slots -v 2` and
   verify the tests fail because the field type/kind is unsupported.
3. Add `ghl-embed` to the field types, parse `data-ghl-kind`, and validate the
   approved prefix contract.
4. Write renderer tests for populated public output, empty public output,
   malformed values, escaping/opaque ID validation, one auto-resize script,
   and the non-interactive preview notice.
5. Run the focused tests and verify renderer cases fail.
6. Implement the minimal allowlisted form renderer and structural preview
   submission shield (`sandbox="allow-scripts"` without `allow-forms`, plus a
   full-slot overlay), including bridge support for preview reload after a
   picker change.
7. Run the focused tests until green, then refactor shared value parsing into
   one helper used by renderer and write paths.
8. Commit the parser/renderer slice.

### Task 2: Tenant-scoped GHL forms service

**Files:**
- Modify: `core/ghl_oauth.py`
- Modify: `core/services/ghl_connect.py`
- Create: `core/services/ghl_forms.py`
- Create: `core/tests/test_ghl_forms.py`
- Modify: `core/tests/test_ghl_oauth.py`
- Modify: `core/tests/test_ghl_connect.py`

1. Write failing tests for `forms.readonly`, the exact `GET /forms/` headers
   and `locationId`, response normalization, valid-token reuse, refresh, and
   missing/disconnected/revoked installs.
2. Add `forms.readonly`, the raw API client function, location-token refresh,
   and typed tenant-facing form-list errors.
3. Prove the service selects the install whose `tenant` and `location_id`
   match the requested tenant and never accepts a caller-provided location.
4. Run the focused GHL suites until green and refactor error mapping without
   exposing token or upstream response bodies.
5. Commit the service slice.

### Task 3: Dashboard picker, warnings, and protected routes

**Files:**
- Modify: `dashboard/urls.py`
- Modify: `dashboard/views.py`
- Modify: `templates/dashboard/components/field.html`
- Modify: `templates/dashboard/editor.html`
- Modify: `static/js/editor.js`
- Modify: `static/css/editor.css`
- Create: `core/tests/test_ghl_embed_dashboard.py`

1. Write failing route tests for tenant-member access, agency access, anonymous
   denial, cross-tenant indistinguishability, and upstream location isolation.
2. Add tenant-host and agency-host JSON list routes that authorize first and
   call the shared service second.
3. Write failing editor tests for the native picker and loading/empty/error/
   stale states, plus published-empty warnings in both dashboard modes.
4. Add the `ghl-embed` field component, async form loading/retry, accessible
   state messaging, `form:<id>` autosave, explicit unset confirmation, and a
   preview reload on selection.
5. Write failing save tests proving crafted requests cannot clear a populated
   embed on published home/pages and cannot store a form belonging to another
   tenant.
6. Add shared server-side embed-write validation to the dashboard save path.
7. Run dashboard and renderer suites until green, then run the Impeccable
   detector once over the changed UI files and address in-scope findings.
8. Commit the dashboard slice.

### Task 4: MCP embed tools

**Files:**
- Modify: `api/mcp/tools.py`
- Modify: `api/mcp/dispatch.py`
- Create: `api/tests/test_mcp_ghl_embed.py`

1. Write failing tests for tool discovery and output schemas for
   `list_embed_slots`, `set_embed_slot`, and `list_ghl_forms`.
2. Add failing call tests for home/page slot listing, successful set with a
   current content etag, field/kind/prefix validation, published unset refusal,
   stale/deleted form refusal, disconnected/revoked messaging, and cross-tenant
   form enumeration/write denials.
3. Implement the three handlers using existing auth resolution, content
   helpers/version history, and the shared GHL forms service.
4. Ensure dispatch auditing stamps a tenant only after authorization and never
   stores arguments/form IDs.
5. Run `uv run python manage.py test api.tests.test_mcp_ghl_embed -v 2` and the
   existing MCP suites until green; refactor duplicated schema-field lookup.
6. Commit the MCP slice.

### Task 5: CSP and regression verification

**Files:**
- Modify: `core/middleware.py`
- Modify: `core/tests/test_frame_ancestors.py`
- Modify: `CLAUDE.md`
- Modify: `README.md`

1. Write failing CSP tests proving `frame-ancestors` retains its current
   self/configured/wildcard behavior and no global `frame-src` restriction is
   introduced for existing template-authored iframes.
2. Preserve the existing parent-only CSP composition without replacing an
   existing response CSP.
3. Update the annotation and GHL integration documentation for `ghl-embed`,
   `data-ghl-kind="form"`, `form:<id>`, scope/re-consent, and preview behavior.
4. Run focused tests, then `uv run python manage.py test` and `npm run
   check:assets`. Run repository lint/check commands if present.
5. Inspect `git diff --check`, `git status --short`, and the complete diff for
   secrets, unrelated changes, tenant leaks, and accidental phase 2-4 work.
6. Commit final CSP/docs/refactor changes.

### Task 6: PR and Plane handoff

1. Push `feat/cms-42-ghl-embed-slot` to the personal SSH remote without
   bypassing hooks.
2. Open a PR against `main` with acceptance coverage and verification results.
3. Post the PR URL on CMS-42.
4. Move CMS-42 from In Progress to In Review and stop without merging.
