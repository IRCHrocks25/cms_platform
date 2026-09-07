"""Crawler-facing files on tenant hosts: /sitemap.xml and /robots.txt.

Both are host-agnostic Django routes registered before the ``<slug>/``
catch-all. They only answer on a resolved tenant host; the agency host and
unknown hosts 404 so nothing leaks about sites that do not exist. Unlike
page rendering there is no editor bypass: an unpublished site is invisible
to crawlers regardless of who is asking.
"""

from __future__ import annotations

from html.parser import HTMLParser
import re
from xml.sax.saxutils import escape

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

from .services import blocks, blog_render
from .urls_helpers import tenant_canonical_base_url


def _published_tenant_or_404(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("No site here")
    if not tenant.is_published:
        raise Http404("Site not published")
    return tenant


class _RobotsMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.noindex = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        values = {name.lower(): value or "" for name, value in attrs}
        if values.get("name", "").lower() != "robots":
            return
        directives = {
            token for token in re.split(r"[\s,]+", values.get("content", "").lower())
            if token
        }
        self.noindex = self.noindex or "noindex" in directives


def _page_is_indexable(tenant, page, *, nav_pages) -> bool:
    html = blocks.render_content(
        page.template,
        page.content,
        preview=False,
        site_settings=tenant.site_settings or {},
        nav_pages=nav_pages,
    )
    parser = _RobotsMetaParser()
    parser.feed(html)
    return not parser.noindex


def sitemap_entries(tenant) -> list[tuple[str, str]]:
    """``[(absolute_url, lastmod_date)]`` for everything public on a tenant."""
    base = tenant_canonical_base_url(tenant)
    entries = [(base, tenant.updated_at.date().isoformat())]
    pages = tenant.pages.filter(is_published=True).order_by("nav_order", "title")
    nav_pages = blocks.nav_pages_for(tenant)
    entries.extend(
        (f"{base}{page.slug}", page.updated_at.date().isoformat())
        for page in pages
        if _page_is_indexable(tenant, page, nav_pages=nav_pages)
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


@require_GET
def robots_txt(request):
    """Allow crawling on published sites; hide drafts entirely.

    Unlike the sitemap this answers 200 for an unpublished tenant, because a
    404 here means "no rules", which crawlers read as "crawl everything"."""
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("No site here")
    if not tenant.is_published:
        body = "User-agent: *\nDisallow: /\n"
    else:
        base = tenant_canonical_base_url(tenant)
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /dashboard/\n"
            "Disallow: /login/\n"
            "\n"
            f"Sitemap: {base}sitemap.xml\n"
        )
    return HttpResponse(body, content_type="text/plain; charset=utf-8")
