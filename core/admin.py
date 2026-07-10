from django.contrib import admin

from .models import (
    Template, Tenant, TenantMembership, MediaAsset, ContentVersion, BlogPost,
    Page, AnnotationJob, EmbeddableAssistant, GhlAgencyInstall, GhlInstall,
)


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "updated_at")
    search_fields = ("name", "slug")
    readonly_fields = ("schema", "created_at", "updated_at")


class TenantMembershipInline(admin.TabularInline):
    model = TenantMembership
    extra = 1
    autocomplete_fields = ("user",)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "subdomain", "template", "owner", "is_published", "updated_at")
    list_filter = ("is_published", "template")
    search_fields = ("name", "subdomain")
    inlines = [TenantMembershipInline]


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("tenant", "user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("tenant__name", "tenant__subdomain", "user__username", "user__email")
    autocomplete_fields = ("tenant", "user")


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "slug", "template", "is_published", "nav_order", "updated_at")
    list_filter = ("is_published", "tenant")
    search_fields = ("title", "slug", "tenant__name", "tenant__subdomain")
    readonly_fields = ("created_at", "updated_at")


admin.site.register(MediaAsset)
admin.site.register(ContentVersion)


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "status", "featured", "publish_date", "updated_at")
    list_filter = ("status", "featured", "tenant")
    search_fields = ("title", "slug", "tenant__name", "tenant__subdomain")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AnnotationJob)
class AnnotationJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "created_by", "created_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("id", "created_by__username")
    readonly_fields = (
        "id", "status", "created_by", "result_html", "sections", "error",
        "created_at", "updated_at",
    )


@admin.register(EmbeddableAssistant)
class EmbeddableAssistantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "brand", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "brand", "brand_full")


@admin.register(GhlAgencyInstall)
class GhlAgencyInstallAdmin(admin.ModelAdmin):
    list_display = ("company_id", "company_name", "expires_at", "updated_at")
    search_fields = ("company_id", "company_name")
    readonly_fields = ("access_token", "refresh_token", "available_locations",
                       "installed_at", "updated_at")


@admin.register(GhlInstall)
class GhlInstallAdmin(admin.ModelAdmin):
    list_display = ("location_id", "location_name", "tenant", "agency", "status", "updated_at")
    search_fields = ("location_id", "location_name")
    list_filter = ("status",)
    readonly_fields = ("access_token", "refresh_token", "installed_at", "updated_at")
