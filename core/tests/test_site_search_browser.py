"""Sites search updates without submitting or losing input focus."""
import os
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from core.models import Template, Tenant

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@override_settings(
    TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"],
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class SiteSearchBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:
            cls.pw.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"chromium unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        super().tearDownClass()

    def test_typing_clearing_status_and_replaced_menus(self):
        staff = get_user_model().objects.create_user("search-op", is_staff=True)
        template = Template.objects.create(name="Search", html_source="<p>Test</p>")
        for name, subdomain, published in [("Lisa Oates", "lisa", False), ("Jill Gentry", "jill", True)]:
            Tenant.objects.create(name=name, subdomain=subdomain, is_published=published, template=template, owner=staff)
        client = Client()
        client.force_login(staff)
        context = self.browser.new_context()
        self.addCleanup(context.close)
        context.add_cookies([{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": self.live_server_url}])
        page = context.new_page()
        page.goto(self.live_server_url + reverse("dashboard:tenant_list"))
        search = page.locator('[data-site-search] input[type="search"]')
        rows = page.locator("[data-site-results] tbody tr")
        expect(rows).to_have_count(2)
        search.fill("LiSa")
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text("Lisa Oates")
        expect(search).to_be_focused()
        search.fill("no matches")
        expect(page.locator("[data-site-results]")).to_contain_text("No sites match")
        search.fill("")
        expect(rows).to_have_count(2)
        page.locator('[data-site-status] a').filter(has_text="Published").click()
        expect(rows).to_have_count(1)
        search.fill("lisa")
        expect(page.locator("[data-site-results]")).to_contain_text("No sites match")
        search.fill("")
        expect(rows).to_have_count(1)
        expect(rows).to_contain_text("Jill Gentry")
        page.locator("[data-site-clear]").click()
        expect(rows).to_have_count(2)
        page.get_by_role("button", name="More actions for Lisa Oates").click()
        expect(page.locator('[role="menu"]:visible')).to_have_count(1)
