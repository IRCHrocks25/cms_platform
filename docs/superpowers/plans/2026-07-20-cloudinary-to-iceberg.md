# Cloudinary → Iceberg Media Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Cloudinary with Iceberg (`cdn.katalyst-crm.com`) as the CMS media backend for both new image/video uploads and existing stored assets.

**Architecture:** A new `core/services/iceberg_media.py` talks to the Iceberg HTTP API (init-upload → PUT bytes → complete) server-side using a `kic_…` bearer token. Both image and video uploads are server-proxied (browser → Django → R2). A management command re-hosts existing public `res.cloudinary.com` URLs onto Iceberg and rewrites them in tenant content.

**Tech Stack:** Django 5.1, `httpx` (already a dependency), Pillow (image validation), Django `TestCase` (run with `python manage.py test`).

## Global Constraints

- **No new dependencies.** Use `httpx==0.27.2` (already in `requirements.txt`) for HTTP; do not add `requests`. — CLAUDE.md.
- **Secret token never committed.** `ICEBERG_TOKEN` (`kic_…`) is env-only. Add keys (no values) to `.env.example` and `docker-compose.yml`; real values live in the Dokploy panel.
- **Full replace, no Cloudinary fallback.** Remove `cloudinary` from `requirements.txt`, delete `core/services/cloudinary_media.py`.
- **Tests first (TDD), then implementation.** Strict lint clean. Run tests with `python manage.py test`.
- **Commit author** is `jezmerrr <jezmer_ramos@dlsu.edu.ph>`; **no** Claude attribution in commits. Commit directly to `main`.
- **Key scheme:** new uploads → `cms/tenants/<subdomain>/<kind>/<uuid8>-<safe-name>.<ext>`; migrated assets → `cloudinary/<public_id>.<ext>`. Delivery URL: `{ICEBERG_CDN}/{ICEBERG_TENANT}/{key}`.
- **Iceberg API shape:** `POST {API}/assets/init-upload {key, content_type}` → `{upload_url, ...}`; `PUT <upload_url>` bytes; `POST {API}/assets/complete {key}`. Auth: `Authorization: Bearer <token>`.
- **Video duration cap dropped** (no server-side probe); byte cap `MEDIA_MAX_VIDEO_BYTES` remains. All CMS media lives under Iceberg tenant `t1`.

---

### Task 1: Iceberg media service + settings

**Files:**
- Create: `core/services/iceberg_media.py`
- Modify: `cms_platform/settings.py` (replace the `CLOUDINARY_*` block ~lines 389–398)
- Test: `core/tests/test_iceberg_media.py`

**Interfaces:**
- Produces:
  - `is_configured() -> bool`
  - `validate_image(upload) -> tuple[bool, str | None]`
  - `upload_image(upload, tenant) -> dict` with keys `public_id, secure_url, delivery_url, bytes`
  - `upload_video(upload, tenant) -> tuple[dict | None, str | None]`; dict keys `public_id, secure_url, bytes`
  - `upload_bytes(data: bytes, key: str, content_type: str) -> str` (returns CDN URL)
  - `settings.ICEBERG_API_URL`, `settings.ICEBERG_TOKEN`, `settings.ICEBERG_CDN`, `settings.ICEBERG_TENANT`

- [ ] **Step 1: Add settings block**

In `cms_platform/settings.py`, replace the Cloudinary block (the three
`CLOUDINARY_*` lines) with (keep the `MEDIA_ALLOWED_IMAGE_FORMATS` /
`MEDIA_MAX_*` lines exactly as they are):

```python
# --------------------------------------------------------------------------- #
# Iceberg — client media host (images + video), served from cdn.katalyst-crm  #
# --------------------------------------------------------------------------- #
ICEBERG_API_URL = os.environ.get("ICEBERG_API_URL", "")
ICEBERG_TOKEN = os.environ.get("ICEBERG_TOKEN", "")
ICEBERG_CDN = os.environ.get("ICEBERG_CDN", "https://cdn.katalyst-crm.com")
ICEBERG_TENANT = os.environ.get("ICEBERG_TENANT", "t1")
```

- [ ] **Step 2: Write the failing tests**

Create `core/tests/test_iceberg_media.py`:

