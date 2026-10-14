"""Typography tokens driven from the Design tab in a real browser (CMS-74).

Needs the optional ``playwright`` package and its Chromium build; skipped when
either is missing.
"""
import os
import time
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import OperationalError
from django.test import Client, override_settings

from core.models import Template, Tenant
from core.services.blocks import upgrade_shell_chrome_slots
from core.tests.test_typography_tokens import IAM_TPL

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

FONT_OF = "sel => getComputedStyle(document.querySelector(sel)).fontFamily"


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
class TypographyTokensBrowserTests(StaticLiveServerTestCase):
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
            name="IAM", html_source=IAM_TPL, editing_mode=Template.EDITING_EDITABLE,
        )
        self.tenant = Tenant.objects.create(
            name="IAM", subdomain="iam", template=tpl, owner=staff, is_published=True,
        )
        client = Client()
        client.force_login(staff)
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.context.add_cookies([{
            "name": "sessionid",
            "value": client.cookies["sessionid"].value,
            "url": self.live_server_url,
        }])
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def open_design_tab(self):
        self.page.goto(f"{self.live_server_url}/dashboard/sites/{self.tenant.pk}/edit/")
        self.page.wait_for_selector("#preview-loading", state="hidden")
        self.page.click('[data-tab="brand"]')
        self.page.wait_for_selector('[data-global-bind="fontFamily"]', state="visible")

    def preview_font(self, selector, contains, *, absent=False):
        frame = self.page.frame_locator("#preview-frame")
        deadline = time.monotonic() + 10
        while True:
            value = frame.locator("body").evaluate(f"(_, s) => ({FONT_OF})(s)", selector)
            if (contains not in value) if absent else (contains in value):
                return value
            if time.monotonic() > deadline:
                self.fail(f"{selector} font is {value!r}, expected {contains!r}")
            time.sleep(0.2)

    def saved_global(self, expected):
        deadline = time.monotonic() + 10
        current = None
        while True:
            try:
                self.tenant.refresh_from_db()
            except OperationalError:
                pass  # the live server holds the SQLite lock mid-save
            else:
                current = (self.tenant.content or {}).get("_global")
                if current == expected:
                    return
            if time.monotonic() > deadline:
                self.fail(f"_global never saved as {expected}: {current}")
            time.sleep(0.2)

    def public_fonts(self, width):
        page = self.browser.new_page(viewport={"width": width, "height": 900})
        try:
            page.goto(f"{self.live_server_url}/site/iam/")
            return {
                sel: page.evaluate(FONT_OF, sel)
                for sel in ("body", ".btn", ".h-title", ".sec-h", ".tag")
            }
        finally:
            page.close()

    def test_design_fonts_reach_every_token_use_in_preview_and_public_site(self):
        self.open_design_tab()
        self.preview_font(".sec-h", "Bebas Neue")

        self.page.select_option('[data-global-bind="fontFamily"]', "Poppins")
        self.page.select_option('[data-global-bind="headingFamily"]', "Oswald")
        self.preview_font(".btn", "Poppins")
        self.preview_font(".sec-h", "Oswald")
        self.preview_font(".h-title", "Oswald")
        self.preview_font(".tag", "Inter")
        self.saved_global({"fontFamily": "Poppins", "headingFamily": "Oswald"})

        self.open_design_tab()
        self.assertEqual(self.page.input_value('[data-global-bind="fontFamily"]'), "Poppins")
        self.assertEqual(self.page.input_value('[data-global-bind="headingFamily"]'), "Oswald")
        self.preview_font(".btn", "Poppins")
        self.preview_font(".sec-h", "Oswald")

        for width in (1280, 390):
            with self.subTest(width=width):
                fonts = self.public_fonts(width)
                self.assertIn("Poppins", fonts["body"])
                self.assertIn("Poppins", fonts[".btn"])
                self.assertIn("Oswald", fonts[".h-title"])
                self.assertIn("Oswald", fonts[".sec-h"])
                self.assertIn("Inter", fonts[".tag"])

    def test_block_editor_toolbar_opens_font_controls(self):
        shell = upgrade_shell_chrome_slots(
            "<!doctype html><html><head><title>T</title><style>"
            ":root{--disp:'Bebas Neue',sans-serif;--body:'Inter',sans-serif}"
            "body{font-family:var(--body)} .ft-h{font-family:var(--disp)}"
            "</style></head><body>"
            '<header data-section="nav" data-group="Header">'
            '<span data-edit="nav.brand" data-type="text">Brand</span></header>'
            '<main data-region="main"></main>'
            '<footer data-section="footer" data-group="Footer">'
            '<h3 class="ft-h" data-edit="footer.title" data-type="text">Talk to us</h3>'
            '<p class="ft-p" data-edit="footer.name" data-type="text">Co</p></footer>'
            "</body></html>"
        )
        self.tenant.template.html_source = shell
        self.tenant.template.save()
        self.tenant.content = {"regions": {"main": []}}
        self.tenant.save()

        self.page.goto(f"{self.live_server_url}/dashboard/sites/{self.tenant.pk}/edit/")
        self.page.wait_for_selector("#preview-loading", state="hidden")
        self.page.click('[data-chrome-drawer="_global"]')
        self.page.wait_for_selector('[data-global-bind="headingFamily"]', state="visible")

        self.page.select_option('[data-global-bind="headingFamily"]', "Oswald")
        self.page.select_option('[data-global-bind="fontFamily"]', "Poppins")
        self.preview_font(".ft-h", "Oswald")
        self.preview_font(".ft-p", "Poppins")
        self.saved_global({"headingFamily": "Oswald", "fontFamily": "Poppins"})

    def test_resetting_to_default_restores_template_font_live(self):
        self.tenant.content = {"_global": {"headingFamily": "Oswald"}}
        self.tenant.save()
        self.open_design_tab()
        self.preview_font(".sec-h", "Oswald")

        self.page.select_option('[data-global-bind="headingFamily"]', "")
        self.preview_font(".sec-h", "Bebas Neue")
        self.preview_font(".sec-h", "Oswald", absent=True)
        self.saved_global({})
