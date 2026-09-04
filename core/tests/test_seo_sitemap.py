"""CMS-57: generated /sitemap.xml on tenant hosts."""
import xml.etree.ElementTree as ET

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core.models import BlogPost, CustomDomain, Page, Template, Tenant
from core.urls_helpers import tenant_canonical_base_url

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _template(name="T"):
    return Template.objects.create(
        name=name,
        html_source=(
            "<!doctype html><html><head><title>T</title></head><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            "</body></html>"
        ),
    )


def _locs(body: bytes) -> list[str]:
    root = ET.fromstring(body)
    assert root.tag == "{%s}urlset" % NS["sm"], root.tag
    return [u.findtext("sm:loc", namespaces=NS) for u in root.findall("sm:url", NS)]


@override_settings(TENANT_BASE_DOMAIN="sites.example.test", DEBUG=False, STORAGES=STATIC)
class SitemapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.tpl = _template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=cls.tpl, owner=cls.staff,
            is_published=True,
        )
        Page.objects.create(
            tenant=cls.tenant, template=_template("About"), title="About",
            slug="about", is_published=True,
        )
        Page.objects.create(
            tenant=cls.tenant, template=_template("Draft"), title="Draft",
            slug="draft", is_published=False,
        )

    def _get(self, host="acme.sites.example.test"):
        return Client(HTTP_HOST=host).get("/sitemap.xml")

    def test_serves_valid_sitemap_with_home_and_published_pages(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/xml")
        locs = _locs(resp.content)
        self.assertEqual(
            locs,
            [
                "https://acme.sites.example.test/",
                "https://acme.sites.example.test/about/",
            ],
        )
        self.assertNotIn("https://acme.sites.example.test/draft/", locs)

    def test_lastmod_is_the_page_updated_date(self):
        resp = self._get()
        root = ET.fromstring(resp.content)
        about = [
            u for u in root.findall("sm:url", NS)
            if u.findtext("sm:loc", namespaces=NS).endswith("/about/")
        ][0]
        page = Page.objects.get(slug="about")
        self.assertEqual(
            about.findtext("sm:lastmod", namespaces=NS),
            page.updated_at.date().isoformat(),
        )

    def test_verified_custom_domain_is_the_canonical_host(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme-live.com", is_verified=True
        )
        # Served on the subdomain host, the locs still point at the custom domain.
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            all(loc.startswith("https://www.acme-live.com/") for loc in _locs(resp.content)),
            _locs(resp.content),
        )
        # And the custom-domain host itself resolves and serves the same map.
        resp2 = self._get(host="www.acme-live.com")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(_locs(resp2.content), _locs(resp.content))

    def test_unverified_custom_domain_is_ignored(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.pending.com", is_verified=False
        )
        self.tenant.custom_domain = "www.pending.com"
        self.tenant.save(update_fields=["custom_domain"])
        locs = _locs(self._get().content)
        self.assertTrue(all(loc.startswith("https://acme.sites.example.test/") for loc in locs), locs)

    def test_blog_entries_appear_only_when_a_post_is_published(self):
        self.assertNotIn("https://acme.sites.example.test/blog/", _locs(self._get().content))
        BlogPost.objects.create(
            tenant=self.tenant, title="Hello", slug="hello",
            status=BlogPost.STATUS_PUBLISHED,
        )
        BlogPost.objects.create(
            tenant=self.tenant, title="Secret", slug="secret",
            status=BlogPost.STATUS_DRAFT,
        )
        locs = _locs(self._get().content)
        self.assertIn("https://acme.sites.example.test/blog/", locs)
        self.assertIn("https://acme.sites.example.test/blog/hello/", locs)
        self.assertNotIn("https://acme.sites.example.test/blog/secret/", locs)

    def test_unpublished_tenant_returns_404_even_for_staff(self):
        self.tenant.is_published = False
        self.tenant.save(update_fields=["is_published"])
        c = Client(HTTP_HOST="acme.sites.example.test")
        c.force_login(self.staff)
        self.assertEqual(c.get("/sitemap.xml").status_code, 404)

    def test_unknown_host_and_agency_host_return_404(self):
        self.assertEqual(self._get(host="nobody.sites.example.test").status_code, 404)
        self.assertEqual(self._get(host="sites.example.test").status_code, 404)

    def test_loc_is_xml_escaped(self):
        Page.objects.create(
            tenant=self.tenant, template=_template("Amp"), title="Q&A",
            slug="q-and-a", is_published=True,
        )
        body = self._get().content.decode()
        self.assertIn("/q-and-a/", body)
        # No unescaped ampersand anywhere in the document.
        ET.fromstring(body)


@override_settings(TENANT_BASE_DOMAIN="sites.example.test", DEBUG=False)
class CanonicalBaseUrlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        owner = User.objects.create_user("o", password="x")
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=_template(), owner=owner
        )

    def test_subdomain_when_no_verified_domain(self):
        self.assertEqual(
            tenant_canonical_base_url(self.tenant), "https://acme.sites.example.test/"
        )

    def test_first_verified_domain_wins(self):
        CustomDomain.objects.create(tenant=self.tenant, domain="b.com", is_verified=False)
        CustomDomain.objects.create(tenant=self.tenant, domain="a.com", is_verified=True)
        CustomDomain.objects.create(tenant=self.tenant, domain="c.com", is_verified=True)
        self.assertEqual(tenant_canonical_base_url(self.tenant), "https://a.com/")
