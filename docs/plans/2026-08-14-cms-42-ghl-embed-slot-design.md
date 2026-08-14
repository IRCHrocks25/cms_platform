# CMS-42 GHL Embed Slot Design

## Scope and decisions

Phase 1 adds one annotation field type, `data-type="ghl-embed"`, and one
allowed `data-ghl-kind`, `form`. Calendar, survey, payment-link, chat, and a
CMS-native form implementation remain out of scope.

Embed values are self-describing strings in the form `form:<id>`. This is the
owner-approved contract for parser defaults, saved content, renderer input,
editor changes, and MCP writes.

## Considered approaches

### Shared server-side GHL service with scoped adapters (chosen)

A small service resolves a tenant's connected location install, refreshes its
token when required, and lists forms for that location. Protected dashboard
views and MCP handlers authorize their caller and resolve the tenant before
calling this service. This keeps OAuth tokens server-side and gives every
surface the same connection, revocation, response-normalization, and tenant
isolation behavior.

### Browser-direct GHL calls

The editor could call GHL directly, but that would expose a location OAuth
token to browser JavaScript and make tenant isolation depend on client code.
This is rejected.

### Fetch forms while rendering the editor page

The server could place the forms list directly in the editor HTML. This avoids
a new JSON route, but a slow or unavailable GHL API would delay or break the
entire editor. This is rejected in favor of an asynchronous picker with local
loading, empty, disconnected, revoked, and retry states.

## Architecture

`core/parser.py` recognizes `ghl-embed`, requires `data-ghl-kind`, and rejects
anything except `form` with a parse-time exception. The schema field records
`ghl_kind: "form"`; its default is an empty string or a valid `form:<id>` value.

`core/renderer.py` replaces a populated form slot with the allowlisted GHL form
iframe at `https://msgsndr.com/widget/form/<id>` and includes GHL's
`form_embed.js` auto-resize script. It never interpolates an arbitrary URL.
Empty or malformed values render no embed on public pages. Preview rendering
shows the real form while an overlay prevents pointer/keyboard submission and
announces, “This is a preview, nothing is sent.” Live picker changes reload the
server-rendered preview so the iframe behavior does not need a second,
client-only renderer.

`core/ghl_oauth.py` owns the raw `GET /forms/` request and adds
`forms.readonly` to `DEFAULT_SCOPES`. `core/services/ghl_connect.py` gains the
location-token refresh path, while a focused forms service maps a tenant to its
connected `GhlInstall`, calls the API with the tenant's `ghl_location_id`, and
normalizes form rows. Missing, disconnected, expired/revoked, or failed calls
return typed operator-facing errors without leaking tokens or another
location's data.

Dashboard routes exist in both authorization shapes already used by the
editor: tenant-host members can list only `request.tenant` forms, and agency
operators can list forms only for the tenant identified by the protected
agency route. The field component asynchronously fills a native select and
keeps the existing autosave model. Published pages with an empty embed slot,
or a selected form no longer returned by GHL, receive a visible warning in the
shared editor used by both dashboards. Unsetting a populated embed slot asks
for explicit confirmation; the server also refuses an empty embed write on a
published page so a crafted dashboard request cannot bypass the warning.

MCP adds dedicated tools to list embed slots, set an embed slot, and enumerate
available GHL forms. These tools reuse tenant authorization and the same
validation/service functions as the dashboard. `set_embed_slot` accepts only
an existing `ghl-embed`/`form` field, requires `form:<id>`, verifies that the ID
belongs to the target tenant's current form list, preserves the content-etag
concurrency guard, and refuses an empty value on a published page.

## Security and CSP

Tests prove that a member of tenant A cannot enumerate tenant B's forms through
either dashboard or MCP, and that every upstream request carries tenant A's
bound location ID rather than caller-controlled input. Form IDs are parsed as
opaque IDs, not URLs, before the renderer constructs a fixed-host iframe.

The response CSP adds a `frame-src` allowlist for `https://msgsndr.com` and
`https://*.leadconnectorhq.com` (including the script/API host required by the
official embed) while leaving the existing configurable `frame-ancestors`
directive unchanged. Tests assert both directives independently so allowing a
child frame cannot weaken who may frame Katek Sites.

## User-facing states

The picker has explicit loading, ready, empty, disconnected/re-consent,
network-error, and stale-selection states. State copy names the recovery:
connect/reconnect GHL, add forms through the tenant's GHL snapshot/account, or
retry loading. Dynamic status is announced with an `aria-live` region; long
form names wrap or truncate without changing the control width.

## Test strategy

Tests start red at each boundary: parser/kind/value validation, public and
preview rendering, OAuth request shape and token refresh, dashboard endpoint
authorization and tenant isolation, save/publish protection, editor markup,
MCP discovery/list/set behavior and cross-tenant denials, and independent CSP
directives. Focused suites run after each minimal implementation, followed by
the complete Django suite and committed-asset checks.
