from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from .models import Tenant
from .renderer import render_site, merge_with_defaults, apply_head_settings
from .services import blog_render


def root_redirect(request):
    if request.tenant is not None:
        if request.tenant.is_published or request.tenant.user_can_edit(request.user):
            return _render_tenant(request.tenant, request)
        # Unpublished tenant host, anonymous/non-member visitor: don't
        # leak the draft. Operators/owners see the rendered page above.
        if request.user.is_authenticated:
            return redirect("dashboard:root")
        return redirect("login")
    if request.user.is_authenticated:
        return redirect("dashboard:root")
    return redirect("login")


def public_render(request, subdomain):
    tenant = get_object_or_404(Tenant, subdomain=subdomain)
    if not tenant.is_published and not tenant.user_can_edit(request.user):
        raise Http404("Site not published")
    return _render_tenant(tenant, request, blog_base=f"/site/{tenant.subdomain}/blog/")


def _render_tenant(tenant: Tenant, request=None, *, blog_base: str = "/blog/") -> HttpResponse:
    if not tenant.template_id:
        raise Http404("Template missing")
    content = merge_with_defaults(tenant.template.schema, tenant.content)
    html = render_site(
        tenant.template.html_source,
        content,
        preview=False,
        site_settings=tenant.site_settings or {},
    )
    html = blog_render.inject_strip(html, tenant, request=request, blog_base=blog_base)
    return HttpResponse(html)


# --------------------------------------------------------------------------- #
# Public blog: index + detail                                                  #
# --------------------------------------------------------------------------- #


def _tenant_visible(tenant, request) -> bool:
    """Same gate as the homepage: published, or visible to an editor."""
    return tenant.is_published or tenant.user_can_edit(request.user)


def blog_index(request):
    """Blog index on a tenant host (`/blog/`)."""
    if request.tenant is None:
        raise Http404("No site here")
    return _blog_index(request, request.tenant, blog_base="/blog/")


def blog_detail(request, slug):
    """Single post on a tenant host (`/blog/<slug>/`)."""
    if request.tenant is None:
        raise Http404("No site here")
    return _blog_detail(request, request.tenant, slug, blog_base="/blog/")


def blog_index_public(request, subdomain):
    """Blog index via the agency-host fallback (`/site/<sub>/blog/`)."""
    tenant = get_object_or_404(Tenant, subdomain=subdomain)
    return _blog_index(request, tenant, blog_base=f"/site/{subdomain}/blog/")


def blog_detail_public(request, subdomain, slug):
    tenant = get_object_or_404(Tenant, subdomain=subdomain)
    return _blog_detail(request, tenant, slug, blog_base=f"/site/{subdomain}/blog/")


def _blog_index(request, tenant, *, blog_base):
    if not _tenant_visible(tenant, request):
        raise Http404("Site not published")

    settings = blog_render.get_blog_settings(tenant)
    style = settings["template"]
    page_size = blog_render.INDEX_PAGE_SIZE.get(style, 8)

    posts = blog_render.published_posts(tenant).order_by("-publish_date", "-created_at")
    paginator = Paginator(posts, page_size)
    page = paginator.get_page(request.GET.get("page"))

    site_settings = dict(tenant.site_settings or {})
    site_settings["page_title"] = f"{settings['title']} · {tenant.name}"

    html = blog_render.render_index(
        tenant,
        {
            "tenant": tenant,
            "settings": settings,
            "style": style,
            "page_obj": page,
            "posts": page.object_list,
            "accent": blog_render.accent_color(tenant),
            "blog_base": blog_base,
        },
        style=style,
        request=request,
    )
    html = apply_head_settings(html, site_settings)
    return HttpResponse(html)


def _blog_detail(request, tenant, slug, *, blog_base):
    if not _tenant_visible(tenant, request):
        raise Http404("Site not published")

    can_edit = tenant.user_can_edit(request.user)
    qs = tenant.blog_posts.all() if can_edit else blog_render.published_posts(tenant)
    post = qs.filter(slug=slug).first()
    if post is None:
        raise Http404("Post not found")

    html, _ = blog_render.render_detail(
        tenant,
        post,
        request=request,
        blog_base=blog_base,
        is_preview=not post.is_live,
    )
    html = apply_head_settings(html, post.resolved_seo(tenant.site_settings or {}))
    return HttpResponse(html)
