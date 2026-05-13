from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from core import views as core_views
from core.auth_views import TenantAwareLoginView


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

    path("debug-headers/", debug_headers),

    path("dashboard/", include("dashboard.urls")),

    path("site/<slug:subdomain>/", core_views.public_render, name="public_render"),
    path("", core_views.root_redirect, name="root"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
