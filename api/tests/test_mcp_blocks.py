"""CMS-71: block instance addressing and guarded HTML edits over JSON-RPC."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.mcp.content import content_etag
from core.models import BlockType, ContentVersion, Page, Template, Tenant, TenantMembership
from core.services import blocks


SHELL = ('<html><body><header data-section="nav"><span data-edit="nav.brand" '
         'data-type="text">Brand</span></header><main data-region="main"></main></body></html>')
HERO = ('<section data-block="hero"><h1 data-edit="hero.title" '
        'data-type="text">Default title</h1></section>')
ROW = '<div data-block="row"><div data-region="col"></div></div>'


@override_settings(CLAUDE_OAUTH_CLIENT_ID="claude-test", MCP_ALLOWED_ORIGINS="https://claude.ai",
                   TENANT_BASE_DOMAIN="sites.katek.app", ALLOWED_HOSTS=["*"])
class McpBlockTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user("owner-block", password="x")
        self.member = user_model.objects.create_user("member-block", password="x")
        self.other = user_model.objects.create_user("other-block", password="x")
        app = Application.objects.create(name="Claude", client_id="claude-test",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE)
        for user, token in ((self.member, "block-member"), (self.other, "block-other")):
            AccessToken.objects.create(user=user, application=app, token=token,
                expires=timezone.now() + timedelta(hours=1), scope="read write")
        self.tpl = Template.objects.create(name="Block shell", html_source=SHELL,
            editing_mode=Template.EDITING_EDITABLE)
        self.hero = BlockType.objects.create(html_source=HERO)
        self.row = BlockType.objects.create(html_source=ROW)
        self.tpl.allowed_block_types.add(self.hero, self.row)
        self.tenant = Tenant.objects.create(name="Alpha", subdomain="alpha", owner=self.owner,
            template=self.tpl, is_published=True, content={"nav": {"brand": "Acme"},
                "regions": {"main": [
                    {"id": "blk_11111111", "type": "hero", "fields": {"title": "One"}},
                    {"id": "blk_22222222", "type": "hero", "fields": {"title": "Two"}},
                    {"id": "blk_33333333", "type": "row", "fields": {},
                     "children": {"col": [{"id": "blk_44444444", "type": "hero",
                                            "fields": {"title": "Nested"}}]}},
                ]}})
        self.tpl.tenant = self.tenant
        self.tpl.save(update_fields=["tenant"])
        TenantMembership.objects.create(tenant=self.tenant, user=self.member,
            role=TenantMembership.ROLE_EDITOR)
        self.page = Page.objects.create(tenant=self.tenant, template=self.tpl,
            title="About", slug="about", content={"regions": {"main": [
                {"id": "blk_55555555", "type": "hero", "fields": {"title": "About"}}]}},
            is_published=True)
        self.client = Client()

    def call(self, name, args, token="block-member"):
        response = self.client.post("/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": name, "arguments": args}}),
            content_type="application/json", HTTP_HOST="localhost",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_MCP_PROTOCOL_VERSION="2025-06-18", HTTP_ACCEPT="application/json")
        return response.status_code, response.json()["result"]

    def test_enumerates_shell_repeated_and_nested_fields_on_home_and_inner(self):
        status, result = self.call("get_page", {"site": "alpha"})
        self.assertEqual(status, 200)
        fields = result["structuredContent"]["fields"]
        self.assertEqual(fields["nav.brand"]["value"], "Acme")
        self.assertEqual(fields["blk_11111111.title"]["value"], "One")
        self.assertEqual(fields["blk_22222222.title"]["value"], "Two")
        self.assertEqual(fields["blk_44444444.title"]["value"], "Nested")
        self.assertNotIn("hero.title", fields)
        _, one = self.call("get_content", {"site": "alpha", "field": "blk_22222222.title"})
        self.assertEqual(one["structuredContent"]["value"], "Two")
        _, inner = self.call("get_page", {"site": "alpha", "page": "about"})
        self.assertEqual(inner["structuredContent"]["fields"]["blk_55555555.title"]["value"], "About")

    def test_patch_targets_one_instance_versions_and_rejects_stale_etag(self):
        old = content_etag(self.tenant.content)
        status, result = self.call("patch_content", {"site": "alpha", "field": "blk_22222222.title",
            "value": "Changed", "if_match": old})
        self.assertEqual(status, 200)
        self.assertFalse(result["isError"])
        self.tenant.refresh_from_db()
        main = self.tenant.content["regions"]["main"]
        self.assertEqual([x["fields"].get("title") for x in main[:2]], ["One", "Changed"])
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant).count(), 1)
        self.assertIn("Changed", blocks.render_content(self.tpl, self.tenant.content))
        status, _ = self.call("patch_content", {"site": "alpha", "field": "blk_22222222.title",
            "value": "Stale", "if_match": old})
        self.assertEqual(status, 409)

    def test_inner_page_patch_and_reorder_keep_instance_identity(self):
        old = content_etag(self.page.content)
        status, result = self.call("patch_content", {"site": "alpha", "page": "about",
            "field": "blk_55555555.title", "value": "Revised about", "if_match": old})
        self.assertEqual(status, 200)
        self.assertFalse(result["isError"], result)
        self.page.refresh_from_db()
        self.assertEqual(self.page.content["regions"]["main"][0]["fields"]["title"], "Revised about")
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant, page=self.page).count(), 1)
        self.tenant.content["regions"]["main"].reverse()
        self.tenant.save(update_fields=["content"])
        _, read = self.call("get_content", {"site": "alpha", "field": "blk_11111111.title"})
        self.assertEqual(read["structuredContent"]["value"], "One")

    def test_block_default_and_ghl_form_field_cannot_be_patched(self):
        form = BlockType.objects.create(html_source=(
            '<section data-block="form"><div data-edit="form.embed" '
            'data-type="ghl-embed" data-ghl-kind="form"></div></section>'))
        self.tpl.allowed_block_types.add(form)
        self.tenant.content["regions"]["main"].append({"id": "blk_66666666", "type": "form", "fields": {}})
        self.tenant.content["regions"]["main"][0]["fields"] = {}
        self.tenant.save(update_fields=["content"])
        _, read = self.call("get_page", {"site": "alpha"})
        self.assertTrue(read["structuredContent"]["fields"]["blk_11111111.title"]["is_default"])
        self.assertEqual(read["structuredContent"]["fields"]["blk_11111111.title"]["value"], "Default title")
        _, denied = self.call("patch_content", {"site": "alpha", "field": "blk_66666666.embed",
            "value": "attacker-form", "if_match": content_etag(self.tenant.content)})
        self.assertTrue(denied["isError"])
        _, slots = self.call("list_embed_slots", {"site": "alpha"})
        self.assertIn("blk_66666666.embed", [slot["id"] for slot in slots["structuredContent"]["slots"]])
        with patch("core.services.ghl_forms.list_forms_for_tenant", return_value=[{"id": "allowed"}]):
            _, saved = self.call("set_embed_slot", {"site": "alpha",
                "field": "blk_66666666.embed", "value": "form:allowed",
                "if_match": content_etag(self.tenant.content)})
        self.assertFalse(saved["isError"], saved)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["regions"]["main"][-1]["fields"], {"embed": "form:allowed"})
        _, removal = self.call("set_embed_slot", {"site": "alpha",
            "field": "blk_66666666.embed", "value": "",
            "if_match": content_etag(self.tenant.content)})
        self.assertTrue(removal["isError"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["regions"]["main"][-1]["fields"], {"embed": "form:allowed"})

    def test_deleted_instance_and_foreign_site_are_unavailable(self):
        _, result = self.call("get_content", {"site": "alpha", "field": "blk_deadbeef.title"})
        self.assertTrue(result["isError"])
        response = self.client.post("/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1,
            "method": "tools/call", "params": {"name": "get_page", "arguments": {"site": "alpha"}}}),
            content_type="application/json", HTTP_HOST="localhost",
            HTTP_AUTHORIZATION="Bearer block-other", HTTP_MCP_PROTOCOL_VERSION="2025-06-18",
            HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 401)

    def test_ambiguous_legacy_instance_ids_fail_closed(self):
        self.tenant.content["regions"]["main"][1]["id"] = "blk_11111111"
        self.tenant.save(update_fields=["content"])
        _, result = self.call("get_page", {"site": "alpha"})
        self.assertTrue(result["isError"])
        _, patch = self.call("patch_content", {"site": "alpha",
            "field": "blk_11111111.title", "value": "Wrong block",
            "if_match": content_etag(self.tenant.content)})
        self.assertTrue(patch["isError"])

    def test_block_html_round_trip_and_structural_rejection(self):
        _, read = self.call("get_block_html", {"site": "alpha", "instance_id": "blk_11111111"})
        self.assertFalse(read["isError"], read)
        data = read["structuredContent"]
        self.assertIn("One", data["html"])
        new_html = data["html"].replace("One", "Changed copy")
        _, write = self.call("push_block_html", {"site": "alpha", "instance_id": "blk_11111111",
            "html": new_html, "if_match": data["content_etag"]})
        self.assertFalse(write["isError"], write)
        _, reread = self.call("get_block_html", {"site": "alpha", "instance_id": "blk_11111111"})
        self.assertIn("Changed copy", reread["structuredContent"]["html"])
        stale_status, _ = self.call("push_block_html", {"site": "alpha",
            "instance_id": "blk_11111111", "html": new_html,
            "if_match": data["content_etag"]})
        self.assertEqual(stale_status, 409)
        self.tenant.refresh_from_db()
        before = self.tenant.content
        _, refused = self.call("push_block_html", {"site": "alpha", "instance_id": "blk_11111111",
            "html": new_html.replace("</h1>", "</h1><script>alert(1)</script>"),
            "if_match": reread["structuredContent"]["content_etag"]})
        self.assertTrue(refused["isError"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, before)

    def test_nested_block_html_is_instance_scoped(self):
        _, read = self.call("get_block_html", {"site": "alpha", "instance_id": "blk_44444444"})
        html = read["structuredContent"]["html"]
        self.assertIn("Nested", html)
        _, write = self.call("push_block_html", {"site": "alpha", "instance_id": "blk_44444444",
            "html": html.replace("Nested", "Inner changed"),
            "if_match": read["structuredContent"]["content_etag"]})
        self.assertFalse(write["isError"], write)
        self.tenant.refresh_from_db()
        child = self.tenant.content["regions"]["main"][2]["children"]["col"][0]
        self.assertEqual(child["fields"]["title"], "Inner changed")

    def test_inner_page_block_html_round_trip(self):
        _, read = self.call("get_block_html", {"site": "alpha", "page": "about",
            "instance_id": "blk_55555555"})
        data = read["structuredContent"]
        _, write = self.call("push_block_html", {"site": "alpha", "page": "about",
            "instance_id": "blk_55555555", "html": data["html"].replace("About", "New about"),
            "if_match": data["content_etag"]})
        self.assertFalse(write["isError"], write)
        self.page.refresh_from_db()
        self.assertEqual(self.page.content["regions"]["main"][0]["fields"]["title"], "New about")
        self.assertIn("New about", blocks.render_content(self.tpl, self.page.content))

    def test_richtext_html_is_sanitized_before_storage(self):
        rich = BlockType.objects.create(html_source=(
            '<section data-block="rich"><p data-edit="rich.copy" '
            'data-type="richtext">Original</p></section>'))
        self.tpl.allowed_block_types.add(rich)
        self.tenant.content["regions"]["main"].append(
            {"id": "blk_77777777", "type": "rich", "fields": {}})
        self.tenant.save(update_fields=["content"])
        _, read = self.call("get_block_html", {"site": "alpha", "instance_id": "blk_77777777"})
        html = read["structuredContent"]["html"].replace(
            "Original", "<strong>Safe</strong><script>alert(1)</script>")
        _, write = self.call("push_block_html", {"site": "alpha", "instance_id": "blk_77777777",
            "html": html, "if_match": read["structuredContent"]["content_etag"]})
        self.assertFalse(write["isError"], write)
        self.tenant.refresh_from_db()
        value = self.tenant.content["regions"]["main"][-1]["fields"]["copy"]
        self.assertIn("<strong>Safe</strong>", value)
        self.assertNotIn("script", value)

    def test_push_page_rejects_existing_block_page_before_any_mutation(self):
        before = self.tpl.html_source
        status, result = self.call("push_page", {"site": "alpha", "html": "<h1>Flatten</h1>"})
        self.assertEqual(status, 200)
        self.assertTrue(result["isError"])
        self.assertIn("block", result["content"][0]["text"].lower())
        self.tpl.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.tpl.html_source, before)
        self.assertEqual(len(self.tenant.content["regions"]["main"]), 3)
        self.assertEqual(self.tpl.versions.count(), 0)
        _, inner = self.call("push_page", {"site": "alpha", "page": "about",
            "html": "<h1>Flatten</h1>"})
        self.assertTrue(inner["isError"])
        self.page.refresh_from_db()
        self.assertEqual(len(self.page.content["regions"]["main"]), 1)
        _, explicit = self.call("push_page", {"site": "alpha", "page": "new-page",
            "template_id": self.tpl.pk, "html": "<h1>Flatten</h1>"})
        self.assertTrue(explicit["isError"])
        self.assertFalse(Page.objects.filter(tenant=self.tenant, slug="new-page").exists())
