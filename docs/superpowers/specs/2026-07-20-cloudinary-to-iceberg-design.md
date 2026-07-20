# Cloudinary → Iceberg (CMS media) — Design

**Date:** 2026-07-20
**Status:** Approved (design), pending spec review

## Problem

Client image upload in the CMS editor doesn't work. The current media backend
is Cloudinary (server-routed images, signed browser-direct video). We are
replacing Cloudinary entirely with **Iceberg** (the agency media host,
delivered from `https://cdn.katalyst-crm.com`). This covers both **new uploads**
and **migrating existing Cloudinary assets** already referenced in site content.

## Decisions (locked)

- **Backend:** Full replace. Iceberg is the only media backend; no Cloudinary
  fallback.
- **Scope:** Images **and** video.
- **Upload path:** Both server-proxied. Browser → Django → R2 (via Iceberg
  presigned PUT). No browser-direct-to-R2 (the shared `katalyst-iceberg` bucket
  only allows browser PUT from `dashboard.katalyst-crm.com`; GET/HEAD are open
  to `*`, so CDN delivery works everywhere). Server-side PUT needs no CORS and
  works uniformly across all tenant + custom domains.
- **Existing assets:** Migrate all. Existing `res.cloudinary.com` URLs in tenant
  content are re-hosted on Iceberg and rewritten.
- **Deploy:** The CMS runs on **Dokploy** (not Railway). Env vars are set in the
  Dokploy service; the migration command runs there against prod.
- **Migration run:** After deploy, Claude runs it via Dokploy — dry-run first,
  review report, then `--apply`.

## Iceberg facts (verified against the live service)

- API origin (from `~/.iceberg/config.json`):
  `https://api-production-2bad.up.railway.app` (note: differs from the
  `dashboard.katalyst-crm.com` shown in `USING_ICEBERG.md`; use the config value
  via env var).
- Token: `kic_…` bearer, tenant `t1`, role `tenant_admin`. **Secret** — env var
  only, never committed.
- Upload is a two-step server flow:
  1. `POST /assets/init-upload` `{key, content_type}` → returns
     `{asset_id, key, tenant_id, upload_url, content_type, expires_at}`.
     `upload_url` is a presigned R2 PUT URL (1h expiry).
  2. `PUT <upload_url>` with the raw bytes and `Content-Type`.
  3. `POST /assets/complete` `{key}` to finalize.
- Public delivery URL: `https://cdn.katalyst-crm.com/<tenant>/<key>`
  (e.g. `https://cdn.katalyst-crm.com/t1/cms/tenants/acme/image/uuid-hero.png`).
- Verified end-to-end: upload → served (HTTP 200) → delete all work with the
  local token. A server-side PUT with an `Origin` header returns **no**
  `Access-Control-Allow-Origin`, confirming browser-direct is not viable.

## Architecture

### New: `core/services/iceberg_media.py`

Replaces `core/services/cloudinary_media.py`. Same public surface so callers
barely change.

- `is_configured() -> bool` — all Iceberg env vars present.
- `validate_image(upload) -> (ok, error)` — **kept verbatim** from
  `cloudinary_media` (Pillow content-sniff + `MEDIA_MAX_IMAGE_BYTES` +
  `MEDIA_ALLOWED_IMAGE_FORMATS`).
- `upload_image(upload, tenant) -> dict` — server does init-upload → PUT bytes
  → complete. Returns `{public_id (=key), secure_url (=CDN url), delivery_url,
  bytes}`.
- `upload_video(upload, tenant) -> (dict|None, error)` — enforces
  `MEDIA_MAX_VIDEO_BYTES`; streams bytes from the uploaded file (Django writes
  >2.5MB uploads to an on-disk `TemporaryUploadedFile`, so we stream from disk
  and never load the whole video in memory) straight to the presigned PUT.
  Returns the same dict shape. (Duration cap `MEDIA_MAX_VIDEO_DURATION` is not
  enforceable without a media probe; the byte cap is the guard. Documented as a
  known change from the Cloudinary flow.)
- Internal helpers: `_configure()` (read env), `_init_upload(key, ct)`,
  `_put_bytes(url, fileobj, ct)`, `_complete(key)`, `_cdn_url(key)`,
  `_key(tenant, kind, filename)`.

**Key scheme:** `cms/tenants/<subdomain>/<kind>/<uuid8>-<safe-name>.<ext>`
where `kind` ∈ {`image`, `video`}. Stable, collision-free, human-legible.

**HTTP client:** stdlib `urllib.request` — **no new dependency** (CLAUDE.md
forbids casual deps; the stack is intentionally Django + BeautifulSoup + lxml +
Pillow). The three Iceberg calls (JSON POST, binary PUT, JSON POST) are simple
enough for `urllib`. Streaming the PUT body from the temp file uses a file-like
`data=` with an explicit `Content-Length`.

### Changed: `dashboard/views.py`

- `_save_upload(request, tenant)` — swap `cloudinary_media` → `iceberg_media`;
  store CDN URL in `MediaAsset.secure_url`, key in `MediaAsset.public_id`. JSON
  response unchanged: `{ok, url, id}`.
- **Video collapses to one step.** Remove `_video_sign` and `_video_confirm`.
  Add a `_save_video_upload(request, tenant)` handler mirroring `_save_upload`:
  browser POSTs the file → validate type + size → `iceberg_media.upload_video`
  → return `{ok, url, id}`.
