"""CMS-35 — list_templates MCP tool: agency-library templates only."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.models import McpAuditLog
from core.models import Template, Tenant, TenantMembership


User = get_user_model()

CLAUDE_CLIENT_ID = "claude-test"
PROTOCOL = "2025-06-18"

SAMPLE_HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
</section>
"""


def _make_app():
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
    return app


def _make_token(user, *, token="tok-test"):
    return AccessToken.objects.create(
        user=user,
        application=_make_app(),
        token=token,
        expires=timezone.now() + timedelta(hours=1),
        scope="read write",
    )


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="sites.example.test",
    ALLOWED_HOSTS=["*"],
)
class ListTemplatesToolTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.staff = User.objects.create_user(
            "ops", "ops@ex.com", "x", is_staff=True
        )
        # Non-superusers must resolve to *some* scope or the token itself is
        # rejected (401) before reaching the tool — give both a membership.
        # The seed template is re-parented to its tenant so it never counts
        # towards the library, keeping this setUp neutral for every test.
        seed_template = Template.objects.create(
            name="Seed", html_source=SAMPLE_HTML
        )
        existing_owner = User.objects.create_user("existing-owner", "eo@ex.com", "x")
        existing = Tenant.objects.create(
            name="Existing",
            subdomain="existing",
            template=seed_template,
            owner=existing_owner,
            content={},
            is_published=False,
        )
        seed_template.tenant = existing
        seed_template.save(update_fields=["tenant"])
        TenantMembership.objects.create(
            tenant=existing, user=self.member, role=TenantMembership.ROLE_EDITOR
        )
        TenantMembership.objects.create(
            tenant=existing, user=self.staff, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(self.admin, token="tok-admin")
        _make_token(self.member, token="tok-member")
        _make_token(self.staff, token="tok-staff-member")

    def _post(self, body: dict, *, token: str = "tok-admin"):
        return self.client.post(
            "/mcp",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_ACCEPT="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
        )

    def _call(self, *, token: str = "tok-admin"):
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_templates", "arguments": {}},
            },
            token=token,
        )

    def test_returns_library_templates_only(self):
        library = Template.objects.create(
            name="Restaurant starter",
            slug="restaurant-starter",
            description="A starter for restaurants",
            html_source=SAMPLE_HTML,
        )
        owner = User.objects.create_user("owned-owner", "oo@ex.com", "x")
        tenant = Tenant.objects.create(
            name="Owned Co",
            subdomain="ownedco",
            template=library,
            owner=owner,
            content={},
            is_published=False,
        )
        Template.objects.create(
            name="Client private",
            html_source=SAMPLE_HTML,
            tenant=tenant,
        )

        r = self._call()
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False))
        templates = result["structuredContent"]["templates"]

        names = {t["name"] for t in templates}
        self.assertIn("Restaurant starter", names)
        self.assertNotIn("Client private", names)

        row = next(t for t in templates if t["name"] == "Restaurant starter")
        self.assertEqual(row["id"], library.pk)
        self.assertEqual(row["slug"], "restaurant-starter")
        self.assertEqual(row["description"], "A starter for restaurants")
        self.assertEqual(row["editing_mode"], Template.EDITING_RAW)
        self.assertEqual(
            set(row.keys()),
            {"id", "name", "slug", "description", "editing_mode"},
        )

    def test_empty_library_returns_empty_list_not_error(self):
        self.assertEqual(Template.objects.filter(tenant__isnull=True).count(), 0)
        r = self._call()
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False))
        self.assertEqual(result["structuredContent"]["templates"], [])

    def test_superuser_allowed_non_superuser_denied_identically(self):
        Template.objects.create(name="Lib", html_source=SAMPLE_HTML)

        admin_r = self._call(token="tok-admin")
        self.assertEqual(admin_r.status_code, 200)
        self.assertFalse(admin_r.json()["result"]["isError"])

        member_r = self._call(token="tok-member")
        staff_r = self._call(token="tok-staff-member")
        self.assertEqual(member_r.status_code, 200)
        self.assertEqual(staff_r.status_code, 200)
        self.assertEqual(member_r.content, staff_r.content)
        self.assertTrue(member_r.json()["result"]["isError"])
        self.assertTrue(staff_r.json()["result"]["isError"])

    def test_listed_as_readonly_in_tools_list(self):
        r = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token="tok-admin",
        )
        tools = {t["name"]: t for t in r.json()["result"]["tools"]}
        self.assertIn("list_templates", tools)
        tool = tools["list_templates"]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertIn("template_id", tool["description"])

    def test_audit_row_recorded_without_tenant(self):
        self._call()
        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "list_templates")
        self.assertIsNone(row.tenant_id)
        self.assertEqual(row.actor_id, self.admin.pk)
