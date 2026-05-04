from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect

from .models import Tenant
from .renderer import render_site, merge_with_defaults


def root_redirect(request):
    if request.tenant is not None:
        if request.tenant.is_published or request.tenant.user_can_edit(request.user):
            return _render_tenant(request.tenant)
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
    return _render_tenant(tenant)


def _render_tenant(tenant: Tenant) -> HttpResponse:
    if not tenant.template_id:
        raise Http404("Template missing")
    content = merge_with_defaults(tenant.template.schema, tenant.content)
    html = render_site(tenant.template.html_source, content, preview=False)
    return HttpResponse(html)