```python
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.services import iceberg_media

IB = dict(
    ICEBERG_API_URL="https://api.test",
    ICEBERG_TOKEN="kic_test",
    ICEBERG_CDN="https://cdn.test",
    ICEBERG_TENANT="t1",
)


def _tenant():
    tpl = Template.objects.create(
        name="T", html_source="<section data-section='x'></section>"
    )
    return Tenant.objects.create(name="Acme", subdomain="acme", template=tpl)


def _mock_client(put_url="https://r2.test/put?sig"):
    """Return a MagicMock standing in for httpx.Client() as a context manager."""
    client = mock.MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    post_resp = mock.MagicMock()
    post_resp.json.return_value = {"upload_url": put_url}
    post_resp.raise_for_status.return_value = None
    client.post.return_value = post_resp
    put_resp = mock.MagicMock()
    put_resp.raise_for_status.return_value = None
    client.put.return_value = put_resp
    return client


class IsConfiguredTests(TestCase):
    @override_settings(**IB)
    def test_configured_when_all_present(self):
        self.assertTrue(iceberg_media.is_configured())

    @override_settings(ICEBERG_API_URL="", ICEBERG_TOKEN="kic_x", ICEBERG_TENANT="t1")
    def test_not_configured_when_api_missing(self):
        self.assertFalse(iceberg_media.is_configured())


@override_settings(**IB)
class UploadImageTests(TestCase):
    def test_upload_image_runs_init_put_complete_and_returns_cdn_url(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("Hero Shot.PNG", b"pngbytes", content_type="image/png")
        client = _mock_client()
        with mock.patch("core.services.iceberg_media.httpx.Client", return_value=client):
            result = iceberg_media.upload_image(upload, tenant)

        # init-upload, then complete = 2 POSTs; 1 PUT
        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(client.put.call_count, 1)
        init_call = client.post.call_args_list[0]
        self.assertTrue(init_call.args[0].endswith("/assets/init-upload"))
        key = init_call.kwargs["json"]["key"]
        self.assertTrue(key.startswith("cms/tenants/acme/image/"))
        self.assertTrue(key.endswith(".png"))
        self.assertEqual(result["secure_url"], f"https://cdn.test/t1/{key}")
        self.assertEqual(result["public_id"], key)
        self.assertEqual(result["bytes"], upload.size)


@override_settings(**IB)
class UploadVideoTests(TestCase):
    def test_rejects_oversize_video(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("clip.mp4", b"x", content_type="video/mp4")
        with override_settings(MEDIA_MAX_VIDEO_BYTES=0):
            info, error = iceberg_media.upload_video(upload, tenant)
        self.assertIsNone(info)
        self.assertIn("too large", error)

    def test_rejects_non_video(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("x.txt", b"hi", content_type="text/plain")
        info, error = iceberg_media.upload_video(upload, tenant)
        self.assertIsNone(info)
        self.assertIn("not a video", error)

    def test_uploads_video_and_returns_cdn_url(self):
        tenant = _tenant()
        upload = SimpleUploadedFile("clip.mp4", b"videodata", content_type="video/mp4")
        client = _mock_client()
        with mock.patch("core.services.iceberg_media.httpx.Client", return_value=client):
            info, error = iceberg_media.upload_video(upload, tenant)
        self.assertIsNone(error)
        self.assertTrue(info["public_id"].startswith("cms/tenants/acme/video/"))
        self.assertEqual(info["secure_url"], f"https://cdn.test/t1/{info['public_id']}")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_iceberg_media -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.services.iceberg_media'`

- [ ] **Step 4: Write the implementation**

Create `core/services/iceberg_media.py`:

