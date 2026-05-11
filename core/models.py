from django.conf import settings
from django.db import models
from django.utils.text import slugify

from .parser import build_schema


class Template(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.CharField(max_length=240, blank=True)

    html_source = models.TextField()
    schema = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        self.schema = build_schema(self.html_source)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Tenant(models.Model):
    name = models.CharField(max_length=120)
    subdomain = models.SlugField(max_length=80, unique=True)
    custom_domain = models.CharField(max_length=253, blank=True, default="")

    template = models.ForeignKey(
        Template, on_delete=models.PROTECT, related_name="tenants"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenants",
    )

    content = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.subdomain})"

    def user_can_edit(self, user) -> bool:
        if not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        return self.memberships.filter(user=user).exists()


class TenantMembership(models.Model):
    ROLE_OWNER = "owner"
    ROLE_EDITOR = "editor"
    ROLE_CHOICES = [
        (ROLE_OWNER, "Owner"),
        (ROLE_EDITOR, "Editor"),
    ]

    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_EDITOR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tenant", "user")]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"


class CustomDomain(models.Model):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="custom_domains"
    )
    domain = models.CharField(max_length=253, unique=True)
    cloudflare_hostname_id = models.CharField(max_length=255, blank=True, default="")
    ssl_txt_name = models.CharField(max_length=255, blank=True, default="")
    ssl_txt_value = models.CharField(max_length=255, blank=True, default="")
    pre_validation_txt_name = models.CharField(max_length=255, blank=True, default="")
    pre_validation_txt_value = models.CharField(max_length=255, blank=True, default="")
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.domain} → {self.tenant.subdomain}"


class MediaAsset(models.Model):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="assets"
    )
    file = models.ImageField(upload_to="tenants/%Y/%m/")
    original_name = models.CharField(max_length=240, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]


class ContentVersion(models.Model):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="versions"
    )
    snapshot = models.JSONField()
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-saved_at"]
