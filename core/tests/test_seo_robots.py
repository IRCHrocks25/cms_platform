"""CMS-58: per-site /robots.txt on tenant hosts."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core.models import CustomDomain, Template, Tenant

STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _template():
    return Template.objects.create(
        name="T",
        html_source=(
            "<!doctype html><html><head><title>T</title></head><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            "</body></html>"
        ),
    )


@override_settings(TENANT_BASE_DOMAIN="sites.example.test", DEBUG=False, STORAGES=STATIC)
class RobotsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=_template(), owner=cls.staff,
            is_published=True,
        )

    def _get(self, host="acme.sites.example.test"):
        return Client(HTTP_HOST=host).get("/robots.txt")

    def test_published_site_allows_crawling_and_points_at_sitemap(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/plain; charset=utf-8")
        lines = resp.content.decode().splitlines()
        self.assertEqual(lines[0], "User-agent: *")
        self.assertIn("Allow: /", lines)
        self.assertIn("Disallow: /dashboard/", lines)
        self.assertIn("Disallow: /login/", lines)
        self.assertIn("Sitemap: https://acme.sites.example.test/sitemap.xml", lines)
        self.assertNotIn("Disallow: /", lines)

    def test_sitemap_line_uses_verified_custom_domain(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme-live.com", is_verified=True
        )
        body = self._get().content.decode()
        self.assertIn("Sitemap: https://www.acme-live.com/sitemap.xml", body)
        self.assertNotIn("acme.sites.example.test", body)

    def test_unpublished_site_disallows_everything(self):
        self.tenant.is_published = False
        self.tenant.save(update_fields=["is_published"])
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        lines = resp.content.decode().splitlines()
        self.assertEqual(lines, ["User-agent: *", "Disallow: /"])

    def test_unpublished_site_has_no_editor_bypass(self):
        self.tenant.is_published = False
        self.tenant.save(update_fields=["is_published"])
        c = Client(HTTP_HOST="acme.sites.example.test")
        c.force_login(self.staff)
        self.assertEqual(c.get("/robots.txt").content.decode().splitlines()[-1], "Disallow: /")

    def test_unknown_host_and_agency_host_return_404(self):
        self.assertEqual(self._get(host="nobody.sites.example.test").status_code, 404)
        self.assertEqual(self._get(host="sites.example.test").status_code, 404)