```python
"""
Iceberg media helpers for client uploads.

Images and videos are routed through our server, validated at the door, then
uploaded to Iceberg (init-upload -> PUT bytes -> complete) and served from
cdn.katalyst-crm.com. Bytes are streamed from the upload's .chunks() with an
explicit Content-Length so large videos never load fully into memory.
"""
from __future__ import annotations

import io
import logging
import os
import re
import uuid

import httpx
from django.conf import settings

logger = logging.getLogger("core")


def is_configured() -> bool:
    return bool(
        settings.ICEBERG_API_URL
        and settings.ICEBERG_TOKEN
        and settings.ICEBERG_TENANT
    )


# --------------------------------------------------------------------------- #
# Images — validate at the door (identical rules to the old Cloudinary path)   #
# --------------------------------------------------------------------------- #


def validate_image(upload):
    """Return (ok, error). Rejects by real content type (Pillow) + size cap."""
    max_bytes = settings.MEDIA_MAX_IMAGE_BYTES
    if upload.size and upload.size > max_bytes:
        return False, f"Image is too large (max {max_bytes // (1024 * 1024)} MB)."

    from PIL import Image

    fmt = ""
    try:
        upload.seek(0)
        with Image.open(upload) as img:
            fmt = (img.format or "").lower()
            img.verify()  # content-based check, not extension
    except Exception:
        return False, "That file isn't a valid image."
    finally:
        upload.seek(0)

    if fmt not in settings.MEDIA_ALLOWED_IMAGE_FORMATS:
        return False, (
            f"Unsupported image type '{fmt or 'unknown'}'. "
            "Use PNG, JPG, GIF, or WebP."
        )
    return True, None


# --------------------------------------------------------------------------- #
# Iceberg upload plumbing                                                       #
# --------------------------------------------------------------------------- #


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.ICEBERG_TOKEN}"}


def _key(tenant, kind: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename or "")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()[:60] or "file"
    return f"cms/tenants/{tenant.subdomain}/{kind}/{uuid.uuid4().hex[:8]}-{safe}{ext.lower()}"


def _cdn_url(key: str) -> str:
    return f"{settings.ICEBERG_CDN.rstrip('/')}/{settings.ICEBERG_TENANT}/{key}"


def _iter_chunks(fileobj):
    fileobj.seek(0)
    for chunk in iter(lambda: fileobj.read(1024 * 1024), b""):
        yield chunk


def _upload(fileobj, size: int, key: str, content_type: str) -> str:
    """init-upload -> PUT bytes -> complete. Returns the public CDN URL."""
    base = settings.ICEBERG_API_URL.rstrip("/")
    json_headers = {**_headers(), "Content-Type": "application/json"}
    with httpx.Client(timeout=300.0) as client:
        init = client.post(
            f"{base}/assets/init-upload",
            headers=json_headers,
            json={"key": key, "content_type": content_type},
        )
        init.raise_for_status()
        put_url = init.json()["upload_url"]

        put = client.put(
            put_url,
            content=_iter_chunks(fileobj),
            headers={"Content-Type": content_type, "Content-Length": str(size)},
        )
        put.raise_for_status()

        done = client.post(
            f"{base}/assets/complete",
            headers=json_headers,
            json={"key": key},
        )
        done.raise_for_status()
    return _cdn_url(key)


def upload_bytes(data: bytes, key: str, content_type: str) -> str:
    """Upload raw bytes to a fixed key (used by the migration command)."""
    return _upload(io.BytesIO(data), len(data), key, content_type)


def upload_image(upload, tenant) -> dict:
    key = _key(tenant, "image", upload.name)
    content_type = getattr(upload, "content_type", None) or "application/octet-stream"
    url = _upload(upload, upload.size, key, content_type)
    return {
        "public_id": key,
        "secure_url": url,
        "delivery_url": url,
        "bytes": upload.size or 0,
    }


def upload_video(upload, tenant):
    """Return (info, error). Enforces the byte cap; streams to Iceberg."""
    max_bytes = settings.MEDIA_MAX_VIDEO_BYTES
    if upload.size and upload.size > max_bytes:
        return None, f"Video is too large (max {max_bytes // (1024 * 1024)} MB)."
    content_type = getattr(upload, "content_type", None) or ""
    if not content_type.startswith("video/"):
        return None, "That file is not a video."

    key = _key(tenant, "video", upload.name)
    url = _upload(upload, upload.size, key, content_type)
    return {"public_id": key, "secure_url": url, "bytes": upload.size or 0}, None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_iceberg_media -v 2`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  add core/services/iceberg_media.py core/tests/test_iceberg_media.py cms_platform/settings.py
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  commit -m "feat(media): add Iceberg upload service + settings"
```

---

### Task 2: Switch the image upload view to Iceberg

**Files:**
- Modify: `dashboard/views.py` (line 34 import; `_save_upload` ~lines 2423–2455)
- Test: `core/tests/test_upload_views.py`

**Interfaces:**
- Consumes: `iceberg_media.validate_image`, `iceberg_media.is_configured`, `iceberg_media.upload_image` (Task 1).
- Produces: `POST` to `dashboard:tenant_upload_self` returns JSON `{ok: True, url, id}` and creates a `MediaAsset` whose `secure_url` is the CDN URL.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_upload_views.py`:

```python
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from core.models import MediaAsset, Template, TenantMembership, Tenant

IB = dict(
    ICEBERG_API_URL="https://api.test",
    ICEBERG_TOKEN="kic_test",
    ICEBERG_CDN="https://cdn.test",
    ICEBERG_TENANT="t1",
)

# A tiny valid 1x1 PNG so validate_image (Pillow) passes.
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


@override_settings(TENANT_BASE_DOMAIN="localhost", **IB)
class ImageUploadViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tpl = Template.objects.create(
            name="T", html_source="<section data-section='x'></section>"
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.tpl
        )
        self.user = User.objects.create_user("u", password="p")
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.user, role="owner"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, data):
        upload = SimpleUploadedFile("pic.png", data, content_type="image/png")
        return self.client.post(
            "/dashboard/editor/upload/", {"file": upload}, HTTP_HOST="acme.localhost"
        )

    def test_successful_image_upload_stores_cdn_url(self):
        with mock.patch(
            "core.services.iceberg_media.upload_image",
            return_value={
                "public_id": "cms/tenants/acme/image/ab-pic.png",
                "secure_url": "https://cdn.test/t1/cms/tenants/acme/image/ab-pic.png",
                "delivery_url": "https://cdn.test/t1/cms/tenants/acme/image/ab-pic.png",
                "bytes": len(PNG_1x1),
            },
        ):
            resp = self._post(PNG_1x1)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["url"], "https://cdn.test/t1/cms/tenants/acme/image/ab-pic.png")
        asset = MediaAsset.objects.get(id=body["id"])
        self.assertEqual(asset.secure_url, body["url"])
        self.assertEqual(asset.resource_type, MediaAsset.RESOURCE_IMAGE)

    def test_rejects_non_image(self):
        resp = self._post(b"not an image")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    @override_settings(ICEBERG_API_URL="", ICEBERG_TOKEN="", ICEBERG_TENANT="")
    def test_not_configured_returns_clean_error(self):
        resp = self._post(PNG_1x1)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("configured", resp.json()["error"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_upload_views.ImageUploadViewTests -v 2`
