# CMS-9 — MCP JSON-RPC endpoint: design

**Ticket:** CMS-9 "MCP JSON-RPC endpoint"
**Status:** Spec — clears the `needs-spec` rung. No implementation code exists yet.
**Session:** Interactive spec session with the owner (Bernard), 2026-08-07 → 2026-08-08.
**Reference implementation:** Iceberg's Streamable HTTP MCP transport, branch
`feat/ice-mcp-transport` (`a6b7f7b`), contract at `docs/mcp-v1.md`. Divergences are stated
inline with a reason.

---

## 1. What this ticket delivers

A Streamable HTTP MCP endpoint at `POST /mcp` (plus `GET /mcp` for the SSE stream) on the
agency host, protected by the already-merged `CmsBearerAuth` / `resolve_access_token` path,
exposing four read-only tools over the CMS's existing tenant/page/content model.

`api/auth.py` merged in `ab4b196` but **is not mounted on any live route**. Mounting it is the
core of this ticket. Until CMS-9 ships, that code is unreachable and cannot leak anything.

---

## 2. Inherited constraints — not reopened here

These were settled before this session and are treated as fixed.

1. **Per-app authorization server.** cms_platform runs its own django-oauth-toolkit AS, not the
   shared Authentik IdP, because this surface serves the CMS's own paying customers.
   (Owner decision 2026-08-07, CMS-1.)
2. **Superuser reaches every tenant; staff reach only their `TenantMembership` rows.** A staff
   account with no memberships resolves to `None`. (Owner decision 2026-08-07, CMS-17.)
3. **`Tenant.user_can_edit` (`core/models.py:116`) and `core/permissions.py` are never touched.**
   They govern the Django dashboard and intentionally still let `is_staff` edit any tenant.
   Changing them locks every agency operator out of the live dashboard.
4. **Tokens must be issued by the configured Claude client** (`settings.CLAUDE_OAUTH_CLIENT_ID`),
   and that check fails closed when unset (`api/auth.py:76-83`).
5. **django-oauth-toolkit 3.4.0 covers RFC 9728 and RFC 8707 natively.** No custom metadata views.

### Verified live during this session

Probes against `https://sites.katek.app` on 2026-08-07:

| Probe | Result |
|---|---|
| `GET /mcp` | `301 → /mcp/` |
| `GET /mcp/` | `404` (falls into `<slug:slug>/` → `page_render` → `request.tenant is None`) |
| `GET /api/health` | `{"status":"ok"}` — ninja is mounted and live |
| `/.well-known/oauth-protected-resource` | `{"resource":"https://sites.katek.app", ...}` |
| `/.well-known/oauth-protected-resource/mcp` | `200`, `"resource":"https://sites.katek.app/mcp"` |
| `/.well-known/oauth-protected-resource/totally/made/up` | `200`, echoes the suffix back |

**Consequence:** django-oauth-toolkit echoes any path suffix into RFC 9728 metadata without
validating it. Correct protected-resource metadata for `/mcp` therefore already exists in
production and requires no code. It also means the AS will happily advertise metadata for
resources that do not exist — noted, not CMS-9's problem.

---

## 3. Decisions made in this session

All five open forks, resolved by the owner on 2026-08-07 unless noted.

### 3.1 First slice — transport plus a full read surface

**Decision (owner):** transport, auth mounting, and read-only tools: `list_sites`,
`list_pages`, `get_page`, `get_content`. No writes.

Rejected: transport-only (Iceberg parity), which would have required freezing `push_page` and
content-tool schemas before their tickets are specced, and would leave nothing in CMS-9
verifiable beyond a handshake.

**Divergence from Iceberg, stated:** Iceberg's transport ticket shipped `tools/call` returning
`-32601` for everything. CMS-9 executes its tools. The reason is that CMS's tool schemas belong
to unbuilt, unspecced tickets, so advertising them without executing them would freeze
contracts that their own tickets should design.

