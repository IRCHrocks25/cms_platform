"""CMS-42 MCP contracts for tenant-safe GHL embed slots."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.mcp.content import content_etag
from api.models import McpAuditLog
from core.models import ContentVersion, Page, Template, Tenant, TenantMembership
from core.services.ghl_forms import GhlFormsUnavailable


User = get_user_model()
CLIENT_ID = "claude-ghl-embed-test"
PROTOCOL = "2025-06-18"
EMBED_HTML = """
<section data-section="contact" data-label="Contact" data-group="Home">
  <h2 data-edit="contact.title" data-type="text">Contact</h2>
  <div data-edit="contact.embed" data-type="ghl-embed"
       data-ghl-kind="form" data-label="Lead form"></div>
</section>
"""


def _token(user, value):
    app, _ = Application.objects.get_or_create(
        name="Claude GHL",
        defaults={
            "client_id": CLIENT_ID,
            "client_type": Application.CLIENT_CONFIDENTIAL,
            "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
            "redirect_uris": "https://example.test/callback",
        },
    )
    return AccessToken.objects.create(
        user=user,
        application=app,
        token=value,
        expires=timezone.now() + timedelta(hours=1),
        scope="read write",
    )


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
)
class McpGhlEmbedTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.member = User.objects.create_user("embed-editor", password="x")
        self.other = User.objects.create_user("other-editor", password="x")
        template_a = Template.objects.create(
            name="Alpha embed",
            html_source=EMBED_HTML,
            editing_mode=Template.EDITING_EDITABLE,
        )
        template_b = Template.objects.create(
            name="Beta embed",
            html_source=EMBED_HTML,
            editing_mode=Template.EDITING_EDITABLE,
        )
        self.tenant_a = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=template_a,
            owner=self.member,
            content={"contact": {"title": "A", "embed": "form:alpha_old"}},
            is_published=True,
        )
        self.tenant_b = Tenant.objects.create(
            name="Beta",
            subdomain="beta",
            template=template_b,
            owner=self.other,
            content={"contact": {"title": "B", "embed": "form:beta_form"}},
            is_published=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        self.page = Page.objects.create(
            tenant=self.tenant_a,
            template=template_a,
            title="Landing",
            slug="landing",
            content={"contact": {"title": "Landing", "embed": ""}},
            is_published=False,
        )
        _token(self.member, "tok-embed-member")

    def _post(self, body):
        return self.client.post(
            "/mcp",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION="Bearer tok-embed-member",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
            HTTP_ACCEPT="application/json",
        )

    def _call(self, name, arguments=None):
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )

    def _result(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"]

    def test_tools_are_discoverable_with_correct_mutability(self):
        tools = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        ).json()["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}

        self.assertTrue(by_name["list_embed_slots"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["list_ghl_forms"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["set_embed_slot"]["annotations"]["readOnlyHint"])
        self.assertEqual(
            set(by_name["set_embed_slot"]["inputSchema"]["required"]),
            {"site", "field", "value", "if_match"},
        )
        for name in ("list_embed_slots", "set_embed_slot", "list_ghl_forms"):
            self.assertIn("outputSchema", by_name[name])

    def test_lists_home_and_page_slots_with_prefixed_values(self):
        home = self._result(self._call("list_embed_slots", {"site": "alpha"}))
        page = self._result(
            self._call("list_embed_slots", {"site": "alpha", "page": "landing"})
        )

        self.assertEqual(
            home["structuredContent"]["slots"],
            [
                {
                    "id": "contact.embed",
                    "label": "Lead form",
                    "kind": "form",
                    "value": "form:alpha_old",
                }
            ],
        )
        self.assertEqual(page["structuredContent"]["slots"][0]["value"], "")
        self.assertEqual(page["structuredContent"]["page"], "landing")
        self.assertFalse(page["structuredContent"]["published"])

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_lists_only_authorized_tenants_ghl_forms(self, list_forms):
        list_forms.return_value = [{"id": "alpha_form", "name": "Alpha lead"}]

        result = self._result(self._call("list_ghl_forms", {"site": "alpha"}))

        self.assertEqual(
            result["structuredContent"]["forms"],
            [
                {
                    "id": "alpha_form",
                    "name": "Alpha lead",
                    "value": "form:alpha_form",
                }
            ],
        )
        self.assertEqual(list_forms.call_args.args[0].pk, self.tenant_a.pk)

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_cross_tenant_form_enumeration_matches_missing_site(self, list_forms):
        forbidden = self._call("list_ghl_forms", {"site": "beta"})
        self.tenant_b.delete()
        missing = self._call("list_ghl_forms", {"site": "beta"})

        self.assertEqual(forbidden.content, missing.content)
        list_forms.assert_not_called()
        self.assertTrue(self._result(forbidden)["isError"])

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_cross_tenant_embed_write_matches_missing_and_never_lists_forms(
        self, list_forms
    ):
        arguments = {
            "site": "beta",
            "field": "contact.embed",
            "value": "form:alpha_form",
            "if_match": content_etag(self.tenant_b.content),
        }
        forbidden = self._call("set_embed_slot", arguments)
        self.tenant_b.delete()
        missing = self._call("set_embed_slot", arguments)

        self.assertEqual(forbidden.content, missing.content)
        self.assertTrue(self._result(forbidden)["isError"])
        list_forms.assert_not_called()

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_set_embed_slot_validates_tenant_form_and_versions_home(self, list_forms):
        list_forms.return_value = [{"id": "alpha_new", "name": "New lead"}]
        old_etag = content_etag(self.tenant_a.content)

        result = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "form:alpha_new",
                    "if_match": old_etag,
                },
            )
        )

        self.assertFalse(result["isError"])
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content["contact"]["embed"], "form:alpha_new")
        self.assertNotEqual(result["structuredContent"]["content_etag"], old_etag)
        version = ContentVersion.objects.get(tenant=self.tenant_a)
        self.assertEqual(version.snapshot["contact"]["embed"], "form:alpha_old")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_set_embed_slot_supports_unpublished_inner_page(self, list_forms):
        list_forms.return_value = [{"id": "landing_form", "name": "Landing"}]

        result = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "page": "landing",
                    "field": "contact.embed",
                    "value": "form:landing_form",
                    "if_match": content_etag(self.page.content),
                },
            )
        )

        self.assertFalse(result["isError"])
        self.page.refresh_from_db()
        self.assertEqual(self.page.content["contact"]["embed"], "form:landing_form")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_wrong_tenant_form_and_raw_id_are_refused(self, list_forms):
        list_forms.return_value = [{"id": "alpha_form", "name": "Alpha"}]
        etag = content_etag(self.tenant_a.content)
        wrong = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "form:beta_form",
                    "if_match": etag,
                },
            )
        )
        raw = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "raw-id",
                    "if_match": etag,
                },
            )
        )

        self.assertTrue(wrong["isError"])
        self.assertIn("not available", wrong["content"][0]["text"])
        self.assertTrue(raw["isError"])
        self.assertIn("form:<id>", raw["content"][0]["text"])
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content["contact"]["embed"], "form:alpha_old")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_non_embed_field_is_refused_without_form_lookup(self, list_forms):
        result = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "field": "contact.title",
                    "value": "form:alpha_form",
                    "if_match": content_etag(self.tenant_a.content),
                },
            )
        )
        self.assertTrue(result["isError"])
        list_forms.assert_not_called()

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_published_embed_cannot_be_unset(self, list_forms):
        result = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "",
                    "if_match": content_etag(self.tenant_a.content),
                },
            )
        )
        self.assertTrue(result["isError"])
        self.assertIn("published page", result["content"][0]["text"])
        list_forms.assert_not_called()

    def test_generic_patch_cannot_bypass_embed_slot_rules(self):
        result = self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "",
                    "if_match": content_etag(self.tenant_a.content),
                },
            )
        )
        self.assertTrue(result["isError"])
        self.assertIn("set_embed_slot", result["content"][0]["text"])
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content["contact"]["embed"], "form:alpha_old")

    def test_generic_patch_cannot_bypass_stale_stored_schema(self):
        stale = self.tenant_a.template.schema
        stale["sections"][0]["fields"][1]["type"] = "text"
        Template.objects.filter(pk=self.tenant_a.template_id).update(schema=stale)

        result = self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "",
                    "if_match": content_etag(self.tenant_a.content),
                },
            )
        )
        self.assertTrue(result["isError"])
        self.assertIn("set_embed_slot", result["content"][0]["text"])

    def test_list_embed_slots_reparses_current_html_when_stored_schema_is_stale(self):
        stale = self.tenant_a.template.schema
        stale["sections"][0]["fields"][1]["type"] = "text"
        Template.objects.filter(pk=self.tenant_a.template_id).update(schema=stale)

        result = self._result(
            self._call("list_embed_slots", {"site": "alpha"})
        )

        self.assertFalse(result["isError"])
        self.assertEqual(
            result["structuredContent"]["slots"][0]["id"], "contact.embed"
        )

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_set_embed_slot_reparses_current_html_when_stored_schema_is_stale(
        self, list_forms
    ):
        stale = self.tenant_a.template.schema
        stale["sections"][0]["fields"][1]["type"] = "text"
        Template.objects.filter(pk=self.tenant_a.template_id).update(schema=stale)
        list_forms.return_value = [{"id": "alpha_new", "name": "New lead"}]

        result = self._result(
            self._call(
                "set_embed_slot",
                {
                    "site": "alpha",
                    "field": "contact.embed",
                    "value": "form:alpha_new",
                    "if_match": content_etag(self.tenant_a.content),
                },
            )
        )

        self.assertFalse(result["isError"])
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content["contact"]["embed"], "form:alpha_new")
        list_forms.assert_called_once_with(self.tenant_a)

    def test_stale_etag_writes_nothing(self):
        result = self._call(
            "set_embed_slot",
            {
                "site": "alpha",
                "field": "contact.embed",
                "value": "form:anything",
                "if_match": "deadbeef" * 8,
            },
        )
        self.assertEqual(result.status_code, 409)
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content["contact"]["embed"], "form:alpha_old")

    @mock.patch("core.services.ghl_forms.list_forms_for_tenant")
    def test_disconnected_message_is_returned_without_write(self, list_forms):
        list_forms.side_effect = GhlFormsUnavailable(
            code="reconnect_required",
            public_message="Reconnect this site's GoHighLevel integration.",
        )
        result = self._result(self._call("list_ghl_forms", {"site": "alpha"}))
        self.assertTrue(result["isError"])
        self.assertIn("Reconnect", result["content"][0]["text"])

    def test_authorized_audit_stamps_tenant_but_denial_does_not(self):
        self._result(self._call("list_embed_slots", {"site": "alpha"}))
        self._result(self._call("list_embed_slots", {"site": "beta"}))
        rows = list(McpAuditLog.objects.order_by("id"))
        self.assertEqual(rows[0].tenant_id, self.tenant_a.pk)
        self.assertIsNone(rows[1].tenant_id)
        self.assertEqual(rows[0].action, "list_embed_slots")
