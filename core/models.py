import re
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from .parser import build_schema


BLOG_TEMPLATE_MINIMAL = "minimal"
BLOG_TEMPLATE_MAGAZINE = "magazine"
BLOG_TEMPLATE_CARDS = "cards"
BLOG_TEMPLATE_CHOICES = [
    (BLOG_TEMPLATE_MINIMAL, "Minimal Reading"),
    (BLOG_TEMPLATE_MAGAZINE, "Magazine"),
    (BLOG_TEMPLATE_CARDS, "Card Grid"),
]
BLOG_TEMPLATE_IDS = {c[0] for c in BLOG_TEMPLATE_CHOICES}
DEFAULT_BLOG_TEMPLATE = BLOG_TEMPLATE_MINIMAL

# Homepage "featured posts" strip layouts. Chosen independently of the blog
# index/detail style so the strip can be tuned to match the site's homepage.
# Each maps to a self-contained template under templates/blog/strips/.
BLOG_STRIP_CARDS = "cards"
BLOG_STRIP_OVERLAY = "overlay"
BLOG_STRIP_SPOTLIGHT = "spotlight"
BLOG_STRIP_LIST = "list"
BLOG_STRIP_CHOICES = [
    (BLOG_STRIP_CARDS, "Cards — cover image, title & excerpt"),
    (BLOG_STRIP_OVERLAY, "Overlay — title on a full-bleed image"),
    (BLOG_STRIP_SPOTLIGHT, "Spotlight — one lead post + a list"),
    (BLOG_STRIP_LIST, "Minimal list — clean, text-forward"),
]
BLOG_STRIP_IDS = {c[0] for c in BLOG_STRIP_CHOICES}
DEFAULT_BLOG_STRIP = BLOG_STRIP_CARDS


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
    site_settings = models.JSONField(default=dict, blank=True)
    blog_settings = models.JSONField(default=dict, blank=True)
    is_published = models.BooleanField(default=False)

    ghl_location_id = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Maps a GHL sub-account (location.id) to this tenant. When set, the "
            "/embed/ auto-login route accepts visits from that location."
        ),
    )

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


# Top-level paths on a tenant host that a Page slug must not shadow. A page
# addressed at `/<slug>/` shares the URL namespace with these, so we refuse them.
RESERVED_PAGE_SLUGS = {
    "blog", "dashboard", "login", "logout", "admin", "static", "media",
    "site", "password-reset", "reset", "debug-headers", "api",
}


class Page(models.Model):
    """An additional annotated page for a tenant (About, Services, ...).

    The tenant's *home* page stays on ``Tenant.template`` / ``Tenant.content``;
    a Page is any extra page, served at ``/<slug>/`` on the same host. Each
    Page is its own annotated ``Template`` + content blob and publishes
    independently of the home page. Rendering reuses ``render_site`` /
    ``merge_with_defaults`` unchanged — a Page exposes the same
    ``template`` / ``content`` / ``is_published`` shape the editor already
    drives the home page with.
    """

    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.CASCADE, related_name="pages"
    )
    template = models.ForeignKey(
        Template, on_delete=models.PROTECT, related_name="pages"
    )

    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    content = models.JSONField(default=dict, blank=True)

    is_published = models.BooleanField(default=False)
    show_in_nav = models.BooleanField(default=True)
    nav_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nav_order", "title"]
        unique_together = [("tenant", "slug")]

    def __str__(self):
        return f"{self.title} ({self.tenant.subdomain}/{self.slug})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:80]
        super().save(*args, **kwargs)

    def user_can_edit(self, user) -> bool:
        return self.tenant.user_can_edit(user)


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
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.domain} → {self.tenant.subdomain}"


class MediaAsset(models.Model):
    RESOURCE_IMAGE = "image"
    RESOURCE_VIDEO = "video"

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="assets"
    )
    # Legacy local storage — kept so already-stored /media/ assets still resolve.
    # New uploads go to Cloudinary and leave this blank.
    file = models.ImageField(upload_to="tenants/%Y/%m/", blank=True, null=True)
    original_name = models.CharField(max_length=240, blank=True)

    # Cloudinary-backed fields (new uploads).
    resource_type = models.CharField(max_length=10, default=RESOURCE_IMAGE)
    public_id = models.CharField(max_length=255, blank=True, default="")
    secure_url = models.URLField(max_length=600, blank=True, default="")
    bytes = models.PositiveBigIntegerField(default=0)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    @property
    def url(self) -> str:
        """Best available URL: Cloudinary first, then legacy local file."""
        if self.secure_url:
            return self.secure_url
        if self.file:
            return self.file.url
        return ""


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


