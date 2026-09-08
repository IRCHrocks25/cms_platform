"""A tenant host must serve that tenant's own `404` page for an unknown slug.

Without handler404 wired to it, a client site that ships a designed 404 page
still answered with Django's bare "Not Found" body, so the page was dead
weight. Found on thenolangroup.com, whose 404 page exists as Page(slug="404").
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from core.models import Page, Template, Tenant


BASE = "sites.katek.app"
HERO = (
    "<section data-section='hero'>"
    "<h1 data-edit='hero.title' data-type='text'>{}</h1></section>"
)


@override_settings(TENANT_BASE_DOMAIN=BASE, DEBUG=False, ALLOWED_HOSTS=["*"])
class TenantNotFoundPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("op", is_staff=True)
        self.home_tpl = Template.objects.create(
            name="Home", html_source=HERO.format("Home")
        )
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=self.home_tpl,
            owner=self.owner,
            is_published=True,
        )
        self.host = f"acme.{BASE}"
        self.client = Client()

    def _add_404_page(self, *, published=True):
        tpl = Template.objects.create(
            name="Not found", html_source=HERO.format("Lost your way?")
        )
        return Page.objects.create(
            tenant=self.tenant,
            template=tpl,
            title="404",
            slug="404",
            is_published=published,
        )

    def test_unknown_slug_serves_the_tenant_404_page(self):
        self._add_404_page()
        r = self.client.get("/no-such-page/", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 404)
        self.assertIn("Lost your way?", r.content.decode())

    def test_status_is_404_not_200(self):
        """A soft 404 keeps search engines indexing dead URLs."""
        self._add_404_page()
        r = self.client.get("/no-such-page/", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 404)

    def test_falls_back_when_the_tenant_has_no_404_page(self):
        r = self.client.get("/no-such-page/", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Lost your way?", r.content.decode())

    def test_unpublished_404_page_is_not_served(self):
        self._add_404_page(published=False)
        r = self.client.get("/no-such-page/", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Lost your way?", r.content.decode())

    def test_unpublished_site_does_not_leak_its_404_page(self):
        self.tenant.is_published = False
        self.tenant.save()
        self._add_404_page()
        r = self.client.get("/no-such-page/", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Lost your way?", r.content.decode())

    def test_agency_host_is_unaffected(self):
        """No tenant on the host: the default 404 body, never a client's page."""
        self._add_404_page()
        r = self.client.get("/no-such-page/", HTTP_HOST=BASE)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Lost your way?", r.content.decode())