### 3.2 Read contract ownership — CMS-9 owns read and etag; CMS-7 narrows to writes

**Decision (owner):** CMS-9 defines the field keyspace, the read shape, and the etag algorithm.
CMS-7 is re-scoped to the write half — `patch_content`, `If-Match` enforcement, 409 semantics,
`ContentVersion` rows.

CMS-7's description currently reads "Return an etag/hash on read; require it on PATCH for
optimistic concurrency," i.e. it owned both halves. Shipping reads in CMS-9 without settling
this would have produced two designs for one keyspace. Filed as a comment on CMS-7.

### 3.3 Mount point — `/mcp` at origin root

**Decision (owner):** `/mcp`, origin root, Iceberg URL parity.

Rejected: `/api/mcp` (zero routing risk, `api` already reserved) and a dedicated
`mcp.sites.katek.app` host. The metadata argument that would have favoured `/api/mcp` does not
apply — see §2, DOT echoes any suffix — so this was a canonical-URL choice.

Two obligations follow, both acceptance criteria:

- `mcp` must be added to `RESERVED_PAGE_SLUGS` (`core/models.py:172`). That set is enforced at
  form level (`dashboard/views.py:1679`, `:1837`) and never backfilled, so it guards new pages
  only.
- A pre-flight query for `Page.objects.filter(slug="mcp")` must run against production before
  deploy. Any such page silently stops resolving the moment the route lands.

### 3.4 Tenant selection — explicit `site` argument, required on every call

**Decision (owner):** every tool takes `site` (the subdomain). The server resolves it through
`ResolvedAuth.for_tenant()`, which already returns `None` for non-members (`api/auth.py:37-43`).

Rejected: binding the tenant at grant time (needs a new model or RFC 8707 encoding, and reopens
the consent screen that merged in `06b9ba8`); a `select_tenant` tool with server-side session
state (makes the transport stateful and produces audit rows that need session state to
interpret); and host-bound selection via `acme.sites.katek.app/mcp` (mints one AS identity per
tenant host).

This keeps the transport stateless, matches Iceberg, and makes every audit row complete from
the call alone.

### 3.5 Audit — land the seam, not the model

**Decision (owner):** a single `record_mcp_call(...)` in the `tools/call` dispatcher, backed by
a Python logger. CMS-6 swaps the backend for its model and migration.

Placing it in the single dispatch chokepoint means no future tool can forget to log, which is
the failure mode a per-tool hook invites.

> **Flagged at the owner's request:** this fork was answered outside the structured option set
> — the owner supplied the answer in prose rather than selecting against the full set of
> alternatives that had been prepared. It is recorded as a real decision, but the owner asked
> that it be marked as revisitable rather than presented as settled on the same footing as the
> other four.

### 3.6 Confirmation gates — declare annotations, and write the rule

**Decision (owner):** every CMS-9 tool emits `readOnlyHint: true`. The spec states the rule
CMS-10/11 inherit:

- Write tools declare `destructiveHint`.
- **Because MCP 2025-06-18 defines annotations as untrusted hints a client may ignore**, any
  tool that creates credentials or makes a site publicly visible must *also* carry a
  server-side gate, designed in its own ticket. A hint is not a control.

---

## 4. Prerequisite refactor — rename `ResolvedAuth.scopes` → `tenant_scopes`

**Decision (owner):** do it in CMS-9, not later.

`ResolvedAuth.scopes` holds `TenantScope` objects. It does not hold OAuth scopes. DOT already
advertises `scopes_supported: ["read","write"]`, so two different meanings of "scope" are one
commit away from coexisting in a security-critical module. The moment CMS-10/11 gate write
tools on the real OAuth `write` scope, that collision becomes expensive; today the field is not
mounted on any live route and the change is nearly free.

Blast radius, measured:

