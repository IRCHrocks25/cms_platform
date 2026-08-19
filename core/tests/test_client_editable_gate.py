"""CMS-27 §4.3 / §5: client editor + MCP refuse when not is_client_editable."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.mcp.content import content_etag
from core.models import Template, Tenant, TenantMembership


User = get_user_model()

HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Hi</h1>
</section>
"""

CLAUDE_CLIENT_ID = "claude-gate"
PROTOCOL = "2025-06-18"


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class ClientEditorGateTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("own", "o@ex.com", "x")
        self.tpl = Template.objects.create(
            name="Locked",
            html_source=HTML,
            editing_mode=Template.EDITING_RAW,
        )
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=self.tpl,
            owner=self.owner,
            content={"hero": {"title": "Hi"}},
        )
        self.tpl.tenant = self.tenant
        self.tpl.save(update_fields=["tenant"])
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.ROLE_OWNER,
        )

    def test_tenant_editor_shows_not_set_up_message(self):
        c = Client(HTTP_HOST="acme.localhost")
        c.force_login(self.owner)
        r = c.get(reverse("dashboard:tenant_home"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("isn’t set up for editing yet", body)
        self.assertIn("is-readonly", body)

    def test_tenant_save_refused_when_not_editable(self):
        c = Client(HTTP_HOST="acme.localhost")
        c.force_login(self.owner)
        r = c.post(
            reverse("dashboard:tenant_save_self"),
            data=json.dumps({"content": {"hero": {"title": "Nope"}}}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["hero"]["title"], "Hi")


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="sites.example.test",
    ALLOWED_HOSTS=["*"],
)
class PatchContentEditableGateTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("ed", "e@ex.com", "x")
        self.admin = User.objects.create_superuser("adm", "a@ex.com", "x")
        self.tpl = Template.objects.create(
            name="Raw",
            html_source=HTML,
            editing_mode=Template.EDITING_RAW,
        )
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.member,
            content={"hero": {"title": "A"}},
            is_published=True,
        )
        self.tpl.tenant = self.tenant
        self.tpl.save(update_fields=["tenant"])
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        app, _ = Application.objects.get_or_create(
            name="Claude",
            defaults={
                "client_id": CLAUDE_CLIENT_ID,
                "client_type": Application.CLIENT_CONFIDENTIAL,
                "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
                "redirect_uris": "https://example.test/callback",
            },
        )
        if app.client_id != CLAUDE_CLIENT_ID:
            app.client_id = CLAUDE_CLIENT_ID
            app.save(update_fields=["client_id"])
        AccessToken.objects.create(
            user=self.member,
            application=app,
            token="tok-member",
            expires=timezone.now() + timedelta(hours=1),
            scope="read write",
        )
        AccessToken.objects.create(
            user=self.admin,
            application=app,
            token="tok-admin",
            expires=timezone.now() + timedelta(hours=1),
            scope="read write",
        )
        self.client = Client()

    def _call(self, *, token):
        return self.client.post(
            "/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "patch_content",
                        "arguments": {
                            "site": "alpha",
                            "field": "hero.title",
                            "value": "X",
                            "if_match": content_etag(self.tenant.content),
                        },
                    },
                }
            ),
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
            HTTP_ACCEPT="application/json",
        )

    def test_member_refused_when_not_client_editable(self):
        r = self._call(token="tok-member")
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["hero"]["title"], "A")

    def test_superuser_still_can_patch(self):
        r = self._call(token="tok-admin")
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["hero"]["title"], "X")