Expected: FAIL — `_save_upload` still calls `cloudinary_media`, so `is_configured()` is False and the success test fails with a 500.

- [ ] **Step 3: Edit the import**

In `dashboard/views.py` line 34, change:

```python
from core.services import cloudinary_media
```

to:

```python
from core.services import iceberg_media
```

- [ ] **Step 4: Rewrite `_save_upload`**

Replace the body of `_save_upload` (~lines 2423–2455) with:

```python
def _save_upload(request, tenant):
    """Image upload: validated at the door, then stored on Iceberg and served
    from cdn.katalyst-crm.com. Returns a clear error the editor can display."""
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "No file received."}, status=400)

    ok, error = iceberg_media.validate_image(upload)
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=400)

    if not iceberg_media.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Image storage isn't configured."}, status=500
        )

    try:
        result = iceberg_media.upload_image(upload, tenant)
    except Exception:
        logger.exception("Iceberg image upload failed for tenant %s", tenant.pk)
        return JsonResponse(
            {"ok": False, "error": "Upload failed — please try again."}, status=502
        )

    asset = MediaAsset.objects.create(
        tenant=tenant,
        original_name=upload.name[:240],
        resource_type=MediaAsset.RESOURCE_IMAGE,
        public_id=result["public_id"],
        secure_url=result["secure_url"],
        bytes=result.get("bytes", 0),
    )
    return JsonResponse({"ok": True, "url": result["delivery_url"], "id": asset.id})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test core.tests.test_upload_views.ImageUploadViewTests -v 2`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  add dashboard/views.py core/tests/test_upload_views.py
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  commit -m "feat(media): route image upload through Iceberg"
```

---

### Task 3: Replace the video flow (server-proxied, single step)

**Files:**
- Modify: `dashboard/views.py` — remove `_video_sign`, `_video_confirm`, `tenant_video_sign`, `tenant_video_confirm`, `tenant_video_sign_self`, `tenant_video_confirm_self`; add `_save_video_upload`, `tenant_video_upload`, `tenant_video_upload_self`; update `_render_editor` context (~lines 2105–2107, 2126–2128, 2200–2202)
- Modify: `dashboard/urls.py` — swap the four video-sign/confirm routes for two video-upload routes
- Modify: `templates/dashboard/editor.html:480-482` — window.CMS keys
- Modify: `static/js/editor.js` — video branch (~lines 632–719); bump cache-bust query string
- Test: `core/tests/test_upload_views.py` (add `VideoUploadViewTests`)

**Interfaces:**
- Consumes: `iceberg_media.upload_video` (Task 1).
- Produces: `POST` to `dashboard:tenant_video_upload_self` returns `{ok, url, id}`, creates a `MediaAsset` (`resource_type=video`). JS reads `window.CMS.videoUploadUrl`.

- [ ] **Step 1: Write the failing test**

Add to `core/tests/test_upload_views.py`:

```python
@override_settings(TENANT_BASE_DOMAIN="localhost", **IB)
class VideoUploadViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tpl = Template.objects.create(
            name="T", html_source="<section data-section='x'></section>"
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.tpl
        )
        self.user = User.objects.create_user("v", password="p")
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.user, role="owner"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, content_type="video/mp4"):
        upload = SimpleUploadedFile("clip.mp4", b"videobytes", content_type=content_type)
        return self.client.post(
            "/dashboard/editor/video-upload/",
            {"file": upload},
            HTTP_HOST="acme.localhost",
        )

    def test_successful_video_upload_stores_cdn_url(self):
        with mock.patch(
            "core.services.iceberg_media.upload_video",
            return_value=(
                {
                    "public_id": "cms/tenants/acme/video/ab-clip.mp4",
                    "secure_url": "https://cdn.test/t1/cms/tenants/acme/video/ab-clip.mp4",
                    "bytes": 10,
                },
                None,
            ),
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        asset = MediaAsset.objects.get(id=body["id"])
        self.assertEqual(asset.resource_type, MediaAsset.RESOURCE_VIDEO)
        self.assertEqual(asset.secure_url, body["url"])

    def test_rejects_non_video(self):
        resp = self._post(content_type="text/plain")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_upload_views.VideoUploadViewTests -v 2`
Expected: FAIL — 404 (route `/dashboard/editor/video-upload/` doesn't exist yet).

- [ ] **Step 3: Add the video-upload handler + wrapper views**

In `dashboard/views.py`, replace `_video_sign` and `_video_confirm` (~lines
2458–2491) with a single handler:

```python
def _save_video_upload(request, tenant):
    """Video upload: streamed through our server to Iceberg (no browser-direct
    upload — R2 blocks cross-origin PUT from tenant domains)."""
    upload = request.FILES.get("file")
    if not upload:
        return JsonResponse({"ok": False, "error": "No file received."}, status=400)

    if not iceberg_media.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Video storage isn't configured."}, status=500
        )

    info, error = iceberg_media.upload_video(upload, tenant)
    if error:
        return JsonResponse({"ok": False, "error": error}, status=400)

    asset = MediaAsset.objects.create(
        tenant=tenant,
        original_name=upload.name[:240],
        resource_type=MediaAsset.RESOURCE_VIDEO,
        public_id=info["public_id"],
        secure_url=info["secure_url"],
        bytes=info.get("bytes", 0),
    )
    return JsonResponse({"ok": True, "url": info["secure_url"], "id": asset.id})
