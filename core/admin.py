from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import ProtectedError

from .models import (
    BlockType, Template, Tenant, TenantMembership, MediaAsset, ContentVersion,
    BlogPost, Page, AnnotationJob, EmbeddableAssistant, GhlAgencyInstall,
    GhlInstall,
)


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Default User admin plus a clear error when the user still owns a site.

    Tenant.owner is PROTECT (A2): a raw delete raises ProtectedError. Catching
    it here tells the operator to delete or reassign the site first instead of
    a generic 500.
    """

    def delete_model(self, request, obj):
        try:
            super().delete_model(request, obj)
        except ProtectedError:
            self.message_user(
                request,
                f"Cannot delete {obj.username}: they still own one or more "
                "sites. Delete or reassign those sites first.",
                level=messages.ERROR,
            )

    def delete_queryset(self, request, queryset):
        try:
            super().delete_queryset(request, queryset)
        except ProtectedError:
            self.message_user(
                request,
                "Cannot delete users who still own sites. Delete or reassign "
                "those sites first.",
                level=messages.ERROR,
            )


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "updated_at")
    search_fields = ("name", "slug")
    # html_source is read-only here on purpose: a direct admin save bypasses
    # versioning, the field-loss guard, the MCP if_match check and the no-op
    # detection. core/services/templates.py is the only supported write path.
    readonly_fields = ("html_source", "schema", "created_at", "updated_at")
    filter_horizontal = ("allowed_block_types",)


@admin.register(BlockType)
class BlockTypeAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "category", "is_active", "updated_at")
    list_filter = ("is_active", "category")
    search_fields = ("key", "label", "category")
    readonly_fields = ("schema", "created_at", "updated_at")
    actions = ("annotate_with_ai",)

    @admin.action(description="Annotate HTML with AI (Phase 4 fragment annotator)")
    def annotate_with_ai(self, request, queryset):
        from core.services.annotator import AnnotatorError, annotate_fragment

        ok = 0
        for block in queryset:
            try:
                block.html_source = annotate_fragment(block.html_source)
                block.save()  # re-derives schema from the annotated fragment
                ok += 1
            except AnnotatorError as exc:
                self.message_user(request, f"{block.key}: {exc}", level="error")
        if ok:
            self.message_user(request, f"Annotated {ok} block(s).")


class TenantMembershipInline(admin.TabularInline):
    model = TenantMembership
    extra = 1
    autocomplete_fields = ("user",)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "subdomain", "template", "owner", "is_published", "updated_at")
    list_filter = ("is_published", "template")
    search_fields = ("name", "subdomain")
    # content is read-only here: writes must go through the content services so
    # the stored shape stays canonical.
    readonly_fields = ("content",)
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
    # See TenantAdmin: content writes go through the content services.
    readonly_fields = ("content", "created_at", "updated_at")


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


@admin.action(description="whether an OAuth token is present")
def _has_token(obj):
    # Never surface the live OAuth tokens in the admin. Showing whether one
    # exists is enough for operators; the tokens themselves are excluded from
    # the form entirely (A11).
    return bool(getattr(obj, "access_token", ""))


_has_token.boolean = True
_has_token.short_description = "Has token"


@admin.register(GhlAgencyInstall)
class GhlAgencyInstallAdmin(admin.ModelAdmin):
    list_display = ("company_id", "company_name", _has_token, "expires_at", "updated_at")
    search_fields = ("company_id", "company_name")
    # access_token / refresh_token are deliberately NOT in the form — a masked
    # "has token" column is all operators need (A11).
    exclude = ("access_token", "refresh_token")
    readonly_fields = ("available_locations", "installed_at", "updated_at")


@admin.register(GhlInstall)
class GhlInstallAdmin(admin.ModelAdmin):
    list_display = ("location_id", "location_name", "tenant", "agency", _has_token,
                    "status", "updated_at")
    search_fields = ("location_id", "location_name")
    list_filter = ("status",)
    exclude = ("access_token", "refresh_token")
    readonly_fields = ("installed_at", "updated_at")
