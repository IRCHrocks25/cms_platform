"""S05: suite credentials may edit annotated drafts, never public effects."""

import copy
import json
import uuid

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from api.mcp.content import content_etag
from api.service_auth import issue_service_credential
from api.suite_credential_handoff import (
    acknowledge_suite_credential_handoff,
    begin_suite_credential_handoff,
)
from core.models import BlockType, ContentVersion, Page, Template, Tenant
from core.services.suite_bindings import link_suite_site

from .test_mcp_blocks import HERO, SHELL


@override_settings(TENANT_BASE_DOMAIN="sites.katek.app", ALLOWED_HOSTS=["*"])
class SuiteDraftBlockTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("suite-block-owner", password="x")
        self.template = Template.objects.create(
            name="Suite block shell", html_source=SHELL,
            editing_mode=Template.EDITING_EDITABLE,
        )
        hero = BlockType.objects.create(html_source=HERO)
        rich = BlockType.objects.create(html_source=(
            '<section data-block="rich"><div data-edit="rich.copy" '
            'data-type="richtext"><p>Original</p></div></section>'
        ))
        self.template.allowed_block_types.add(hero, rich)
        self.tenant = Tenant.objects.create(
            name="Suite Alpha", subdomain="suite-alpha", owner=owner,
            template=self.template, content={"regions": {"main": [
                {"id": "blk_11111111", "type": "hero", "fields": {"title": "One"}},
                {"id": "blk_22222222", "type": "hero", "fields": {"title": "Two"}},
                {"id": "blk_33333333", "type": "rich", "fields": {}},
            ]}},
        )
        self.template.tenant = self.tenant
        self.template.save(update_fields=["tenant"])
        self.page = Page.objects.create(
            tenant=self.tenant, template=self.template, slug="about", title="About",
            content={"regions": {"main": [
                {"id": "blk_44444444", "type": "hero", "fields": {"title": "About"}},
            ]}},
        )
        self.other = Tenant.objects.create(
            name="Suite Beta", subdomain="suite-beta", owner=owner,
            template=self.template, content={"regions": {"main": []}},
        )
        _, self.token = issue_service_credential(
            self.tenant, scopes={"read", "draft", "publish", "domain"}
        )
        self.client = Client()

    def call(self, name, args):
        response = self.client.post(
            "/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1,
                "method": "tools/call", "params": {"name": name, "arguments": args}}),
            content_type="application/json", HTTP_HOST="localhost",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
            HTTP_MCP_PROTOCOL_VERSION="2025-06-18", HTTP_ACCEPT="application/json",
        )
        return response.status_code, response.json()["result"]

    def test_block_html_draft_round_trip_preserves_annotation_and_etag(self):
        _, read = self.call("get_block_html", {
            "site": "suite-alpha", "instance_id": "blk_11111111"})
        self.assertFalse(read["isError"], read)
        before = read["structuredContent"]
        self.assertIn('data-edit="blk_11111111.title"', before["html"])
        status, saved = self.call("push_block_html", {
            "site": "suite-alpha", "instance_id": "blk_11111111",
            "html": before["html"].replace("One", "Changed"),
            "if_match": before["content_etag"],
        })
        self.assertEqual(status, 200)
        self.assertFalse(saved["isError"], saved)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["regions"]["main"][0]["fields"]["title"], "Changed")
        self.assertEqual(self.tenant.content["regions"]["main"][1]["fields"]["title"], "Two")
        self.assertEqual(self.template.html_source, SHELL)
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant, page__isnull=True).count(), 1)
        stale_status, stale = self.call("push_block_html", {
            "site": "suite-alpha", "instance_id": "blk_11111111",
            "html": before["html"].replace("One", "Again"),
            "if_match": before["content_etag"],
        })
        self.assertEqual(stale_status, 409)
        self.assertTrue(stale["isError"])

    def test_structural_draft_tools_keep_undo_and_field_ids(self):
        original = copy.deepcopy(self.tenant.content)
        _, added = self.call("add_block", {
            "site": "suite-alpha", "block_type": "hero", "region": "main",
            "position": 1, "if_match": content_etag(original),
        })
        self.assertFalse(added["isError"], added)
        self.tenant.refresh_from_db()
        new_id = added["structuredContent"]["instance_id"]
        self.assertEqual(self.tenant.content["regions"]["main"][1]["id"], new_id)
        _, moved = self.call("move_block", {
            "site": "suite-alpha", "instance_id": new_id, "region": "main",
            "position": 0, "if_match": content_etag(self.tenant.content),
        })
        self.assertFalse(moved["isError"], moved)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["regions"]["main"][0]["id"], new_id)
        _, appended = self.call("append_block_richtext", {
            "site": "suite-alpha", "field": "blk_33333333.copy", "operation": "paragraph",
            "text": "Safe text", "if_match": content_etag(self.tenant.content),
        })
        self.assertFalse(appended["isError"], appended)
        self.tenant.refresh_from_db()
        self.assertIn("<p>Safe text</p>", self.tenant.content["regions"]["main"][-1]["fields"]["copy"])
        _, removed = self.call("remove_block", {
            "site": "suite-alpha", "instance_id": new_id,
            "if_match": content_etag(self.tenant.content),
        })
        self.assertFalse(removed["isError"], removed)
        self.tenant.refresh_from_db()
        self.assertNotIn(new_id, [b["id"] for b in self.tenant.content["regions"]["main"]])
        version = ContentVersion.objects.get(tenant=self.tenant, page__isnull=True)
        self.assertEqual(version.snapshot, original)
        self.assertEqual(self.template.html_source, SHELL)

    def test_inner_page_draft_write_and_cross_tenant_denial(self):
        _, read = self.call("get_block_html", {
            "site": "suite-alpha", "page": "about", "instance_id": "blk_44444444"})
        html = read["structuredContent"]["html"]
        _, saved = self.call("push_block_html", {
            "site": "suite-alpha", "page": "about", "instance_id": "blk_44444444",
            "html": html.replace("About", "Updated"),
            "if_match": read["structuredContent"]["content_etag"],
        })
        self.assertFalse(saved["isError"], saved)
        self.page.refresh_from_db()
        self.assertEqual(self.page.content["regions"]["main"][0]["fields"]["title"], "Updated")
        before = copy.deepcopy(self.other.content)
        _, denied = self.call("add_block", {
            "site": "suite-beta", "block_type": "hero", "region": "main", "position": 0,
            "if_match": content_etag(before),
        })
        self.assertTrue(denied["isError"])
        self.other.refresh_from_db()
        self.assertEqual(self.other.content, before)

    def test_bound_handoff_credential_edits_only_its_unpublished_draft(self):
        operator = get_user_model().objects.create_superuser(
            "suite-block-operator", "suite-block-operator@example.test", "x"
        )
        binding = link_suite_site(
            business_id=uuid.uuid4(), operation_key=uuid.uuid4(),
            tenant_id=self.other.pk, expected_owner_id=self.other.owner_id,
            operator=operator,
        )
        handoff_key = uuid.uuid4()
        with override_settings(SUITE_CREDENTIAL_HANDOFF_FERNET_KEY=Fernet.generate_key().decode()):
            receipt = begin_suite_credential_handoff(
                binding_id=binding.pk, operation_key=handoff_key,
                scopes={"read", "draft"}, operator=operator,
            )
            self.token = receipt.token
            pending = self.client.post(
                "/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1,
                    "method": "tools/call", "params": {"name": "add_block", "arguments": {
                        "site": "suite-beta", "block_type": "hero", "region": "main",
                        "position": 0, "if_match": content_etag(self.other.content),
                    }}}),
                content_type="application/json", HTTP_HOST="localhost",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
                HTTP_MCP_PROTOCOL_VERSION="2025-06-18", HTTP_ACCEPT="application/json",
            )
            self.assertEqual(pending.status_code, 401)
            acknowledge_suite_credential_handoff(
                binding_id=binding.pk, operation_key=handoff_key, operator=operator,
            )

        before_other = copy.deepcopy(self.other.content)
        before_alpha = copy.deepcopy(self.tenant.content)
        _, added = self.call("add_block", {
            "site": "suite-beta", "block_type": "hero", "region": "main",
            "position": 0, "if_match": content_etag(before_other),
        })
        self.assertFalse(added["isError"], added)
        self.other.refresh_from_db()
        self.assertEqual(len(self.other.content["regions"]["main"]), 1)
        _, denied = self.call("add_block", {
            "site": "suite-alpha", "block_type": "hero", "region": "main",
            "position": 0, "if_match": content_etag(before_alpha),
        })
        self.assertTrue(denied["isError"], denied)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, before_alpha)
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant).count(), 0)

    def test_block_html_rejects_field_id_drift_and_non_editable_template(self):
        _, read = self.call("get_block_html", {
            "site": "suite-alpha", "instance_id": "blk_11111111"})
        data = read["structuredContent"]
        before = copy.deepcopy(self.tenant.content)
        _, drift = self.call("push_block_html", {
            "site": "suite-alpha", "instance_id": "blk_11111111",
            "html": data["html"].replace("blk_11111111.title", "blk_11111111.renamed"),
            "if_match": data["content_etag"],
        })
        self.assertTrue(drift["isError"], drift)
        self.template.editing_mode = Template.EDITING_RAW
        self.template.save(update_fields=["editing_mode"])
        for name, args in (
            ("push_block_html", {"instance_id": "blk_11111111",
                                 "html": data["html"].replace("One", "Changed"),
                                 "if_match": data["content_etag"]}),
            ("add_block", {"block_type": "hero", "region": "main", "position": 0,
                           "if_match": data["content_etag"]}),
        ):
            with self.subTest(name=name):
                _, denied = self.call(name, {"site": "suite-alpha", **args})
                self.assertTrue(denied["isError"], denied)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, before)
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant).count(), 0)

    def test_published_resources_and_public_effects_remain_denied(self):
        self.tenant.is_published = True
        self.tenant.save(update_fields=["is_published"])
        self.page.is_published = True
        self.page.save(update_fields=["is_published"])
        home_before = copy.deepcopy(self.tenant.content)
        page_before = copy.deepcopy(self.page.content)
        for name, args in (
            ("push_block_html", {"instance_id": "blk_11111111", "html": HERO,
                                 "if_match": content_etag(home_before)}),
            ("add_block", {"block_type": "hero", "region": "main", "position": 0,
                           "if_match": content_etag(home_before)}),
            ("remove_block", {"instance_id": "blk_11111111",
                              "if_match": content_etag(home_before)}),
            ("move_block", {"instance_id": "blk_11111111", "region": "main", "position": 1,
                            "if_match": content_etag(home_before)}),
            ("append_block_richtext", {"field": "blk_33333333.copy", "operation": "paragraph",
                                       "text": "No", "if_match": content_etag(home_before)}),
            ("publish_site", {}),
            ("add_custom_domain", {"domain": "suite.example.test"}),
        ):
            with self.subTest(name=name):
                _, result = self.call(name, {"site": "suite-alpha", **args})
                self.assertTrue(result["isError"], result)
        _, inner = self.call("add_block", {
            "site": "suite-alpha", "page": "about", "block_type": "hero",
            "region": "main", "position": 1, "if_match": content_etag(page_before),
        })
        self.assertTrue(inner["isError"], inner)
        self.tenant.refresh_from_db()
        self.page.refresh_from_db()
        self.assertEqual(self.tenant.content, home_before)
        self.assertEqual(self.page.content, page_before)
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant).count(), 0)
