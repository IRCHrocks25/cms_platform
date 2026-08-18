"""Pages are created from pasted HTML (each gets its own dedicated Template)
and their HTML is editable afterwards via an agency-only "Edit HTML source" action.
No template picker; editing a page's HTML never touches another page.
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Page, Template, Tenant

_HERO = (
    "<section data-section='hero'>"
    "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
)
_CTA = (
    "<section data-section='cta'>"
    "<a data-edit='cta.button' data-type='link' href='/'>Go</a></section>"
)
_IGNORED = (
    "<section data-section='cta'>"
    "<a data-edit='cta.button' data-type='link' href='/'>Go</a>"
    "<p data-edit='wrong.copy'>Wrong prefix</p>"
    "</section>"
    "<p data-edit='orphan.copy'>No section</p>"
)


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class PageCreateFromHtmlTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="x", is_staff=True)
        self.home_tpl = Template.objects.create(name="Home", html_source="<h1>Home</h1>")
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.home_tpl, owner=self.staff,
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse("dashboard:page_create", args=[self.tenant.pk])

    def test_pasted_html_creates_page_with_its_own_template(self):
        r = self.client.post(
            self.url, {"title": "About", "slug": "about", "html_source": _HERO}
        )
        self.assertEqual(r.status_code, 302)
        page = self.tenant.pages.get(slug="about")
        # Its own dedicated template, not the home template.
        self.assertNotEqual(page.template_id, self.tenant.template_id)
        self.assertEqual(page.template.html_source, _HERO)
        # Schema was built on save so the editor has fields immediately.
        ids = [s["id"] for s in page.template.schema.get("sections", [])]
        self.assertIn("hero", ids)

    def test_blank_html_is_rejected(self):
        self.client.post(
            self.url, {"title": "About", "slug": "about", "html_source": "   "}
        )
        self.assertFalse(self.tenant.pages.filter(slug="about").exists())

    def test_page_list_marks_new_page_save_row_as_sticky(self):
        response = self.client.get(
            reverse("dashboard:page_list", args=[self.tenant.pk])
        )

        self.assertContains(response, 'class="row-end source-form-actions"', html=False)

    def test_create_warns_when_submitted_markers_are_ignored(self):
        response = self.client.post(
            self.url,
            {"title": "About", "slug": "about", "html_source": _IGNORED},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "submitted editable markers were not added")
        self.assertContains(response, "wrong.copy")
        self.assertContains(response, "orphan.copy")


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class PageEditHtmlTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="x", is_staff=True)
        self.home_tpl = Template.objects.create(name="Home", html_source="<h1>Home</h1>")
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.home_tpl, owner=self.staff,
        )
        self.page_tpl = Template.objects.create(name="Acme — About", html_source=_HERO)
        self.page = Page.objects.create(
            tenant=self.tenant, template=self.page_tpl, title="About", slug="about",
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse("dashboard:page_edit_html", args=[self.tenant.pk, self.page.pk])

    def test_get_shows_current_html(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "data-section")

    def test_get_marks_save_row_as_sticky(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'class="row-end source-form-actions"', html=False)

    def test_get_uses_shared_annotation_error_controls(self):
        r = self.client.get(self.url)

        self.assertContains(r, 'id="compare-loading-retry"', html=False)
        self.assertContains(r, 'id="compare-loading-close"', html=False)
        self.assertContains(
            r,
            "Applying this result will produce a template with no editable fields.",
        )

    def test_post_updates_html_and_rebuilds_schema(self):
        r = self.client.post(self.url, {"html_source": _CTA})
        self.assertEqual(r.status_code, 302)
        self.page_tpl.refresh_from_db()
        self.assertEqual(self.page_tpl.html_source, _CTA)
        ids = [s["id"] for s in self.page_tpl.schema.get("sections", [])]
        self.assertIn("cta", ids)
        self.assertNotIn("hero", ids)

    def test_update_warns_when_submitted_markers_are_ignored(self):
        response = self.client.post(
            self.url, {"html_source": _IGNORED}, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "submitted editable markers were not added")
        self.assertContains(response, "wrong.copy")
        self.assertContains(response, "orphan.copy")

    def test_editing_one_page_does_not_touch_the_home_template(self):
        self.client.post(self.url, {"html_source": _CTA})
        self.home_tpl.refresh_from_db()
        self.assertEqual(self.home_tpl.html_source, "<h1>Home</h1>")

    def test_requires_staff(self):
        self.client.logout()
        r = self.client.post(self.url, {"html_source": _CTA})
        self.assertIn(r.status_code, (302, 403))
