from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from core import views as core_views
from core.auth_views import (
    TenantAwareLoginView,
    TenantPasswordResetConfirmView,
    TenantPasswordResetView,
)


# TEMPORARY: debug view for inspecting headers received from the Cloudflare Worker.
# Mounted at the project root so tenant resolution does not gate access. Remove
# after debugging.
def debug_headers(request):
    headers = {k: v for k, v in request.META.items() if k.startswith('HTTP_')}
    return JsonResponse({
        'http_x_original_host': request.META.get('HTTP_X_ORIGINAL_HOST', 'NOT FOUND'),
        'http_host': request.META.get('HTTP_HOST', 'NOT FOUND'),
        'all_http_headers': headers,
    })


urlpatterns = [
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

    path("debug-headers/", debug_headers),

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