| File | Lines |
|---|---|
| `api/auth.py` | 40, 107, 120 |
| `api/tests/test_token_resolution.py` | 69, 95, 96, 112, 152, 153, 154, 177 |

`core/tests/test_ghl_oauth.py:168` uses `install.scopes` — an unrelated GHL model field.
Out of scope, untouched.

---

## 5. Architecture

New package `api/mcp/`:

| Module | Responsibility |
|---|---|
| `views.py` | HTTP transport: method routing, Origin check, protocol-version header, Accept negotiation, body cap, 401 emission |
| `dispatch.py` | JSON-RPC envelope, method table, the `record_mcp_call` audit seam |
| `tools.py` | Tool definitions, `inputSchema` / `outputSchema`, annotations, handlers |
| `content.py` | Field addressing, merge-with-defaults reads, etag computation |
| `errors.py` | JSON-RPC error codes and the `isError` result helper |

**Implemented as a plain Django view, not a django-ninja operation.** JSON-RPC multiplexes many
methods over one URL with its own error envelope; ninja's per-operation routing and `HttpError`
model fight that shape. Ninja keeps `/api/health` and any later REST surface. `CmsBearerAuth`
is a ninja `HttpBearer`, so the transport calls `resolve_access_token` directly rather than
going through the ninja auth wrapper.

### URLconf placement

```python
# cms_platform/urls.py — adjacent to the existing api/ mount
path("api/", api.urls),
path("mcp", McpView.as_view(), name="mcp"),      # new
path("", include("api.oauth_urls")),
```

Anywhere above `path("<slug:slug>/", ...)` is correct. **No trailing slash** — an `APPEND_SLASH`
301 mid-POST is hostile to non-browser clients, the same reason `healthz` skips it
(`cms_platform/urls.py:34-36`).

`@csrf_exempt`: bearer auth, no cookies.

### Host binding — agency host only

Serve when `request.tenant is None`; otherwise 404.

Serving `/mcp` on every tenant host would give one logical resource N URIs, each advertising
*itself* as its own authorization server (DOT builds RFC 9728 metadata from the request host),
fragmenting consent and audit. It buys nothing now that `site` is an explicit required
argument. One resource, one identity.

---

## 6. Transport contract

Protocol revision **2025-06-18** — the first with `outputSchema` and `resource_link`. Iceberg
parity throughout this section.

| Concern | Behaviour |
|---|---|
| Methods | `initialize`, `notifications/initialized`, `tools/list`, `tools/call`; anything else → `-32601` |
| Notifications | JSON-RPC messages with no `id` receive `202 Accepted`, no body |
| Version header | `MCP-Protocol-Version: 2025-06-18` required on every request after initialization; absent tolerated only on `initialize`; mismatch → `400` |
| Accept | One JSON response, or one `event: message` SSE event when the client accepts `text/event-stream` and not `application/json` |
| `GET /mcp` | SSE keep-alive stream; requires the version header |
| Origin | New `MCP_ALLOWED_ORIGINS` setting, comma-separated. Absent `Origin` allowed (non-browser clients). Present and unlisted → `403`. Defaults to deny for any browser origin. |
| Body cap | 16 MiB → `413` with `-32600 request body too large` |
| Other methods | `405` with `Allow: GET, POST` |

`initialize` returns:

```json
{ "protocolVersion": "2025-06-18",
  "capabilities": { "tools": { "listChanged": false } },
  "serverInfo": { "name": "katek-sites", "version": "1.0.0" } }
```

---

## 7. Authentication and authorization

Bearer token → `resolve_access_token` → `ResolvedAuth`. No change to that function beyond the
§4 rename.

### The 401 — first-class requirement

An unauthenticated or failed request returns:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer realm="katek-sites",
  resource_metadata="https://sites.katek.app/.well-known/oauth-protected-resource/mcp"
