import json

from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import EmbeddableAssistant, Page, Tenant
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
# Public inner pages (additional annotated pages: /about/, /services/, ...)    #
# --------------------------------------------------------------------------- #


def page_render(request, slug):
    """An inner page on a tenant host (`/<slug>/`)."""
    if request.tenant is None:
        raise Http404("No site here")
    return _render_page(request, request.tenant, slug, blog_base="/blog/")


def page_render_public(request, subdomain, slug):
    """An inner page via the agency-host fallback (`/site/<sub>/<slug>/`)."""
    tenant = get_object_or_404(Tenant, subdomain=subdomain)
    return _render_page(request, tenant, slug, blog_base=f"/site/{subdomain}/blog/")


def _render_page(request, tenant: Tenant, slug: str, *, blog_base: str) -> HttpResponse:
    page = get_object_or_404(Page, tenant=tenant, slug=slug)
    # Same visibility gate as the homepage: a draft page is only visible to an
    # editor/operator, never leaked to the public.
    if not page.is_published and not tenant.user_can_edit(request.user):
        raise Http404("Page not published")
    content = merge_with_defaults(page.template.schema, page.content)
    html = render_site(
        page.template.html_source,
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


# --------------------------------------------------------------------------- #
# Embeddable assistant (public iframe + loader + lightweight chat API)         #
# --------------------------------------------------------------------------- #


def _assistant_config(assistant: EmbeddableAssistant, request) -> dict:
    """Assistant config with query overrides for one-off embeds."""
    cfg = {
        "brand": assistant.brand or "Assistant",
        "brand_full": assistant.brand_full or assistant.brand or assistant.name,
        "description": assistant.description or "",
        "logo": assistant.logo_url or "",
        "orb_logo": assistant.orb_logo_url or assistant.logo_url or "",
        "greeting": assistant.greeting or "Hi there! How can I help you today?",
        "suggestions": assistant.suggestions or "",
        "powered_by": assistant.powered_by or assistant.brand or assistant.name,
        "voice": assistant.voice or "marin",
        "launcher_label": assistant.launcher_label or "Need help? Ask us!",
        "extra_instructions": assistant.extra_instructions or "",
    }
    for key in cfg.keys():
        override = (request.GET.get(key) or "").strip()
        if override:
            cfg[key] = override
    return cfg


@require_GET
def embed_assistant_loader(request):
    """Script-tag loader that mounts a toggleable fixed iframe widget."""
    js = r"""
(function () {
  var script = document.currentScript;
  if (!script) return;
  var origin = (script.dataset.apiBase || new URL(script.src).origin).replace(/\/+$/, "");
  var assistant = (script.dataset.assistant || "default").trim();
  var width = parseInt(script.dataset.width || "400", 10) || 400;
  var height = parseInt(script.dataset.height || "640", 10) || 640;
  var zIndex = parseInt(script.dataset.zIndex || "150", 10) || 150;

  var params = new URLSearchParams();
  function addParam(dataKey, queryKey) {
    var value = (script.dataset[dataKey] || "").trim();
    if (value) params.set(queryKey, value);
  }
  addParam("brand", "brand");
  addParam("brandFull", "brand_full");
  addParam("description", "description");
  addParam("logo", "logo");
  addParam("orbLogo", "orb_logo");
  addParam("greeting", "greeting");
  addParam("suggestions", "suggestions");
  addParam("poweredBy", "powered_by");
  addParam("voice", "voice");
  addParam("launcherLabel", "launcher_label");
  addParam("extraInstructions", "extra_instructions");
  addParam("tenant", "tenant");

  var src = origin + "/embed/assistant/" + encodeURIComponent(assistant) + "/";
  var query = params.toString();
  if (query) src += "?" + query;

  var root = document.createElement("div");
  root.style.position = "fixed";
  root.style.right = "20px";
  root.style.bottom = "20px";
  root.style.zIndex = String(zIndex);
  root.style.fontFamily = "system-ui,-apple-system,Segoe UI,Roboto,sans-serif";

  var frame = document.createElement("iframe");
  frame.src = src;
  frame.title = "AI Assistant";
  frame.allow = "microphone";
  frame.style.width = width + "px";
  frame.style.height = height + "px";
  frame.style.border = "none";
  frame.style.borderRadius = "16px";
  frame.style.boxShadow = "0 24px 60px -24px rgba(0,0,0,0.55)";
  frame.style.display = "none";
  frame.style.background = "#fff";

  var launcher = document.createElement("button");
  launcher.type = "button";
  launcher.setAttribute("aria-expanded", "false");
  launcher.style.display = "inline-flex";
  launcher.style.alignItems = "center";
  launcher.style.gap = "10px";
  launcher.style.border = "none";
  launcher.style.padding = "12px 14px";
  launcher.style.borderRadius = "999px";
  launcher.style.background = "linear-gradient(135deg,#1d4ed8,#1e3a8a)";
  launcher.style.color = "#fff";
  launcher.style.fontSize = "13px";
  launcher.style.fontWeight = "600";
  launcher.style.cursor = "pointer";
  launcher.style.boxShadow = "0 12px 32px -16px rgba(30,58,138,.65)";
  launcher.textContent = script.dataset.launcherLabel || "Need help? Ask us!";

  launcher.addEventListener("click", function () {
    var open = frame.style.display !== "none";
    frame.style.display = open ? "none" : "block";
    launcher.setAttribute("aria-expanded", open ? "false" : "true");
    launcher.textContent = open ? (script.dataset.launcherLabel || "Need help? Ask us!") : "Close assistant";
  });

  root.appendChild(frame);
  root.appendChild(launcher);
  document.body.appendChild(root);
})();
"""
    return HttpResponse(js, content_type="application/javascript")


@require_GET
def embed_assistant_frame(request, slug):
    assistant = get_object_or_404(EmbeddableAssistant, slug=slug, is_active=True)
    config = _assistant_config(assistant, request)
    return render(
        request,
        "embed/assistant_widget.html",
        {
            "assistant": assistant,
            "config": config,
            "chat_endpoint": f"/api/embed/chat/{assistant.slug}/",
        },
    )


@require_http_methods(["POST"])
@csrf_exempt
def embed_assistant_chat(request, slug):
    assistant = get_object_or_404(EmbeddableAssistant, slug=slug, is_active=True)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON body.")
    message = (payload.get("message") or "").strip()
    if not message:
        return JsonResponse({"success": False, "error": "Message is required."}, status=400)

    tone = assistant.brand_full or assistant.brand or assistant.name
    hints = [s.strip() for s in (assistant.suggestions or "").split("|") if s.strip()]
    fallback_hint = hints[0] if hints else "Tell me your goal and I can point you to the right next step."
    lowered = message.lower()
    if any(token in lowered for token in ("price", "cost", "plan", "pricing")):
        reply = f"{tone}: pricing can vary by setup. Share your use case and team size, and I will suggest the best-fit option."
    elif any(token in lowered for token in ("demo", "book", "call", "meeting")):
        reply = f"{tone}: great idea. I can help you prepare a short brief so your team can book the right demo quickly."
    elif any(token in lowered for token in ("support", "help", "issue", "problem")):
        reply = f"{tone}: I can help troubleshoot this. Start with what you expected to happen and what happened instead."
    else:
        reply = f"{tone}: thanks for the question. {fallback_hint}"

    return JsonResponse({"success": True, "reply": reply})
