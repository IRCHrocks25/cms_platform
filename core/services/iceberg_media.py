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
# Images: validate at the door (identical rules to the old Cloudinary path)   #
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


def optimize_image_upload(upload):
    """Downscale + recompress an uploaded image in memory.

    Phone/camera photos are routinely several MB and 3000px+, which bloats page
    load. We cap the longest edge at ``MEDIA_IMAGE_MAX_DIMENSION`` and recompress
    in the original format (JPEG/PNG/WebP), baking in EXIF orientation and
    dropping metadata. Returns ``(fileobj, size, content_type)``.

    Robust by design: animated images, GIFs, unknown formats, and any decode
    error fall back to the original upload untouched, so the upload path never
    fails just because optimization couldn't run.
    """
    original_ct = getattr(upload, "content_type", None) or "application/octet-stream"

    def _original():
        try:
            upload.seek(0)
        except Exception:
            pass
        return upload, (upload.size or 0), original_ct

    max_dim = getattr(settings, "MEDIA_IMAGE_MAX_DIMENSION", 0)
    quality = getattr(settings, "MEDIA_IMAGE_JPEG_QUALITY", 82)

    try:
        from PIL import Image, ImageOps

        upload.seek(0)
        img = Image.open(upload)
        fmt = (img.format or "").upper()

        # Never flatten animation to a single frame.
        if getattr(img, "is_animated", False) or fmt == "GIF":
            return _original()

        img = ImageOps.exif_transpose(img)  # bake orientation, drop EXIF

        downscaled = False
        if max_dim and max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
            downscaled = True

        buf = io.BytesIO()
        if fmt in ("JPEG", "JPG"):
            img.convert("RGB").save(
                buf, format="JPEG", quality=quality, optimize=True, progressive=True
            )
            out_ct = "image/jpeg"
        elif fmt == "PNG":
            img.save(buf, format="PNG", optimize=True)
            out_ct = "image/png"
        elif fmt == "WEBP":
            img.save(buf, format="WEBP", quality=quality, method=6)
            out_ct = "image/webp"
        else:
            return _original()

        size = buf.tell()
        # If we didn't shrink dimensions and the re-encode isn't smaller, the
        # original was already lean — keep it rather than risk a larger file.
        if not downscaled and upload.size and size >= upload.size:
            return _original()

        buf.seek(0)
        return buf, size, out_ct
    except Exception:
        logger.warning("Image optimize failed; uploading original", exc_info=True)
        return _original()


def upload_image(upload, tenant) -> dict:
    key = _key(tenant, "image", upload.name)
    fileobj, size, content_type = optimize_image_upload(upload)
    url = _upload(fileobj, size, key, content_type)
    return {
        "public_id": key,
        "secure_url": url,
        "delivery_url": url,
        "bytes": size,
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