```

Then replace the four video wrapper views (~lines 1470–1479 and 1520–1527) with
two:

```python
@require_POST
@agency_operator_required
def tenant_video_upload(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    return _save_video_upload(request, tenant)
```

```python
@require_POST
@tenant_member_required
def tenant_video_upload_self(request):
    return _save_video_upload(request, request.tenant)
```

> NOTE: match the exact decorators used by the neighbouring `tenant_upload` /
> `tenant_upload_self` views (copy their decorator lines verbatim — the snippet
> above shows the expected set; confirm against lines 1462–1469 / 1513–1519
> before saving).

- [ ] **Step 4: Update the editor context**

In `dashboard/views.py::_render_editor`:

Replace (tenant branch, ~2106–2107):
```python
        video_sign_url = reverse("dashboard:tenant_video_sign_self")
        video_confirm_url = reverse("dashboard:tenant_video_confirm_self")
```
with:
```python
        video_upload_url = reverse("dashboard:tenant_video_upload_self")
```

Replace (agency branch, ~2127–2128):
```python
        video_sign_url = reverse("dashboard:tenant_video_sign", args=[tenant.pk])
        video_confirm_url = reverse("dashboard:tenant_video_confirm", args=[tenant.pk])
```
with:
```python
        video_upload_url = reverse("dashboard:tenant_video_upload", args=[tenant.pk])
```

Replace (context dict, ~2201–2202):
```python
            "video_sign_url": video_sign_url,
            "video_confirm_url": video_confirm_url,
```
with:
```python
            "video_upload_url": video_upload_url,
```

- [ ] **Step 5: Update URLs**

In `dashboard/urls.py`, remove these four lines:
```python
    path("sites/<int:pk>/video-sign/", views.tenant_video_sign, name="tenant_video_sign"),
    path("sites/<int:pk>/video-confirm/", views.tenant_video_confirm, name="tenant_video_confirm"),
```
```python
    path("editor/video-sign/", views.tenant_video_sign_self, name="tenant_video_sign_self"),
    path("editor/video-confirm/", views.tenant_video_confirm_self, name="tenant_video_confirm_self"),
```
and add, respectively (next to the matching `upload` routes):
```python
    path("sites/<int:pk>/video-upload/", views.tenant_video_upload, name="tenant_video_upload"),
```
```python
    path("editor/video-upload/", views.tenant_video_upload_self, name="tenant_video_upload_self"),
```

- [ ] **Step 6: Update the template context keys**

In `templates/dashboard/editor.html`, replace lines 481–482:
```html
      videoSignUrl: "{{ video_sign_url }}",
      videoConfirmUrl: "{{ video_confirm_url }}",
```
with:
```html
      videoUploadUrl: "{{ video_upload_url }}",
```

- [ ] **Step 7: Simplify the editor.js video branch**

In `static/js/editor.js`, replace the whole video handler body inside
`vfile.addEventListener("change", function () { ... })` (the block spanning
~lines 641–718, from `var file = vfile.files[0];` through the closing
`});` of the `.catch`) with:

```javascript
          var file = vfile.files[0];
          if (!file) return;
          if (file.type.indexOf("video/") !== 0) {
            vname.textContent = "Please choose a video file.";
            vfile.value = "";
            return;
          }
          vname.textContent = "Uploading… 0%";
          var fd = new FormData();
          fd.append("file", file);
          var xhr = new XMLHttpRequest();
          xhr.open("POST", window.CMS.videoUploadUrl);
          xhr.setRequestHeader("X-CSRFToken", window.CMS.csrfToken);
          xhr.withCredentials = true;
          xhr.upload.onprogress = function (e) {
            if (e.lengthComputable) {
              vname.textContent = "Uploading… " + Math.round((e.loaded / e.total) * 100) + "%";
            }
          };
          xhr.onload = function () {
            var conf;
            try { conf = JSON.parse(xhr.responseText); } catch (err) { conf = null; }
            if (xhr.status < 200 || xhr.status >= 300 || !conf || !conf.ok) {
              vname.textContent = (conf && conf.error) || "Upload failed.";
              vfile.value = "";
              return;
            }
            vid.src = conf.url;
            vid.hidden = false;
            if (vid.load) vid.load();
            vname.textContent = file.name;
            setValue(fieldId, conf.url);
            var p = {}; p[fieldId] = conf.url;
            pushToPreview(p);
            scheduleSave();
          };
          xhr.onerror = function () {
            vname.textContent = "Upload failed — please try again.";
            vfile.value = "";
          };
          xhr.send(fd);
