from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from core import views as core_views
from core.auth_views import TenantAwareLoginView


urlpatterns = [
    path("admin/", admin.site.urls),

    path("login/", TenantAwareLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("dashboard/", include("dashboard.urls")),

    path("site/<slug:subdomain>/", core_views.public_render, name="public_render"),
    path("", core_views.root_redirect, name="root"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
