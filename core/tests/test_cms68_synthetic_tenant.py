"""CMS-68 public rendering regression using a disposable synthetic tenant."""
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from core.models import Page, Template, Tenant
from core.tests.test_blocks import DESIGNED_NAV_SHELL


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class DesignedTenantHeaderTests(TestCase):
    def test_public_home_keeps_designed_navigation_with_published_legal_pages(self):
        owner = get_user_model().objects.create_user("synthetic-owner")
        template = Template.objects.create(name="Synthetic designed shell", html_source=DESIGNED_NAV_SHELL)
        tenant = Tenant.objects.create(name="Synthetic triage", subdomain="triage", owner=owner,
                                       template=template, is_published=True, content={"regions": {"main": []}})
        legal = Template.objects.create(name="Synthetic legal", html_source="<html><body><p>Legal fixture</p></body></html>")
        for slug in ("privacy-policy", "terms-and-conditions"):
            Page.objects.create(tenant=tenant, template=legal, title=slug, slug=slug,
                                is_published=True, show_in_nav=True)
        response = self.client.get("/", HTTP_HOST="triage.localhost")
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, "lxml")
        self.assertEqual([a.get_text() for a in soup.select("#navLinks a")],
                         ["Home", "About", "Free Guide Download"])
        self.assertIsNotNone(soup.select_one("#navLinks [data-fg-open]"))
        for slug in ("privacy-policy", "terms-and-conditions"):
            self.assertContains(self.client.get(f"/{slug}", HTTP_HOST="triage.localhost"), "Legal fixture")
