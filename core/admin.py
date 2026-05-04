from django.contrib import admin

from .models import Template, Tenant, TenantMembership, MediaAsset, ContentVersion


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


admin.site.register(MediaAsset)
admin.site.register(ContentVersion)
