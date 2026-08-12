"""CMS-40 — platform /privacy/ and /terms/ are host-scoped to the agency host.

On a tenant subdomain or verified custom domain those paths must fall through
to the tenant Page renderer so clients can publish real privacy/terms pages.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core.models import (
    CustomDomain,
    Page,
    RESERVED_PAGE_SLUGS,
    Template,
    Tenant,
)

User = get_user_model()

_PAGE_HTML = (
    "<section data-section='legal'>"
    "<h1 data-edit='legal.title' data-type='text'>PAGE_MARKER</h1>"
    "</section>"
)


def _published_page(tenant, *, slug, title, marker):
    tpl = Template.objects.create(
        name=f"{tenant.name} — {title}",
        html_source=_PAGE_HTML.replace("PAGE_MARKER", marker),
    )
    return Page.objects.create(
        tenant=tenant,
        template=tpl,
        title=title,
        slug=slug,
        is_published=True,
    )


_STATIC = {
    "STORAGES": {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
}


@override_settings(
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
    **_STATIC,
)
class PlatformLegalPagesStillServeOnAgencyHostTests(TestCase):
    def test_privacy_and_terms_on_platform_host(self):
        c = Client()
        for path, needle in (
            ("/privacy/", "Privacy Policy"),
            ("/terms/", "Terms of Service"),
        ):
            with self.subTest(path=path):
                r = c.get(path, HTTP_HOST="sites.katek.app")
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, needle)
                # Platform legal copy, not a missing-tenant 404.
                self.assertContains(r, "sites.katek.app")

                head = c.head(path, HTTP_HOST="sites.katek.app")
                self.assertEqual(head.status_code, 200)


@override_settings(
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
    **_STATIC,
)
class TenantPrivacyTermsPagesReachableTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user("owner", password="x")
        self.home = Template.objects.create(name="Home", html_source="<h1>Home</h1>")
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=self.home,
            owner=owner,
            is_published=True,
        )

    def test_privacy_and_terms_slugs_are_not_reserved(self):
        self.assertNotIn("privacy", RESERVED_PAGE_SLUGS)
        self.assertNotIn("terms", RESERVED_PAGE_SLUGS)

    def test_genuinely_reserved_slugs_remain(self):
        for slug in ("api", "mcp", "dashboard", "blog", "login", "admin"):
            self.assertIn(slug, RESERVED_PAGE_SLUGS, slug)

    def test_tenant_privacy_page_on_subdomain(self):
        _published_page(
            self.tenant, slug="privacy", title="Privacy", marker="ACME_PRIVACY"
        )
        r = Client().get("/privacy/", HTTP_HOST="acme.sites.katek.app")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ACME_PRIVACY")
        self.assertNotContains(r, "Privacy Policy — sites.katek.app")

    def test_tenant_terms_page_on_subdomain(self):
        _published_page(
            self.tenant, slug="terms", title="Terms", marker="ACME_TERMS"
        )
        r = Client().get("/terms/", HTTP_HOST="acme.sites.katek.app")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ACME_TERMS")
        self.assertNotContains(r, "Terms of Service — sites.katek.app")

    def test_head_for_tenant_legal_pages_matches_normal_page(self):
        for slug, title, marker in (
            ("about", "About", "ACME_ABOUT"),
            ("privacy", "Privacy", "ACME_PRIVACY"),
            ("terms", "Terms", "ACME_TERMS"),
        ):
            _published_page(self.tenant, slug=slug, title=title, marker=marker)

        c = Client()
        for path in ("/about/", "/privacy/", "/terms/"):
            with self.subTest(path=path):
                self.assertEqual(
                    c.head(path, HTTP_HOST="acme.sites.katek.app").status_code, 200
                )

    def test_tenant_privacy_on_verified_custom_domain(self):
        _published_page(
            self.tenant, slug="privacy", title="Privacy", marker="CUSTOM_PRIVACY"
        )
        CustomDomain.objects.create(
            tenant=self.tenant,
            domain="www.acmebrand.com",
            is_verified=True,
        )
        r = Client().get("/privacy/", HTTP_HOST="www.acmebrand.com")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CUSTOM_PRIVACY")

    def test_tenant_privacy_via_x_forwarded_host(self):
        """Middleware prefers X-Forwarded-Host for custom-domain lookup when a
        proxy rewrites Host — the legal-page guard must agree."""
        _published_page(
            self.tenant, slug="privacy", title="Privacy", marker="FWD_PRIVACY"
        )
        CustomDomain.objects.create(
            tenant=self.tenant,
            domain="www.acmebrand.com",
            is_verified=True,
        )
        r = Client().get(
            "/privacy/",
            HTTP_HOST="proxy.internal",
            HTTP_X_FORWARDED_HOST="www.acmebrand.com",
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "FWD_PRIVACY")

    def test_suffixed_privacy_policy_still_works(self):
        # Existing sites used privacy-policy to dodge the collision — do not
        # migrate them; both slugs must keep working.
        _published_page(
            self.tenant,
            slug="privacy-policy",
            title="Privacy Policy",
            marker="SUFFIXED_PRIVACY",
        )
        r = Client().get("/privacy-policy/", HTTP_HOST="acme.sites.katek.app")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "SUFFIXED_PRIVACY")