```

Then bump the editor.js cache-bust query string in `templates/dashboard/editor.html`
(find the `editor.js?v=...` reference and increment the value, matching the
existing cache-bust convention).

- [ ] **Step 8: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_upload_views -v 2`
Expected: PASS (all image + video tests)

- [ ] **Step 9: Full check — URLs resolve, nothing references removed names**

Run: `python manage.py check`
Expected: `System check identified no issues`.
Run: `grep -rn "video_sign\|video_confirm\|videoSignUrl\|videoConfirmUrl" dashboard templates static`
Expected: no matches.

- [ ] **Step 10: Commit**

```bash
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  add dashboard/views.py dashboard/urls.py templates/dashboard/editor.html static/js/editor.js core/tests/test_upload_views.py
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  commit -m "feat(media): server-proxied video upload to Iceberg"
```

---

### Task 4: Migration command for existing Cloudinary assets

**Files:**
- Create: `core/management/commands/migrate_cloudinary_to_iceberg.py`
- Test: `core/tests/test_migrate_cloudinary.py`

**Interfaces:**
- Consumes: `iceberg_media.upload_bytes` (Task 1), `httpx` (download).
- Produces: `manage.py migrate_cloudinary_to_iceberg [--apply] [--tenant SUB] [--limit N]`. Rewrites `res.cloudinary.com` URLs in `Tenant.content` and `MediaAsset.secure_url` to `{ICEBERG_CDN}/{ICEBERG_TENANT}/cloudinary/<public_id>.<ext>`. Dry-run by default.
- Helper (module-level, importable for tests): `cloudinary_key(url: str) -> str` returning `cloudinary/<public_id>.<ext>`.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_migrate_cloudinary.py`:

```python
import json
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.management.commands.migrate_cloudinary_to_iceberg import cloudinary_key

IB = dict(
    ICEBERG_API_URL="https://api.test",
    ICEBERG_TOKEN="kic_test",
    ICEBERG_CDN="https://cdn.test",
    ICEBERG_TENANT="t1",
)

CLOUD_URL = "https://res.cloudinary.com/dcuswyfur/image/upload/v1777474708/paula_hidalgo_loog_dwnugb.png"
NEW_URL = "https://cdn.test/t1/cloudinary/paula_hidalgo_loog_dwnugb.png"


def _tenant_with_url():
    tpl = Template.objects.create(
        name="T", html_source="<section data-section='x'></section>"
    )
    return Tenant.objects.create(
        name="Acme",
        subdomain="acme",
        template=tpl,
        content={"hero": {"image": CLOUD_URL}},
    )


class CloudinaryKeyTests(TestCase):
    def test_derives_key_without_version_segment(self):
        self.assertEqual(cloudinary_key(CLOUD_URL), "cloudinary/paula_hidalgo_loog_dwnugb.png")

    def test_preserves_nested_public_id(self):
        url = "https://res.cloudinary.com/dc/image/upload/v1/cms/tenants/acme/a_b.jpg"
        self.assertEqual(cloudinary_key(url), "cloudinary/cms/tenants/acme/a_b.jpg")


