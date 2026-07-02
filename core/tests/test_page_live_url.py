"""The agency-surface "live" link for an inner page must point at the tenant's
own host (`<sub>.<base>/<slug>/`), not the agency apex fallback
(`sites.katek.app/site/<sub>/<slug>/`).

Regression: clicking "view page" from the agency dashboard opened the page on
the apex host instead of the client's real subdomain.
"""
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings

from core.models import Page, Template, Tenant
from core.urls_helpers import tenant_public_url
from dashboard.views import _page_row_urls


@override_settings(TENANT_BASE_DOMAIN="sites.katek.app")
class AgencyPageLiveUrlTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", is_staff=True)
        self.template = Template.objects.create(
            name="Base", html_source="<section data-section='hero'>"
            "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>",
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.template, owner=self.staff,
        )
        self.page = Page.objects.create(
            tenant=self.tenant, template=self.template, title="About", slug="about",
        )
        self.request = RequestFactory(HTTP_HOST="sites.katek.app").get("/dashboard/")

    def test_agency_live_url_is_on_tenant_host_not_apex(self):
        urls = _page_row_urls(self.request, "agency", self.tenant, self.page)
        self.assertNotIn("/site/", urls["live"])
        self.assertIn("acme.sites.katek.app", urls["live"])
        self.assertTrue(urls["live"].endswith("/about/"))
        # Ties it to the canonical helper the home page already uses.
        self.assertEqual(
            urls["live"], f"{tenant_public_url(self.request, self.tenant)}about/"
        )

    def test_tenant_scope_live_url_stays_relative(self):
        # Client editing on their own host: a relative slug link is correct.
        urls = _page_row_urls(self.request, "tenant", self.tenant, self.page)
        self.assertEqual(urls["live"], "/about/")
