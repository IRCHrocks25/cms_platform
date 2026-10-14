"""Template-level fields remain editable alongside block instances."""

import json

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import BlockType, Template, Tenant, TenantMembership
from core.renderer import render_page_from_blocks
from core.services import blocks


SHELL = """<!doctype html><html><body>
<div id="scorecardModal"><div class="roles" data-section="scorecard_popup"
 data-label="Scorecard popup">
 <a class="role" data-quiz="corporate" data-edit="scorecard_popup.corporate_url"
 data-type="link" href="https://example.com/quiz"><span class="r-title"
 data-edit="scorecard_popup.corporate_label" data-type="text">Corporate Director</span></a>
</div></div>
<main data-region="main"></main>
</body></html>"""

BLOCK = """<section data-block="hero" data-label="Hero">
<h1 data-edit="hero.title" data-type="text">Welcome</h1>
</section>"""


@override_settings(
    TENANT_BASE_DOMAIN="localhost",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ShellEditorFieldTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="x")
        self.template = Template.objects.create(
            name="Scorecard shell", html_source=SHELL,
            editing_mode=Template.EDITING_EDITABLE,
        )
        block = BlockType.objects.create(html_source=BLOCK)
        self.template.allowed_block_types.add(block)
        self.tenant = Tenant.objects.create(
            name="Scorecard site", subdomain="scorecard", owner=self.owner,
            template=self.template,
            content={"regions": {"main": [{
                "id": "blk_abcdef01", "type": "hero", "fields": {},
            }]}},
        )
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.owner, role=TenantMembership.ROLE_OWNER,
        )
        self.client = Client(HTTP_HOST="scorecard.localhost")
        self.client.force_login(self.owner)

    def test_shell_popup_fields_and_block_instance_are_editable(self):
        editor = self.client.get(reverse("dashboard:tenant_home"))
        self.assertEqual(editor.status_code, 200)
        self.assertContains(editor, 'data-field-id="blk_abcdef01.title"')
        self.assertContains(editor, 'data-field-id="scorecard_popup.corporate_label"')
        self.assertContains(editor, 'data-field-id="scorecard_popup.corporate_url"')
        sidebar = BeautifulSoup(editor.content, "lxml")
        self.assertEqual(sidebar.select_one('[data-jump="scorecard_popup"]')
                         .get("data-static-section"), "1")
        self.assertIsNone(sidebar.select_one('[data-jump="blk_abcdef01"]')
                          .get("data-static-section"))

        content = dict(self.tenant.content)
        content["scorecard_popup"] = {
            "corporate_label": "Portfolio Director",
            "corporate_url": "https://example.org/new-quiz",
        }
        saved = self.client.post(
            reverse("dashboard:tenant_save_self"),
            data=json.dumps({"content": content}),
            content_type="application/json",
        )
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["ok"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["scorecard_popup"], content["scorecard_popup"])

        reloaded = self.client.get(reverse("dashboard:tenant_home"))
        self.assertContains(reloaded, 'data-field-id="scorecard_popup.corporate_label"')
        rendered = render_page_from_blocks(
            self.template.html_source, self.tenant.content,
            blocks.catalog_for_template(self.template),
        )
        role = BeautifulSoup(rendered, "lxml").select_one("[data-quiz=corporate]")
        self.assertEqual(role["href"], "https://example.org/new-quiz")
        self.assertEqual(role.select_one(".r-title").get_text(strip=True),
                         "Portfolio Director")
