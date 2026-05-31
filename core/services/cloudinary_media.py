"""
Cloudinary media helpers for client uploads.

- Images are routed through our server, validated at the door (real-image
  content sniff via Pillow + size cap), then uploaded to Cloudinary and served
  with f_auto,q_auto so browsers get WebP/AVIF automatically.
- Videos use a SIGNED DIRECT upload (browser -> Cloudinary) so large files never
  route through our server; we sign the request, then verify the returned
  public_id (resource_type=video, size/duration caps) before storing.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings

logger = logging.getLogger("core")


def is_configured() -> bool:
    return bool(
        settings.CLOUDINARY_CLOUD_NAME
        and settings.CLOUDINARY_API_KEY
        and settings.CLOUDINARY_API_SECRET
    )


def _configure():
    import cloudinary

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def _folder(tenant, sub: str = "") -> str:
    base = f"cms/tenants/{tenant.subdomain}"
    return f"{base}/{sub}" if sub else base


# --------------------------------------------------------------------------- #
# Images — validate at the door, then server-side upload                        #
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


def upload_image(upload, tenant) -> dict:
    """Upload the ORIGINAL to Cloudinary; return ids + an f_auto,q_auto URL."""
    _configure()
    import cloudinary
    import cloudinary.uploader

    res = cloudinary.uploader.upload(
        upload, folder=_folder(tenant), resource_type="image"
    )
    public_id = res["public_id"]
    delivery_url, _ = cloudinary.utils.cloudinary_url(
        public_id, fetch_format="auto", quality="auto", secure=True
    )
    return {
        "public_id": public_id,
        "secure_url": res.get("secure_url", ""),
        "delivery_url": delivery_url,
        "bytes": res.get("bytes", 0),
    }


# --------------------------------------------------------------------------- #
# Video — signed direct upload (browser -> Cloudinary), then verify            #
# --------------------------------------------------------------------------- #


def sign_video_upload(tenant) -> dict:
    """Signed params for a direct browser upload to Cloudinary (video)."""
    _configure()
    import cloudinary.utils

    timestamp = int(time.time())
    folder = _folder(tenant, "video")
    params_to_sign = {"timestamp": timestamp, "folder": folder}
    signature = cloudinary.utils.api_sign_request(
        params_to_sign, settings.CLOUDINARY_API_SECRET
    )
    return {
        "cloud_name": settings.CLOUDINARY_CLOUD_NAME,
        "api_key": settings.CLOUDINARY_API_KEY,
        "timestamp": timestamp,
        "folder": folder,
        "signature": signature,
    }


def verify_video(public_id: str):
    """Return (info, error). Confirms resource_type=video and enforces caps."""
    _configure()
    import cloudinary.api

    try:
        info = cloudinary.api.resource(public_id, resource_type="video")
    except Exception:
        return None, "Could not find the uploaded video."

    if info.get("resource_type") != "video":
        return None, "That upload is not a video."
    if info.get("bytes", 0) > settings.MEDIA_MAX_VIDEO_BYTES:
        return None, f"Video is too large (max {settings.MEDIA_MAX_VIDEO_BYTES // (1024 * 1024)} MB)."
    duration = info.get("duration") or 0
    if duration and duration > settings.MEDIA_MAX_VIDEO_DURATION:
        return None, f"Video is too long (max {settings.MEDIA_MAX_VIDEO_DURATION}s)."
    return info, None