```

**This header is how an MCP client discovers the authorization server under RFC 9728.** Without
it Claude receives a bare 401 and stops — the connector silently fails to bootstrap rather than
prompting for OAuth. `CmsBearerAuth` returning `None` produces exactly that bare 401 today, so
CMS-9 must emit the header itself. The URL it points at already returns the correct body in
production (§2).

`resource_metadata` is derived from the request host so dev and prod each advertise their own.

### Per-call authorization

```
scope = auth.for_tenant(tenant_for(site))
if scope is None:  ->  isError result (see §9)
```

No OAuth-scope gating in v1. Authorization is membership-derived. When CMS-10/11 add writes,
gating those tools on the OAuth `write` scope becomes worth doing — see also CMS-20, which is
open against the advertised grant list on the same surface.

---

## 8. Tool surface

Four tools, all read-only.

| Tool | Arguments | Result |
|---|---|---|
| `list_sites` | — | `{sites: [{subdomain, name, role, published}]}` |
| `list_pages` | `site` | `{pages: [{page, title, published, is_home}]}` |
| `get_page` | `site`, `page?` | `{fields: {"<section>.<field>": {value, is_default}}, etag}` |
| `get_content` | `site`, `page?`, `field` | `{value, is_default, etag}` |

Annotations on all four:

```json
{ "readOnlyHint": true, "destructiveHint": false,
  "idempotentHint": true, "openWorldHint": false }
```

Every successful `tools/call` places its result in **both** `content` (native MCP) and
`structuredContent` (validated by the advertised `outputSchema`) — Iceberg's contract, adopted
unchanged.

`list_sites` derives entirely from `ResolvedAuth`: for a superuser, every `Tenant`; otherwise
one row per `TenantScope`. It is the discovery mechanism for `site`, and the recovery path
named in every access-denied message.

`list_pages` exists so `get_page` has a discovery path — `Page` rows are per-tenant and a client
has no other way to learn a slug.

### Field addressing

Dotted `<section>.<field>` IDs, unchanged from the annotation DSL. `page` is omitted or `null`
for the home page: `Tenant.content` is the home page and `Page.content` are inner pages, and
`SlugField` cannot hold an empty string, so `null` cannot collide with a real page.

Drafts are visible. The principal is a member or superuser, matching editor semantics; the
public-visibility gate in `core/views.py:72` is a *public* render gate and does not apply.

### Read values are merged; provenance is explicit

Reads return effective values through `merge_with_defaults()`. CLAUDE.md is unambiguous that
nothing may compute display values that bypass it.

`is_default` reports whether the value came from the template default or from stored content.
An AI about to rewrite a site needs to distinguish the client's real copy from template
boilerplate; without the flag, "Welcome" is indistinguishable from a headline someone wrote.

---

## 9. The etag

```
sha256(json.dumps(stored, sort_keys=True, separators=(",",":"), ensure_ascii=False)
       .encode("utf-8")).hexdigest()
```

computed over the **stored** blob — `Tenant.content` or `Page.content` — **not** over
`merge_with_defaults()` output.

**Why stored, not merged.** CMS-7's PATCH mutates the stored blob. If the etag covered merged
output, editing a Template's defaults would invalidate every outstanding etag without anyone
touching content, and PATCH would 409 for a reason the caller can neither see nor fix.

**Stated cost.** The etag answers *"has stored content changed since I read it?"* — the right
question for optimistic concurrency — but it will **not** detect a template change that altered
what actually renders. If CMS-7 needs that, it adds a separate template hash. Do not overload
the etag.

Opaque string. Callers must not parse it.

---

## 10. Error model

| Class | Shape |
|---|---|
| Malformed JSON | `-32700` parse error, `400` |
| Bad envelope, trailing JSON, bad version header | `-32600`, `400` |
| Unknown method or unknown tool name | `-32601` |
| Schema-invalid arguments | `-32602` |
| Tool execution failure, including access denial | `200` with `isError: true` |

**Access denial is a tool error, not a protocol error** — it is actionable, so Claude reads it
and calls `list_sites` rather than flailing.

**"No such site" and "site exists but you cannot reach it" return a byte-identical response.**
Otherwise the endpoint is a tenant-enumeration oracle for any valid token holder.

```json
{ "content": [{"type": "text",
               "text": "No accessible site 'acme'. Call list_sites to see available sites."}],
  "isError": true }
