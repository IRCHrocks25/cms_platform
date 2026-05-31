import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant, TenantMembership
from core.renderer import render_site


def _make_template():
    return Template.objects.create(
        name="Bare",
        html_source=(
            "<!doctype html><html><head><title>Default</title></head><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title' data-type='text'>Hello</h1>"
            "</section></body></html>"
        ),
    )


@override_settings(
    TENANT_BASE_DOMAIN="localhost",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class SiteSettingsEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.member = User.objects.create_user("alice", password="x")
        cls.outsider = User.objects.create_user("eve", password="x")

        template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.staff,
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.member)

    def _agency_client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def _tenant_client(self, user=None):
        c = Client(HTTP_HOST="acme.localhost")
        c.force_login(user or self.member)
        return c

    # --- Agency-side endpoints ---

    def test_agency_get_settings_empty(self):
        c = self._agency_client()
        r = c.get(reverse("dashboard:tenant_site_settings", args=[self.tenant.pk]))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["settings"], {})

    def test_agency_save_settings(self):
        c = self._agency_client()
        payload = {
            "page_title": "My Site",
            "meta_description": "A great site.",
            "og_image_url": "https://example.com/img.jpg",
            "ga_measurement_id": "G-ABC123",
            "custom_head_script": "<script>console.log('hi')</script>",
        }
        r = c.post(
            reverse("dashboard:tenant_site_settings", args=[self.tenant.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.site_settings["page_title"], "My Site")
        self.assertEqual(self.tenant.site_settings["ga_measurement_id"], "G-ABC123")

    def test_agency_save_rejects_bad_ga_id(self):
        c = self._agency_client()
        r = c.post(
            reverse("dashboard:tenant_site_settings", args=[self.tenant.pk]),
            data=json.dumps({"ga_measurement_id": "INVALID"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertTrue(any("GA" in e for e in data["errors"]))

    def test_agency_save_rejects_long_title(self):
        c = self._agency_client()
        r = c.post(
            reverse("dashboard:tenant_site_settings", args=[self.tenant.pk]),
            data=json.dumps({"page_title": "x" * 201}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_agency_save_rejects_bad_og_url(self):
        c = self._agency_client()
        r = c.post(
            reverse("dashboard:tenant_site_settings", args=[self.tenant.pk]),
            data=json.dumps({"og_image_url": "not-a-url"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    # --- Tenant-side endpoints ---

    def test_tenant_member_get_settings(self):
        c = self._tenant_client()
        r = c.get(reverse("dashboard:tenant_site_settings_self"))
        self.assertEqual(r.status_code, 200)

    def test_tenant_member_save_settings(self):
        c = self._tenant_client()
        r = c.post(
            reverse("dashboard:tenant_site_settings_self"),
            data=json.dumps({"page_title": "Client Title", "ga_measurement_id": "UA-12345-1"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.site_settings["page_title"], "Client Title")
        self.assertEqual(self.tenant.site_settings["ga_measurement_id"], "UA-12345-1")

    def test_outsider_cannot_access_tenant_settings(self):
        c = self._tenant_client(user=self.outsider)
        r = c.get(reverse("dashboard:tenant_site_settings_self"))
        self.assertEqual(r.status_code, 403)

    def test_non_staff_cannot_access_agency_settings(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.member)
        r = c.get(reverse("dashboard:tenant_site_settings", args=[self.tenant.pk]))
        self.assertEqual(r.status_code, 403)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class SiteSettingsRenderTests(TestCase):
    def test_title_injection(self):
        html = (
            "<!doctype html><html><head><title>Old</title></head>"
            "<body><p>Hi</p></body></html>"
        )
        result = render_site(html, {}, site_settings={"page_title": "New Title"})
        self.assertIn("<title>New Title</title>", result)
        self.assertNotIn("Old", result)

    def test_meta_description_injection(self):
        html = "<!doctype html><html><head></head><body></body></html>"
        result = render_site(html, {}, site_settings={"meta_description": "A desc"})
        self.assertIn('name="description"', result)
        self.assertIn('content="A desc"', result)

    def test_ga_snippet_injection(self):
        html = "<!doctype html><html><head></head><body></body></html>"
        result = render_site(html, {}, site_settings={"ga_measurement_id": "G-TEST123"})
        self.assertIn("googletagmanager.com/gtag/js?id=G-TEST123", result)
        self.assertIn("gtag('config','G-TEST123')", result)

    def test_og_tags_injection(self):
        html = "<!doctype html><html><head></head><body></body></html>"
        result = render_site(
            html, {},
            site_settings={"og_image_url": "https://img.example.com/photo.jpg"},
        )
        self.assertIn('property="og:image"', result)
        self.assertIn("https://img.example.com/photo.jpg", result)

    def test_custom_script_injection(self):
        html = "<!doctype html><html><head></head><body></body></html>"
        script = '<script>alert("hello")</script>'
        result = render_site(html, {}, site_settings={"custom_head_script": script})
        self.assertIn(script, result)

    def test_empty_settings_no_injection(self):
        html = "<!doctype html><html><head><title>Keep</title></head><body></body></html>"
        result = render_site(html, {}, site_settings={})
        self.assertIn("<title>Keep</title>", result)
        self.assertNotIn("googletagmanager", result)

    def test_preview_mode_skips_settings(self):
        html = "<!doctype html><html><head></head><body></body></html>"
        result = render_site(
            html, {},
            preview=True,
            site_settings={"page_title": "Should Not Appear", "ga_measurement_id": "G-X"},
        )
        self.assertNotIn("Should Not Appear", result)
        self.assertNotIn("googletagmanager", result)

    def test_invalid_ga_id_not_injected(self):
        html = "<!doctype html><html><head></head><body></body></html>"
        result = render_site(html, {}, site_settings={"ga_measurement_id": "INVALID"})
        self.assertNotIn("googletagmanager", result)
