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
                self.stdout.write(
                    f"  would migrate: {u} -> {iceberg_media._cdn_url(cloudinary_key(u))}"
                )
            self.stdout.write("Dry run — pass --apply to migrate.")
            return

        # 2) re-host (or reuse), building the old->new map. If the target key is
        # already on the CDN (a prior migration), just reuse it — don't re-upload.
        mapping = {}
        for u in sorted(urls):
            key = cloudinary_key(u)
            new_url = iceberg_media._cdn_url(key)
            try:
                head = httpx.head(new_url, timeout=30.0, follow_redirects=True)
                if head.status_code == 200:
                    mapping[u] = new_url
                    self.stdout.write(f"  reuse (already on CDN): {u} -> {new_url}")
                    continue
            except Exception:
                pass  # fall through to download + upload

            try:
                resp = httpx.get(u, timeout=120.0, follow_redirects=True)
                resp.raise_for_status()
            except Exception as exc:
                self.stderr.write(f"  SKIP (not on CDN and download failed): {u} ({exc})")
                continue
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

        self.stdout.write(
            f"Done. Re-hosted {len(mapping)} assets, rewrote {rewritten} tenants."
        )