@override_settings(**IB)
class MigrateCommandTests(TestCase):
    def test_dry_run_makes_no_writes(self):
        t = _tenant_with_url()
        with mock.patch("httpx.get") as get, mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ) as up:
            call_command("migrate_cloudinary_to_iceberg")  # no --apply
        get.assert_not_called()
        up.assert_not_called()
        t.refresh_from_db()
        self.assertEqual(t.content["hero"]["image"], CLOUD_URL)

    def test_apply_rehosts_and_rewrites(self):
        t = _tenant_with_url()
        resp = mock.MagicMock()
        resp.content = b"imgbytes"
        resp.raise_for_status.return_value = None
        resp.headers = {"Content-Type": "image/png"}
        with mock.patch("httpx.get", return_value=resp), mock.patch(
            "core.services.iceberg_media.upload_bytes", return_value=NEW_URL
        ) as up:
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        up.assert_called_once()
        self.assertEqual(up.call_args.args[1], "cloudinary/paula_hidalgo_loog_dwnugb.png")
        t.refresh_from_db()
        self.assertEqual(t.content["hero"]["image"], NEW_URL)

    def test_apply_is_idempotent(self):
        t = _tenant_with_url()
        resp = mock.MagicMock()
        resp.content = b"imgbytes"
        resp.raise_for_status.return_value = None
        resp.headers = {"Content-Type": "image/png"}
        with mock.patch("httpx.get", return_value=resp), mock.patch(
            "core.services.iceberg_media.upload_bytes", return_value=NEW_URL
        ):
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        # second run: nothing left to migrate
        with mock.patch("httpx.get") as get, mock.patch(
            "core.services.iceberg_media.upload_bytes"
        ) as up:
            call_command("migrate_cloudinary_to_iceberg", "--apply")
        get.assert_not_called()
        up.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_migrate_cloudinary -v 2`
Expected: FAIL — `ModuleNotFoundError` for the command module.

- [ ] **Step 3: Write the command**

Create `core/management/commands/migrate_cloudinary_to_iceberg.py`:

```python
"""Re-host existing Cloudinary assets on Iceberg and rewrite stored URLs.

The Cloudinary delivery URLs in tenant content are public, so we download the
bytes directly — no Cloudinary API credentials needed. Dry-run by default.
"""
from __future__ import annotations

import json
import re

import httpx
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import MediaAsset, Tenant
from core.services import iceberg_media

CLOUDINARY_RE = re.compile(r"https?://res\.cloudinary\.com/[^\s\"'<>)\\]+")
# .../upload/(v1234/)?<public_id>.<ext>
_UPLOAD_RE = re.compile(r"/upload/(?:v\d+/)?(?P<pid>.+)$")


def cloudinary_key(url: str) -> str:
    """Map a Cloudinary delivery URL to the Iceberg key 'cloudinary/<public_id>.<ext>'."""
    m = _UPLOAD_RE.search(url.split("?", 1)[0])
    pid = m.group("pid") if m else url.rsplit("/", 1)[-1]
    return f"cloudinary/{pid}"


