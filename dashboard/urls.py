from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    # Dispatcher — branches on request.tenant.
    path("", views.dashboard_root, name="root"),
    # Backwards-compat: old reverses to dashboard:home land on the dispatcher.
    path("home/", views.dashboard_root, name="home"),

    # ----- Agency surface (no tenant on host) ----------------------------- #
    path("templates/", views.template_list, name="template_list"),
    path("templates/new/", views.template_create, name="template_create"),
    path("templates/<int:pk>/", views.template_detail, name="template_detail"),
    path("templates/<int:pk>/delete/", views.template_delete, name="template_delete"),

    path("sites/", views.tenant_list, name="tenant_list"),
    path("sites/new/", views.tenant_create, name="tenant_create"),
    path("sites/check-subdomain/", views.check_subdomain, name="check_subdomain"),
    path("sites/<int:pk>/", views.tenant_detail, name="tenant_detail"),
    path("sites/<int:pk>/created/", views.site_created, name="site_created"),
    # Back-compat alias — old bookmarks/redirects still resolve.
    path("sites/<int:pk>/credentials/", views.site_credentials, name="site_credentials"),
    path("sites/<int:pk>/settings/", views.tenant_settings_update, name="tenant_settings_update"),
    path("sites/<int:pk>/custom-domain/", views.tenant_custom_domain_section, name="tenant_custom_domain_section"),
    path("sites/<int:pk>/custom-domain/add/", views.tenant_custom_domain_add, name="tenant_custom_domain_add"),
    path("sites/<int:pk>/custom-domain/verify/", views.tenant_custom_domain_verify, name="tenant_custom_domain_verify"),
    path("sites/<int:pk>/custom-domain/delete/", views.tenant_custom_domain_delete, name="tenant_custom_domain_delete"),
    path("sites/<int:pk>/delete/", views.tenant_delete, name="tenant_delete"),
    path("sites/<int:pk>/members/add/", views.tenant_member_add, name="tenant_member_add"),
    path("sites/<int:pk>/members/<int:membership_id>/remove/", views.tenant_member_remove, name="tenant_member_remove"),
    path("sites/<int:pk>/members/<int:membership_id>/role/", views.tenant_member_role, name="tenant_member_role"),
    path("sites/<int:pk>/edit/", views.tenant_editor, name="tenant_editor"),
    path("sites/<int:pk>/preview/", views.tenant_preview, name="tenant_preview"),
    path("sites/<int:pk>/save/", views.tenant_save, name="tenant_save"),
    path("sites/<int:pk>/publish/", views.tenant_publish, name="tenant_publish"),
    path("sites/<int:pk>/upload/", views.tenant_upload, name="tenant_upload"),

    path("custom-domains/", views.custom_domain_list, name="custom_domain_list"),
    path("custom-domains/<int:pk>/force-verify/", views.custom_domain_force_verify, name="custom_domain_force_verify"),
    path("custom-domains/<int:pk>/force-delete/", views.custom_domain_force_delete_local, name="custom_domain_force_delete_local"),

    path("users/", views.user_list, name="user_list"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/credentials/", views.user_credentials, name="user_credentials"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/deactivate/", views.user_deactivate, name="user_deactivate"),
    path("users/<int:pk>/activate/", views.user_activate, name="user_activate"),
    path("users/<int:pk>/make-staff/", views.user_make_staff, name="user_make_staff"),
    path("users/<int:pk>/memberships/<int:membership_id>/remove/", views.user_remove_membership, name="user_remove_membership"),

    # ----- Tenant surface (host resolves to a tenant) --------------------- #
    path("editor/", views.tenant_home, name="tenant_home"),
    path("editor/preview/", views.tenant_preview_self, name="tenant_preview_self"),
    path("editor/save/", views.tenant_save_self, name="tenant_save_self"),
    path("editor/publish/", views.tenant_publish_self, name="tenant_publish_self"),
    path("editor/upload/", views.tenant_upload_self, name="tenant_upload_self"),
]