- Remove `videoSignUrl` / `videoConfirmUrl` from the `window.CMS` context dict
  in `_render_editor`; add nothing (video reuses `uploadUrl` pattern with its
  own endpoint, `videoUploadUrl`).

### Changed: `dashboard/urls.py`

- Remove: `tenant_video_sign`, `tenant_video_confirm`,
  `tenant_video_sign_self`, `tenant_video_confirm_self`.
- Add: `tenant_video_upload` (agency) + `tenant_video_upload_self` (tenant),
  pointing at the new video handler.

### Changed: `static/js/editor.js`

- Image branch: no change.
- Video branch (~lines 632–719): replace the XHR-to-Cloudinary two-step with a
  single `fetch(window.CMS.videoUploadUrl)` POST (FormData with `file`), keeping
  an `XMLHttpRequest` + `upload.onprogress` variant for the progress bar since
  videos are large. On success: set src, `setValue`, `pushToPreview`,
  `scheduleSave` — same as today.
- Cache-bust the editor.js/editor.css query string (existing pattern) so
  browsers pick up the change after deploy.

### Model

No schema change. `MediaAsset.secure_url` = CDN URL, `.public_id` = Iceberg key,
`.resource_type` = image|video. The legacy `file` field and `url` property are
untouched (old local `/media/` assets still resolve).

### Config: `cms_platform/settings.py`

Replace the `CLOUDINARY_*` block with:

```python
ICEBERG_API_URL = os.environ.get("ICEBERG_API_URL", "")
ICEBERG_TOKEN   = os.environ.get("ICEBERG_TOKEN", "")
ICEBERG_CDN     = os.environ.get("ICEBERG_CDN", "https://cdn.katalyst-crm.com")
ICEBERG_TENANT  = os.environ.get("ICEBERG_TENANT", "t1")
```

Keep `MEDIA_ALLOWED_IMAGE_FORMATS`, `MEDIA_MAX_IMAGE_BYTES`,
`MEDIA_MAX_VIDEO_BYTES`, `MEDIA_MAX_VIDEO_DURATION`. Update `.env.example` with
the new keys (no values). Set real values in the **Dokploy** service env.

## Migration: `manage.py migrate_cloudinary_to_iceberg`

Django management command. **Dry-run by default; `--apply` to commit.**

1. **Discover:** scan every `Tenant.content` JSON and `MediaAsset.secure_url`
   for `https?://res.cloudinary.com/...` URLs. Collect the distinct set.
2. **Re-host (no Cloudinary creds needed — URLs are public):** for each distinct
   URL, HTTP GET the bytes, derive `public_id` + `ext` from the path, upload to
   Iceberg under key `cloudinary/<public_id>.<ext>` →
   `https://cdn.katalyst-crm.com/t1/cloudinary/<public_id>.<ext>`.
3. **Rewrite:** replace old → new URL in each `Tenant.content` and matching
   `MediaAsset` rows. `transaction.atomic()` per tenant.
4. **Idempotent:** a URL already pointing at `cdn.katalyst-crm.com` is skipped;
   re-runs are safe. If an Iceberg key already exists, reuse it (don't re-upload).
5. **Report:** print found / downloaded / uploaded / rewritten / skipped /
   failed, with per-URL detail on failures. `log()` (not silent) any URL it
   could not fetch (dead Cloudinary link) — do not silently drop.

Flags: `--apply`, `--tenant <subdomain>` (limit to one), `--limit N` (cap for a
test batch).

## Testing (TDD — written before implementation)

- `core/tests/test_iceberg_media.py` — `upload_image` / `upload_video` with the
  HTTP calls mocked (assert init → PUT → complete sequence, key scheme, returned
  URL); `validate_image` reject paths; `is_configured` gating.
- `dashboard` upload view tests — image happy path, video happy path, validation
  failure (bad type / too big), not-configured → clean error JSON. Assert
  `MediaAsset` row created with CDN URL.
- Migration command test — mocked download + mocked Iceberg upload; asserts
  discovery, rewrite in content, idempotent re-run, dry-run makes no writes.

Follow repo tooling: tests first, then implementation, strict linting, and the
local-only dev-tooling convention (CLAUDE.md, pre-commit, ruff/pytest config
stay in `.git/info/exclude`).

## Rollout

1. Confirm Dokploy access to the CMS service (prerequisite).
2. Set `ICEBERG_*` env vars in Dokploy.
3. Deploy code (tests green, lint clean).
4. Smoke-test a fresh image + video upload on prod.
5. Run `migrate_cloudinary_to_iceberg` (dry-run) via Dokploy → review report.
6. Run with `--apply` → spot-check rewritten sites render from
   `cdn.katalyst-crm.com`.

## Non-goals / notes

- Not enabling browser-direct-to-R2 (would require broad PUT CORS on the shared
  bucket across arbitrary custom domains — rejected).
- Not adding per-CMS-tenant isolation at the Iceberg level; all CMS media lives
  under Iceberg tenant `t1`, namespaced by CMS subdomain in the key.
- Video duration cap is dropped (no server-side probe); byte cap remains.
- Iceberg CDN edge-caches R2 objects ~1yr; since keys are unique per upload,
  stale-cache is not a concern for new uploads. Migrated keys are new too.
