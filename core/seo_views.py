"""Crawler-facing files on tenant hosts: /sitemap.xml and /robots.txt.

Both are host-agnostic Django routes registered before the ``<slug>/``
catch-all. They only answer on a resolved tenant host; the agency host and
unknown hosts 404 so nothing leaks about sites that do not exist. Unlike
page rendering there is no editor bypass: an unpublished site is invisible
to crawlers regardless of who is asking.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

from .services import blog_render
from .urls_helpers import tenant_canonical_base_url


def _published_tenant_or_404(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("No site here")
    if not tenant.is_published:
        raise Http404("Site not published")
    return tenant


def sitemap_entries(tenant) -> list[tuple[str, str]]:
    """``[(absolute_url, lastmod_date)]`` for everything public on a tenant."""
    base = tenant_canonical_base_url(tenant)
    entries = [(base, tenant.updated_at.date().isoformat())]
    pages = tenant.pages.filter(is_published=True).order_by("nav_order", "title")
    entries.extend(
        (f"{base}{page.slug}/", page.updated_at.date().isoformat()) for page in pages
    )
    posts = list(
        blog_render.published_posts(tenant).order_by("-publish_date", "-created_at")
    )
    if posts:
        newest = max(post.updated_at for post in posts)
        entries.append((f"{base}blog/", newest.date().isoformat()))
        entries.extend(
            (f"{base}blog/{post.slug}/", post.updated_at.date().isoformat())
            for post in posts
        )
    return entries


@require_GET
def sitemap_xml(request):
    tenant = _published_tenant_or_404(request)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, lastmod in sitemap_entries(tenant):
        lines.append(
            f"  <url><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></url>"
        )
    lines.append("</urlset>")
    return HttpResponse("\n".join(lines) + "\n", content_type="application/xml")