class Command(BaseCommand):
    help = "Re-host Cloudinary assets on Iceberg and rewrite stored URLs."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually write changes.")
        parser.add_argument("--tenant", default=None, help="Limit to one subdomain.")
        parser.add_argument("--limit", type=int, default=0, help="Cap distinct URLs migrated.")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        if apply and not iceberg_media.is_configured():
            self.stderr.write("Iceberg is not configured; aborting.")
            return

        tenants = Tenant.objects.all()
        if opts["tenant"]:
            tenants = tenants.filter(subdomain=opts["tenant"])

        # 1) discover distinct cloudinary URLs across content + media assets
        urls = set()
        for t in tenants:
            for u in CLOUDINARY_RE.findall(json.dumps(t.content or {})):
                urls.add(u)
        for a in MediaAsset.objects.filter(secure_url__contains="res.cloudinary.com"):
            urls.update(CLOUDINARY_RE.findall(a.secure_url))

        if opts["limit"]:
            urls = set(list(urls)[: opts["limit"]])

        self.stdout.write(f"Found {len(urls)} distinct Cloudinary URLs.")
        if not apply:
            for u in sorted(urls):
                self.stdout.write(f"  would migrate: {u} -> {iceberg_media._cdn_url(cloudinary_key(u))}")
            self.stdout.write("Dry run — pass --apply to migrate.")
            return

        # 2) download + re-host, building the old->new map
        mapping = {}
        for u in sorted(urls):
            try:
                resp = httpx.get(u, timeout=120.0, follow_redirects=True)
                resp.raise_for_status()
            except Exception as exc:
                self.stderr.write(f"  SKIP (download failed): {u} ({exc})")
                continue
            key = cloudinary_key(u)
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            try:
                new_url = iceberg_media.upload_bytes(resp.content, key, ct)
            except Exception as exc:
                self.stderr.write(f"  SKIP (upload failed): {u} ({exc})")
                continue
            mapping[u] = new_url
            self.stdout.write(f"  migrated: {u} -> {new_url}")

        if not mapping:
            self.stdout.write("Nothing migrated.")
            return

        # 3) rewrite content + media assets, per tenant, atomically
        rewritten = 0
        for t in tenants:
            blob = json.dumps(t.content or {})
            new_blob = blob
            for old, new in mapping.items():
                new_blob = new_blob.replace(old, new)
            if new_blob != blob:
                with transaction.atomic():
                    t.content = json.loads(new_blob)
                    t.save(update_fields=["content"])
                rewritten += 1
        for a in MediaAsset.objects.filter(secure_url__contains="res.cloudinary.com"):
            if a.secure_url in mapping:
                a.secure_url = mapping[a.secure_url]
                a.save(update_fields=["secure_url"])

        self.stdout.write(f"Done. Re-hosted {len(mapping)} assets, rewrote {rewritten} tenants.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_migrate_cloudinary -v 2`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  add core/management/commands/migrate_cloudinary_to_iceberg.py core/tests/test_migrate_cloudinary.py
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  commit -m "feat(media): add migrate_cloudinary_to_iceberg command"
```

---

### Task 5: Remove Cloudinary + wire deploy config

**Files:**
- Delete: `core/services/cloudinary_media.py`
- Modify: `requirements.txt` (remove `cloudinary>=1.40,<2.0`)
- Modify: `docker-compose.yml` (replace the `CLOUDINARY_*` env block ~lines 47–50 with `ICEBERG_*`)
- Modify: `.env.example` (swap Cloudinary keys for Iceberg keys, no values)

**Interfaces:**
- Consumes: nothing new. Verifies no code references Cloudinary anymore.

- [ ] **Step 1: Verify nothing imports cloudinary_media**

Run: `grep -rn "cloudinary_media\|import cloudinary" dashboard core cms_platform --include=*.py`
Expected: no matches (Task 2 removed the last import). If any remain, fix before deleting.

- [ ] **Step 2: Delete the module and drop the dependency**

```bash
git rm core/services/cloudinary_media.py
```
Edit `requirements.txt`: delete the line `cloudinary>=1.40,<2.0`.

- [ ] **Step 3: Update docker-compose.yml**

Replace (around lines 47–50):
```yaml
      # Media uploads (Cloudinary).
      CLOUDINARY_CLOUD_NAME: ${CLOUDINARY_CLOUD_NAME:-}
      CLOUDINARY_API_KEY: ${CLOUDINARY_API_KEY:-}
      CLOUDINARY_API_SECRET: ${CLOUDINARY_API_SECRET:-}
```
with:
```yaml
      # Media uploads (Iceberg — cdn.katalyst-crm.com).
      ICEBERG_API_URL: ${ICEBERG_API_URL:-}
      ICEBERG_TOKEN: ${ICEBERG_TOKEN:-}
      ICEBERG_CDN: ${ICEBERG_CDN:-https://cdn.katalyst-crm.com}
      ICEBERG_TENANT: ${ICEBERG_TENANT:-t1}
```

- [ ] **Step 4: Update .env.example**

Replace any `CLOUDINARY_*` lines with (no values):
```
ICEBERG_API_URL=
ICEBERG_TOKEN=
ICEBERG_CDN=https://cdn.katalyst-crm.com
ICEBERG_TENANT=t1
```

- [ ] **Step 5: Run the full suite + check**

Run: `python manage.py test -v 1`
Expected: whole suite green (no import errors from the deleted module).
Run: `python manage.py check`
Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  add -A requirements.txt docker-compose.yml .env.example core/services/cloudinary_media.py
git -c user.name='jezmerrr' -c user.email='jezmer_ramos@dlsu.edu.ph' \
  commit -m "chore(media): remove Cloudinary, wire Iceberg deploy config"
```

---

## Rollout (post-implementation, needs Dokploy API token)

Not code tasks — run after the branch is pushed and the Dokploy token is provided.

1. Push `main`.
2. In Dokploy (stack `cmsdashboard-sites-2ka9w7`), set env vars: `ICEBERG_API_URL=https://api-production-2bad.up.railway.app`, `ICEBERG_TOKEN=kic_…` (secret), `ICEBERG_CDN=https://cdn.katalyst-crm.com`, `ICEBERG_TENANT=t1`.
3. Redeploy the stack.
4. Smoke test: `curl https://sites.katek.app/healthz` → 200; upload one image + one video in the editor; confirm URLs are `cdn.katalyst-crm.com/t1/...`.
5. Migration dry-run in the `web` container:
   `python manage.py migrate_cloudinary_to_iceberg` → review report.
6. Apply: `python manage.py migrate_cloudinary_to_iceberg --apply`.
7. Spot-check a migrated site renders images from `cdn.katalyst-crm.com`.

## Self-Review notes

- **Spec coverage:** iceberg_media (Task 1) ✓; image view (Task 2) ✓; video view + JS + URLs (Task 3) ✓; migration command (Task 4) ✓; config/secrets + Cloudinary removal (Task 5) ✓; rollout ✓.
- **Placeholder scan:** all code steps carry full code; the only "confirm verbatim" note is Task 3 Step 3 decorators (real values shown, verify against neighbours).
- **Type consistency:** `upload_image` returns dict with `public_id/secure_url/delivery_url/bytes`; `upload_video` returns `(dict|None, error)` with `public_id/secure_url/bytes`; `upload_bytes(data, key, content_type) -> str`; `cloudinary_key(url) -> str`. Consistent across tasks 1/2/3/4.