```

The same rule applies to `page`: an existing-but-forbidden page and a nonexistent page are
indistinguishable in the response.

**Boundary with CMS-13.** CMS-9 defines only the minimum error convention it needs to ship
`tools/call`. CMS-13 keeps the health-check tool and the richer retry-guidance taxonomy. Filed
as a comment on CMS-13.

---

## 11. Audit seam

```python
# api/mcp/dispatch.py
scope = auth.for_tenant(site)
record_mcp_call(actor=auth.user, tenant=scope.tenant,
                tool=name, performed_via="MCP")
return TOOLS[name](scope, **arguments)
```

`record_mcp_call` writes to a Python logger in CMS-9. CMS-6 replaces the backend with its
`actor / tenant / action / performed_via / timestamp` model and migration; the call site does
not move.

No model, no migration, no schema commitment in CMS-9. Arguments are summarised, never logged
verbatim, so a future write tool cannot leak field content or credentials through the log.

---

## 12. Deployment prerequisites

**`CLAUDE_OAUTH_CLIENT_ID` must be set in the production Dokploy environment before this
deploys.** With it unset or blank, `_issued_by_claude_client` (`api/auth.py:76-83`) denies every
token by design — the endpoint will be live and reject 100% of traffic, which reads as a broken
connector rather than a configuration gap. Carried forward from CMS-16's merge comment.

Also required before or at deploy:

| Item | Why |
|---|---|
| `MCP_ALLOWED_ORIGINS` set (or deliberately empty) | Empty means deny all browser origins; that is the safe default, but it should be a decision, not an oversight |
| Pre-flight `Page.objects.filter(slug="mcp")` on prod | Any hit silently stops resolving once the route lands (§3.3) |
| Tests run on Python 3.12 / Django 5.1.2 | The shared dev venv has drifted to Python 3.14 / Django 5.2.15 and does not exercise the pinned stack — see CMS-15. A 3.14 run produces ~99 spurious `AttributeError: 'super' object has no attribute 'dicts'` from Django's own test client |

---

## 13. Acceptance criteria

Each line is a test unless marked otherwise.

**Transport**

1. `POST /mcp` with a valid `initialize` returns protocol version `2025-06-18`, `serverInfo`, and `capabilities.tools`.
2. `notifications/initialized` returns `202` with an empty body.
3. A request after initialization without `MCP-Protocol-Version` returns `400`.
4. An unsupported `MCP-Protocol-Version` returns `400`.
5. `GET /mcp` returns an SSE stream with `Content-Type: text/event-stream`.
6. A client accepting only `text/event-stream` receives one `event: message` frame.
7. A request with an `Origin` absent from `MCP_ALLOWED_ORIGINS` returns `403`; a request with no `Origin` header succeeds.
8. A body over 16 MiB returns `413` with `-32600`.
9. `PUT /mcp` returns `405` with `Allow: GET, POST`.
10. `POST /mcp` on a tenant host returns `404`; the same request on the agency host succeeds.
11. `POST /mcp` (no trailing slash) is not redirected — asserts no `APPEND_SLASH` 301.

**Authentication — first-class**

12. **An unauthenticated `POST /mcp` returns `401` whose `WWW-Authenticate` header contains both `realm="katek-sites"` and a `resource_metadata=` parameter.** Assert both are present, explicitly and separately. A bare 401 is the difference between a connector that bootstraps and one that silently fails to.
13. `resource_metadata` resolves to `/.well-known/oauth-protected-resource/mcp` on the request host.
14. A token from an application other than `CLAUDE_OAUTH_CLIENT_ID` returns `401`.
15. With `CLAUDE_OAUTH_CLIENT_ID` unset, every token returns `401` — fails closed.
16. An expired, revoked, or inactive-user token returns `401`.

**Enumeration oracle — first-class**

17. **A principal with no access to site X receives a byte-identical response body and status for (a) a site that exists but is forbidden and (b) a site that does not exist at all.** Assert equality of the full serialised response, not of a substring. This property is easy to break with a well-meaning "better error message" commit and nobody will notice without the test.
18. The same byte-identical property holds for `page`: existing-but-forbidden versus nonexistent.

**Authorization**

19. A staff user with no `TenantMembership` gets an empty `list_sites` and is denied every site.
20. A member of tenant A can read A and is denied B.
21. A superuser can read any tenant, including one with no membership row.

**Tools**

22. `tools/list` returns exactly four tools, each with `inputSchema`, `outputSchema`, and `readOnlyHint: true`.
23. Every successful `tools/call` returns the result in both `content` and `structuredContent`, and `structuredContent` validates against the advertised `outputSchema`.
24. `get_page` with `page` omitted reads `Tenant.content`; with a slug, the matching `Page`.
25. `get_content` returns `is_default: true` for a field never edited and `false` once stored.
26. Reads return merged values — a field absent from stored content returns the template default, not null.
27. Two reads of unchanged content return equal etags; a content change produces a different etag.
28. Changing a Template's defaults does **not** change the etag — asserts the stored-not-merged rule.
29. An unknown tool name returns `-32601`; schema-invalid arguments return `-32602`.
30. A draft page is readable by a member.

**Prerequisite refactor**

31. `ResolvedAuth.tenant_scopes` is the field name; no reference to `ResolvedAuth.scopes` remains in `api/`.

**Non-test acceptance**

32. `mcp` is present in `RESERVED_PAGE_SLUGS`, and creating a page with that slug is refused by the dashboard form.
33. Full suite green on Python 3.12 / Django 5.1.2.
34. `makemigrations --check --dry-run` reports no changes — CMS-9 adds no models.

---

## 14. Out of scope

| Not in CMS-9 | Owner |
|---|---|
| `patch_content`, `If-Match`, 409 semantics, `ContentVersion` writes | CMS-7 (re-scoped) |
| Audit model and migration | CMS-6 |
| Health-check tool, retry-guidance taxonomy | CMS-13 |
| `push_page` | CMS-10 |
| `create_client_account`, one-time secret return | CMS-11 + CMS-8 |
| Media tools — Claude calls Iceberg's MCP directly | CMS-12 |
| Restricting advertised OAuth grants | CMS-20 |
| Tenant picker on the consent screen | Unfiled; needed before client self-service |

### Plane changes this session files

- **CMS-7** — comment narrowing it to the write half; CMS-9 owns read and etag.
- **CMS-13** — comment recording the error-shape boundary.
- **CMS-9** — summary comment linking this spec's PR.

---

## 15. Open items and risks

1. **The audit fork is revisitable** (§3.5) — recorded at the owner's request.
2. **No tenant picker at consent.** A token still grants every membership the user holds. Fine
   while the surface is operator-driven; needs resolving before client self-service. Not filed
   as a ticket yet — raised by the PM on CMS-4 and still open.
3. **`for_tenant` returns a synthetic `superadmin` role** that is not in
   `TenantMembership.ROLE_CHOICES`. `list_sites` will surface it. Callers switching on role must
   handle it. Carried from the CMS-4/5 review; not fixed here.
4. **`MCP_ALLOWED_ORIGINS` empty means deny-all-browsers.** Correct default, but it will look
   like a bug the first time someone tries a browser-based MCP client.
5. **CMS-20 is open on this same surface** — the AS advertises `implicit` and `password` grants.
   Not CMS-9's to fix, but it lands on the endpoint CMS-9 exposes.