def _unique_blog_slug(tenant, base: str, *, instance=None) -> str:
    """A slug unique within ``tenant``, derived from ``base``, suffixed on
    collision (``-2``, ``-3`` …). Excludes ``instance`` from the check so
    re-saving a post keeps its slug."""
    base = slugify(base or "")[:200].strip("-") or "post"
    candidate = base
    suffix = 2
    while True:
        qs = BlogPost.objects.filter(tenant=tenant, slug=candidate)
        if instance is not None and instance.pk:
            qs = qs.exclude(pk=instance.pk)
        if not qs.exists():
            return candidate
        token = f"-{suffix}"
        candidate = f"{base[: 200 - len(token)].rstrip('-')}{token}"
        suffix += 1


class BlogPost(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
    ]

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="blog_posts"
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, blank=True)

    cover_image = models.CharField(max_length=500, blank=True, default="")
    excerpt = models.TextField(blank=True, default="")
    body = models.TextField(blank=True, default="")
    author = models.CharField(max_length=120, blank=True, default="")

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT
    )
    publish_date = models.DateTimeField(null=True, blank=True)

    seo_title = models.CharField(max_length=200, blank=True, default="")
    seo_description = models.CharField(max_length=500, blank=True, default="")
    og_image_url = models.CharField(max_length=500, blank=True, default="")

    template = models.CharField(
        max_length=20, choices=BLOG_TEMPLATE_CHOICES, blank=True, default=""
    )
    featured = models.BooleanField(default=False)
    featured_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publish_date", "-created_at"]
        unique_together = [("tenant", "slug")]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "featured"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.tenant.subdomain})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_blog_slug(self.tenant, self.title, instance=self)
        # Invariant: a published post MUST have a publish_date. The public
        # queries (`published_posts`, `is_live`) require publish_date IS NOT
        # NULL for stable ordering/display, so a published post without one
        # would never appear in the blog index, post page, or homepage strip.
        # Stamping it here (not only in the dashboard form) keeps the rule true
        # for every write path: admin, the featured-star toggle, reorder, data
        # scripts. An explicit date the caller set is left untouched.
        if self.status == self.STATUS_PUBLISHED and self.publish_date is None:
            self.publish_date = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "publish_date" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["publish_date"]
        super().save(*args, **kwargs)

    @property
    def is_live(self) -> bool:
        # Visibility is status-based (see blog_render.published_posts): a
        # published post is live. We don't gate on `publish_date <= now`, since
        # the timezone-naive datetime-local input routinely stores a "publish
        # now" a few hours in the future, which would wrongly flag a published
        # post as a draft preview on its detail page.
        return self.status == self.STATUS_PUBLISHED and self.publish_date is not None

    def effective_template(self, site_default: str) -> str:
        if self.template in BLOG_TEMPLATE_IDS:
            return self.template
        return site_default

    def display_excerpt(self, length: int = 200) -> str:
        if self.excerpt.strip():
            return self.excerpt.strip()
        text = re.sub(r"<[^>]+>", " ", self.body or "")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= length:
            return text
        return text[:length].rsplit(" ", 1)[0].rstrip() + "…"

    def resolved_seo(self, site_settings: dict | None = None) -> dict:
        """Per-post head settings, layered over the site's settings so blog
        pages keep the site GA snippet + custom head script."""
        merged = dict(site_settings or {})
        merged["page_title"] = (self.seo_title or self.title).strip()
        merged["meta_description"] = (
            self.seo_description or self.display_excerpt()
        ).strip()
        og = (self.og_image_url or self.cover_image).strip()
        if og:
            merged["og_image_url"] = og
        return merged


class AnnotationJob(models.Model):
    """Background job for AI HTML annotation.

    The OpenAI annotation call can run 1–2 minutes on large pages (5000+ lines).
    Running it synchronously inside the web request meant a slow call got killed
    by the Gunicorn worker timeout and the proxy returned an HTML 502 page (the
    dashboard fetch then choked on ``<!DOCTYPE`` → "Unexpected token '<'").

    Instead, ``dashboard.views.template_annotate`` creates one of these rows and
    a worker thread runs ``annotate_html()`` with NO web-request clock; the
    browser polls ``template_annotate_status`` until the row is terminal. The
    request returns in milliseconds, so nothing upstream can ever time it out.

    Rows are transient — they hold the result only until the browser fetches it,
    and old rows are swept on the next submit (see ``template_annotate``).
    """

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_ERROR, "Error"),
    ]

    # UUID PK doubles as an opaque, non-enumerable polling token.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="annotation_jobs",
    )
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    result_html = models.TextField(blank=True, default="")
    sections = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"AnnotationJob {self.id} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in (self.STATUS_DONE, self.STATUS_ERROR)
