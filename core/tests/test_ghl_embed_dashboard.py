import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import ContentVersion, Page, Template, Tenant, TenantMembership
from core.services.ghl_forms import GhlFormsUnavailable


User = get_user_model()

EMBED_HTML = """
<!doctype html><html><body>
<section data-section="contact" data-label="Contact" data-group="Home">
  <h2 data-edit="contact.title" data-type="text">Contact us</h2>
  <div data-edit="contact.embed" data-type="ghl-embed"
       data-ghl-kind="form" data-label="Lead form"></div>
</section>
</body></html>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class GhlFormsDashboardAccessTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.member_a = User.objects.create_user("member-a", password="x")
        self.member_b = User.objects.create_user("member-b", password="x")
        template = Template.objects.create(
            name="Embed", html_source=EMBED_HTML, editing_mode=Template.EDITING_EDITABLE
        )
        self.tenant_a = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=template,
            owner=self.member_a,
            ghl_location_id="loc_alpha",
        )
        self.tenant_b = Tenant.objects.create(
            name="Beta",
            subdomain="beta",
            template=template,
            owner=self.member_b,
            ghl_location_id="loc_beta",
        )
        TenantMembership.objects.create(tenant=self.tenant_a, user=self.member_a)
        TenantMembership.objects.create(tenant=self.tenant_b, user=self.member_b)

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_tenant_member_lists_only_host_tenants_forms(self, list_forms):
        list_forms.return_value = [{"id": "alpha_form", "name": "Alpha form"}]
        client = Client(HTTP_HOST="alpha.localhost")
        client.force_login(self.member_a)

        response = client.get(reverse("dashboard:tenant_ghl_forms_self"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "forms": [
                    {
                        "id": "alpha_form",
                        "name": "Alpha form",
                        "value": "form:alpha_form",
                    }
                ],
            },
        )
        self.assertEqual(list_forms.call_args.args[0].pk, self.tenant_a.pk)

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_agency_operator_can_list_selected_tenants_forms(self, list_forms):
        list_forms.return_value = [{"id": "beta_form", "name": "Beta form"}]
        client = Client(HTTP_HOST="localhost")
        client.force_login(self.staff)

        response = client.get(
            reverse("dashboard:tenant_ghl_forms", args=[self.tenant_b.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["forms"][0]["value"], "form:beta_form")
        self.assertEqual(list_forms.call_args.args[0].pk, self.tenant_b.pk)

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_tenant_member_cannot_use_agency_route_for_another_tenant(self, list_forms):
        client = Client(HTTP_HOST="localhost")
        client.force_login(self.member_a)

        response = client.get(
            reverse("dashboard:tenant_ghl_forms", args=[self.tenant_b.pk])
        )

        self.assertEqual(response.status_code, 403)
        list_forms.assert_not_called()

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_non_member_cannot_list_tenant_host_forms(self, list_forms):
        client = Client(HTTP_HOST="alpha.localhost")
        client.force_login(self.member_b)

        response = client.get(reverse("dashboard:tenant_ghl_forms_self"))

        self.assertEqual(response.status_code, 403)
        list_forms.assert_not_called()

    def test_anonymous_tenant_forms_request_redirects_to_login(self):
        response = Client(HTTP_HOST="alpha.localhost").get(
            reverse("dashboard:tenant_ghl_forms_self")
        )
        self.assertEqual(response.status_code, 302)

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_connection_error_is_clear_json_not_broken_page(self, list_forms):
        list_forms.side_effect = GhlFormsUnavailable(
            code="reconsent_required",
            public_message="Reconnect GoHighLevel and approve the Forms permission.",
        )
        client = Client(HTTP_HOST="alpha.localhost")
        client.force_login(self.member_a)

        response = client.get(reverse("dashboard:tenant_ghl_forms_self"))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "reconsent_required")
        self.assertIn("Reconnect", response.json()["error"])


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class GhlEmbedEditorTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("staff-editor", password="x", is_staff=True)
        self.member = User.objects.create_user("member-editor", password="x")
        self.template = Template.objects.create(
            name="Embed", html_source=EMBED_HTML, editing_mode=Template.EDITING_EDITABLE
        )
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.template,
            owner=self.member,
            content={"contact": {"title": "Contact us", "embed": ""}},
            is_published=True,
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.member)

    def test_tenant_editor_has_accessible_async_picker_and_published_warning(self):
        client = Client(HTTP_HOST="alpha.localhost")
        client.force_login(self.member)

        response = client.get(reverse("dashboard:tenant_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-ghl-picker="contact.embed"')
        self.assertContains(response, 'data-ghl-picker-status')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, "This published page has no form selected")
        self.assertContains(response, reverse("dashboard:tenant_ghl_forms_self"))

    def test_agency_editor_shows_same_published_empty_warning(self):
        client = Client(HTTP_HOST="localhost")
        client.force_login(self.staff)

        response = client.get(
            reverse("dashboard:tenant_editor", args=[self.tenant.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This published page has no form selected")
        self.assertContains(
            response,
            reverse("dashboard:tenant_ghl_forms", args=[self.tenant.pk]),
        )

    def test_unpublished_empty_slot_does_not_show_global_health_warning(self):
        self.tenant.is_published = False
        self.tenant.save(update_fields=["is_published"])
        client = Client(HTTP_HOST="alpha.localhost")
        client.force_login(self.member)

        response = client.get(reverse("dashboard:tenant_home"))

        self.assertNotContains(response, "This published page has no form selected")


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class GhlEmbedDashboardWriteTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("writer", password="x")
        self.template = Template.objects.create(
            name="Embed", html_source=EMBED_HTML, editing_mode=Template.EDITING_EDITABLE
        )
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.template,
            owner=self.member,
            content={
                "contact": {"title": "Contact us", "embed": "form:existing"}
            },
            is_published=True,
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.member)
        self.client = Client(HTTP_HOST="alpha.localhost")
        self.client.force_login(self.member)

    def _save(self, content, *, url=None):
        return self.client.post(
            url or reverse("dashboard:tenant_save_self"),
            data=json.dumps({"content": content}),
            content_type="application/json",
        )

    def test_published_form_cannot_be_unset_with_crafted_save(self):
        response = self._save(
            {"contact": {"title": "Contact us", "embed": ""}}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("published page", response.json()["error"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["contact"]["embed"], "form:existing")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_selected_form_must_exist_in_this_tenants_list(self, list_forms):
        list_forms.return_value = [{"id": "alpha_form", "name": "Alpha"}]

        response = self._save(
            {"contact": {"title": "Contact us", "embed": "form:beta_form"}}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not available", response.json()["error"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["contact"]["embed"], "form:existing")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_valid_tenant_form_is_saved_with_prefix(self, list_forms):
        list_forms.return_value = [{"id": "alpha_form", "name": "Alpha"}]

        response = self._save(
            {"contact": {"title": "Contact us", "embed": "form:alpha_form"}}
        )

        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["contact"]["embed"], "form:alpha_form")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_unchanged_embed_does_not_call_ghl_during_text_save(self, list_forms):
        response = self._save(
            {"contact": {"title": "New title", "embed": "form:existing"}}
        )

        self.assertEqual(response.status_code, 200)
        list_forms.assert_not_called()

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_raw_id_is_rejected_before_api_call(self, list_forms):
        response = self._save(
            {"contact": {"title": "Contact us", "embed": "raw-id"}}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("form:<id>", response.json()["error"])
        list_forms.assert_not_called()

    def test_published_inner_page_form_cannot_be_unset(self):
        page = Page.objects.create(
            tenant=self.tenant,
            template=self.template,
            title="Landing",
            slug="landing",
            content={"contact": {"title": "Landing", "embed": "form:existing"}},
            is_published=True,
        )

        response = self._save(
            {"contact": {"title": "Landing", "embed": ""}},
            url=reverse("dashboard:page_save_self", args=[page.pk]),
        )

        self.assertEqual(response.status_code, 400)
        page.refresh_from_db()
        self.assertEqual(page.content["contact"]["embed"], "form:existing")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_content_restore_cannot_reintroduce_deleted_or_foreign_form(
        self, list_forms
    ):
        version = ContentVersion.objects.create(
            tenant=self.tenant,
            snapshot={
                "contact": {
                    "title": "Old contact",
                    "embed": "form:deleted_or_foreign",
                }
            },
            saved_by=self.member,
        )
        list_forms.return_value = [{"id": "existing", "name": "Current"}]

        response = self.client.post(
            reverse("dashboard:tenant_version_restore_self"),
            data=json.dumps({"version_id": version.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not available", response.json()["error"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["contact"]["embed"], "form:existing")

    def test_content_version_preview_uses_submission_shield(self):
        version = ContentVersion.objects.create(
            tenant=self.tenant,
            snapshot={
                "contact": {
                    "title": "Old contact",
                    "embed": "form:historical",
                }
            },
            saved_by=self.member,
        )

        response = self.client.get(
            reverse("dashboard:tenant_version_preview_self", args=[version.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sandbox="allow-scripts"')
        self.assertContains(response, "This is a preview, nothing is sent.")
