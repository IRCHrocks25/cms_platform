"""Header panel Custom URL links, driven in a real browser (CMS-70).

Needs the optional ``playwright`` package and its Chromium build; skipped when
either is missing.
"""
import os
import time
import unittest

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import OperationalError
from django.test import Client, override_settings

from core.models import Template, Tenant
from core.services.blocks import upgrade_shell_chrome_slots

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

SHELL = (
    "<!doctype html><html><head><title>T</title></head><body>"
    '<header data-section="nav" data-group="Header">'
    '<span data-edit="nav.brand" data-type="text">Brand</span></header>'
    '<main data-region="main"></main>'
    '<footer data-section="footer" data-group="Footer">'
    '<span data-edit="footer.name" data-type="text">Co</span></footer>'
    "</body></html>"
)
NAV_ID = "nav_abcd1234"
ROW = f'#cms-header-panel [data-header-row="{NAV_ID}"]'


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
@override_settings(
    TENANT_BASE_DOMAIN="localhost",
    ALLOWED_HOSTS=["*"],
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class HeaderCustomUrlBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        super().setUpClass()
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001; browser bundle not installed
            cls._pw.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f"chromium unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        super().tearDownClass()

    def setUp(self):
        staff = get_user_model().objects.create_user("op", password="x", is_staff=True)
        tpl = Template.objects.create(
            name="Shell", html_source=upgrade_shell_chrome_slots(SHELL)
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=tpl, owner=staff,
            is_published=True,
            content={
                "regions": {"main": []},
                "_header": {"menu": [{
                    "id": NAV_ID, "label": "About", "href": "/", "page_id": None,
                }]},
            },
        )
        client = Client()
        client.force_login(staff)
        self.context = self.browser.new_context()
        self.context.add_cookies([{
            "name": "sessionid",
            "value": client.cookies["sessionid"].value,
            "url": self.live_server_url,
        }])
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def open_header_panel(self):
        self.page.goto(f"{self.live_server_url}/dashboard/sites/{self.tenant.pk}/edit/")
        self.page.click('[data-chrome-drawer="nav"]')
        self.page.wait_for_selector(ROW)

    def saved_item(self, **expected):
        deadline = time.monotonic() + 10
        item = None
        while True:
            try:
                self.tenant.refresh_from_db()
            except OperationalError:
                pass  # the live server holds the SQLite lock mid-save
            else:
                item = self.tenant.content["_header"]["menu"][0]
                if all(item.get(k) == v for k, v in expected.items()):
                    return item
            if time.monotonic() > deadline:
                self.fail(f"menu item never saved as {expected}: {item}")
            time.sleep(0.2)

    def test_home_link_switches_to_custom_url_and_survives_reload(self):
        self.open_header_panel()
        self.page.select_option(f"{ROW} [data-header-page]", "__custom__")

        href = self.page.locator(f"{ROW} [data-header-href]")
        self.assertTrue(href.is_visible())
        self.assertTrue(href.evaluate("el => el === document.activeElement"))
        self.assertEqual(self.page.input_value(f"{ROW} [data-header-page]"), "__custom__")

        href.type("#about")
        self.saved_item(href="#about", custom=True, page_id=None)

        self.open_header_panel()
        self.assertEqual(self.page.input_value(f"{ROW} [data-header-page]"), "__custom__")
        self.assertTrue(self.page.locator(f"{ROW} [data-header-href]").is_visible())
        self.assertEqual(self.page.input_value(f"{ROW} [data-header-href]"), "#about")

        public = Client(HTTP_HOST="acme.localhost").get("/")
        self.assertEqual(public.status_code, 200)
        link = BeautifulSoup(public.content, "lxml").find(
            attrs={"data-header-link": NAV_ID}
        )
        self.assertIsNotNone(link)
        self.assertEqual(link.get("href"), "#about")

    def test_switching_back_to_a_page_hides_the_url_input(self):
        self.open_header_panel()
        self.page.select_option(f"{ROW} [data-header-page]", "__custom__")
        self.page.locator(f"{ROW} [data-header-href]").type("/#about")
        self.saved_item(href="/#about", custom=True)

        self.page.select_option(f"{ROW} [data-header-page]", "")
        self.assertFalse(self.page.locator(f"{ROW} [data-header-href]").is_visible())
        self.saved_item(href="/", custom=False)
