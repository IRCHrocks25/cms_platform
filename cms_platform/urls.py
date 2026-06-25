from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.db import connection
from django.http import JsonResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static


def healthz(request):
    """Container health check. Verifies the DB is reachable so an orchestrator
    only routes traffic to a container that can actually serve requests (e.g.
    after migrations have run). Host-agnostic and unauthenticated — no tenant
    resolution gates it. Returns 503 (not 500) when unhealthy so it reads as a
    transient "not ready" rather than an application error."""
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "error", "db": "unreachable"}, status=503)
    return JsonResponse({"status": "ok"})

from core import views as core_views
from core import ghl_views
from core.auth_views import (
    TenantAwareLoginView,
    TenantPasswordResetConfirmView,
    TenantPasswordResetView,
)


urlpatterns = [
    # Health check — no trailing slash so the orchestrator's probe gets a
    # direct 200 instead of an APPEND_SLASH 301. Kept first so nothing shadows it.
    path("healthz", healthz, name="healthz"),

    path("admin/", admin.site.urls),

    path("login/", TenantAwareLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Self-service password reset (tenant-aware; email sent via Resend).
    path("password-reset/", TenantPasswordResetView.as_view(), name="password_reset"),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="auth/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        TenantPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="auth/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),

    # GHL marketplace app endpoints. URLs are deliberately neutral
    # (no /ghl/ exposed) — see core/ghl_views.py.
    path("embed/", ghl_views.embed_view, name="ghl_embed"),
    path("embed/sop-assistant.js", core_views.embed_assistant_loader, name="embed_assistant_loader"),
    path("embed/assistant/<slug:slug>/", core_views.embed_assistant_frame, name="embed_assistant_frame"),
    path("api/embed/chat/<slug:slug>/", core_views.embed_assistant_chat, name="embed_assistant_chat"),
    path("connect/install/", ghl_views.oauth_install, name="ghl_oauth_install"),
    path("connect/callback/", ghl_views.oauth_callback, name="ghl_oauth_callback"),
    path("connect/webhook/", ghl_views.webhook, name="ghl_webhook"),
    path("privacy/", ghl_views.privacy, name="privacy"),
    path("terms/", ghl_views.terms, name="terms"),

    path("dashboard/", include("dashboard.urls")),

    # Public blog (tenant host: `<sub>.<base>/blog/...`).
    path("blog/", core_views.blog_index, name="blog_index"),
    path("blog/<slug:slug>/", core_views.blog_detail, name="blog_detail"),

    # Public blog (agency-host fallback: `/site/<sub>/blog/...`).
    path("site/<slug:subdomain>/blog/", core_views.blog_index_public, name="blog_index_public"),
    path("site/<slug:subdomain>/blog/<slug:slug>/", core_views.blog_detail_public, name="blog_detail_public"),

    # Inner page via agency-host fallback (`/site/<sub>/<slug>/`). Listed after
    # the blog routes so `blog` resolves to the blog index, not a page slug.
    path("site/<slug:subdomain>/<slug:slug>/", core_views.page_render_public, name="page_render_public"),

    path("site/<slug:subdomain>/", core_views.public_render, name="public_render"),

    # Inner page on a tenant host (`/<slug>/`). This is a catch-all single
    # segment, so it MUST stay last (before root) — every more specific route
    # above wins first. Unknown slugs 404 in page_render.
    path("<slug:slug>/", core_views.page_render, name="page_render"),
    path("", core_views.root_redirect, name="root"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
