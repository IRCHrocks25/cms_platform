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

from core.models import BlogPost, MediaAsset, Page, Template, Tenant
from core.services import iceberg_media

# BlogPost text fields that can hold media URLs.
_BLOG_FIELDS = ("cover_image", "body", "og_image_url", "excerpt")

CLOUDINARY_RE = re.compile(r"https?://res\.cloudinary\.com/[^\s\"'<>)\\]+")
# .../upload/[<transforms>/]v<version>/<public_id>   (transforms + version optional)
_VER_RE = re.compile(r"/upload/(?:.+?/)?v\d+/(?P<pid>.+)$")
_NOVER_RE = re.compile(r"/upload/(?P<pid>.+)$")


def cloudinary_key(url: str) -> str:
    """Map a Cloudinary delivery URL to the Iceberg key 'cloudinary/<public_id>'.

    Handles an optional transformation segment (e.g. ``f_auto,q_auto/``) and an
    optional version segment (``v1234/``) between ``/upload/`` and the public id.
    """
    u = url.split("?", 1)[0]
    m = _VER_RE.search(u)
    if m:
        return f"cloudinary/{m.group('pid')}"
    m = _NOVER_RE.search(u)
    pid = m.group("pid") if m else u.rsplit("/", 1)[-1]
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

        # Templates hold the annotated-HTML defaults the renderer falls back to
        # for unedited fields, so their src URLs must be migrated too. Templates
        # are global; only scan them on a full (non --tenant) run.
        templates = Template.objects.none() if opts["tenant"] else Template.objects.all()

        pages = Page.objects.filter(tenant__in=tenants)
        blogs = BlogPost.objects.filter(tenant__in=tenants)

        # 1) discover distinct cloudinary URLs across every place they can live
        urls = set()
        for t in tenants:
            urls.update(CLOUDINARY_RE.findall(json.dumps(t.content or {})))
        for p in pages:
            urls.update(CLOUDINARY_RE.findall(json.dumps(p.content or {})))
        for b in blogs:
            for f in _BLOG_FIELDS:
                urls.update(CLOUDINARY_RE.findall(getattr(b, f) or ""))
        for a in MediaAsset.objects.filter(secure_url__contains="res.cloudinary.com"):
            urls.update(CLOUDINARY_RE.findall(a.secure_url))
        for tpl in templates:
            urls.update(CLOUDINARY_RE.findall(tpl.html_source or ""))

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

        def _rewrite(text: str) -> str:
            for old, new in mapping.items():
                text = text.replace(old, new)
            return text

        # 3) rewrite everywhere, atomically
        rewritten = 0
        for t in tenants:
            blob = json.dumps(t.content or {})
            new_blob = _rewrite(blob)
            if new_blob != blob:
                with transaction.atomic():
                    t.content = json.loads(new_blob)
                    t.save(update_fields=["content"])
                rewritten += 1

        rewritten_pages = 0
        for p in pages:
            blob = json.dumps(p.content or {})
            new_blob = _rewrite(blob)
            if new_blob != blob:
                with transaction.atomic():
                    p.content = json.loads(new_blob)
                    p.save(update_fields=["content"])
                rewritten_pages += 1

        rewritten_blogs = 0
        for b in blogs:
            changed = {}
            for f in _BLOG_FIELDS:
                cur = getattr(b, f) or ""
                new = _rewrite(cur)
                if new != cur:
                    setattr(b, f, new)
                    changed[f] = new
            if changed:
                with transaction.atomic():
                    b.save(update_fields=list(changed))
                rewritten_blogs += 1

        for a in MediaAsset.objects.filter(secure_url__contains="res.cloudinary.com"):
            if a.secure_url in mapping:
                a.secure_url = mapping[a.secure_url]
                a.save(update_fields=["secure_url"])

        # Rewrite template defaults. Template.save() rebuilds the derived schema,
        # which is fine — only src attribute values change, not structure.
        rewritten_templates = 0
        for tpl in templates:
            html = tpl.html_source or ""
            new_html = html
            for old, new in mapping.items():
                new_html = new_html.replace(old, new)
            if new_html != html:
                with transaction.atomic():
                    tpl.html_source = new_html
                    tpl.save()
                rewritten_templates += 1

        self.stdout.write(
            f"Done. Re-hosted {len(mapping)} assets; rewrote {rewritten} tenants, "
            f"{rewritten_pages} pages, {rewritten_blogs} blog posts, "
            f"{rewritten_templates} templates."
        )
