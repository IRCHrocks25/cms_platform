"""Tests for the agency-side ``tenant_template_swap`` endpoint that lets
the operator re-point ``Tenant.template`` at a different ``Template``.

The product promise: the agency picks a new template; the tenant's saved
content is left intact on the row (fields whose ``section.field`` id also
exists in the new template's schema keep rendering with the saved value;
the rest sits dormant and comes back on a swap-back). Nothing is deleted.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant


User = get_user_model()


def _make_template(name, html="<section data-section='hero' data-label='Hero'></section>"):
    return Template.objects.create(name=name, description="", html_source=html)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TenantTemplateSwapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.outsider = User.objects.create_user("eve", password="x")
        cls.template_a = _make_template("Template A")
        cls.template_b = _make_template("Template B")
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme",
            template=cls.template_a, owner=cls.staff,
        )

    def _agency_client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def _swap_url(self, pk):
        return reverse("dashboard:tenant_template_swap", args=[pk])

    def test_anonymous_request_redirects_to_login(self):
        c = Client(HTTP_HOST="localhost")
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": self.template_b.pk})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, self.template_a.pk)

    def test_non_staff_user_is_rejected(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.outsider)
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": self.template_b.pk})
        # Decorator returns 403 or redirects depending on context; either
        # way the swap must NOT happen.
        self.assertIn(response.status_code, (302, 403))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, self.template_a.pk)

    def test_get_method_not_allowed(self):
        c = self._agency_client()
        response = c.get(self._swap_url(self.tenant.pk))
        self.assertEqual(response.status_code, 405)

    def test_valid_swap_repoints_template(self):
        c = self._agency_client()
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": self.template_b.pk})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:tenant_detail", args=[self.tenant.pk]),
                      response["Location"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, self.template_b.pk)

    def test_swap_to_same_template_is_noop(self):
        c = self._agency_client()
        before = self.tenant.template_id
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": before})
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, before)

    def test_invalid_template_id_does_not_change_template(self):
        c = self._agency_client()
        before = self.tenant.template_id
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": "999999"})
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, before)

    def test_empty_template_id_does_not_change_template(self):
        c = self._agency_client()
        before = self.tenant.template_id
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": ""})
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, before)

    def test_non_integer_template_id_does_not_change_template(self):
        c = self._agency_client()
        before = self.tenant.template_id
        response = c.post(self._swap_url(self.tenant.pk),
                          {"template_id": "abc"})
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, before)

    def test_swap_does_not_wipe_tenant_content(self):
        """Content lives on the Tenant row and must survive a swap. Fields
        whose ids no longer exist in the new schema sit dormant; nothing
        is deleted."""
        self.tenant.content = {"hero": {"title": "Welcome", "body": "Hi"}}
        self.tenant.save(update_fields=["content"])

        c = self._agency_client()
        c.post(self._swap_url(self.tenant.pk),
               {"template_id": self.template_b.pk})

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, self.template_b.pk)
        self.assertEqual(self.tenant.content,
                         {"hero": {"title": "Welcome", "body": "Hi"}})

    def test_unknown_tenant_pk_returns_404(self):
        c = self._agency_client()
        response = c.post(self._swap_url(999999),
                          {"template_id": self.template_b.pk})
        self.assertEqual(response.status_code, 404)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TenantDetailExposesAvailableTemplatesTests(TestCase):
    """The tenant detail page must include the template dropdown options
    so the inline swap form renders."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template_a = _make_template("Alpha")
        cls.template_b = _make_template("Bravo")
        cls.template_c = _make_template("Charlie")
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme",
            template=cls.template_a, owner=cls.staff,
        )

    def test_detail_page_lists_all_templates_in_swap_form(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        response = c.get(reverse("dashboard:tenant_detail", args=[self.tenant.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for needle in ("Alpha", "Bravo", "Charlie"):
            self.assertIn(needle, body, f"template {needle!r} missing from page")
        # The swap form action must point at our new endpoint.
        self.assertIn(
            reverse("dashboard:tenant_template_swap", args=[self.tenant.pk]),
            body,
        )

    def test_edit_html_link_points_at_current_template(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        response = c.get(reverse("dashboard:tenant_detail", args=[self.tenant.pk]))
        body = response.content.decode()
        self.assertIn(
            reverse("dashboard:template_detail", args=[self.template_a.pk]),
            body,
            "tenant detail must link to the current template's editor",
        )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TemplateEditorWarnsWhenSharedTests(TestCase):
    """When the template editor is opened on a template used by more than
    one tenant, a banner must list the affected sites so the operator
    knows their HTML edit fans out beyond one client."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _make_template("Sample_website")
        cls.t1 = Tenant.objects.create(
            name="One", subdomain="one", template=cls.template, owner=cls.staff,
        )
        cls.t2 = Tenant.objects.create(
            name="Two", subdomain="two", template=cls.template, owner=cls.staff,
        )

    def test_banner_shown_when_template_has_multiple_tenants(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        response = c.get(reverse("dashboard:template_detail", args=[self.template.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("template is shared", body)
        # Both affected tenant names should appear in the banner.
        self.assertIn("One", body)
        self.assertIn("Two", body)

    def test_banner_hidden_when_template_has_one_tenant(self):
        # Reduce to a single tenant for this test.
        self.t2.delete()
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        response = c.get(reverse("dashboard:template_detail", args=[self.template.pk]))
        body = response.content.decode()
        self.assertNotIn("template is shared", body)
