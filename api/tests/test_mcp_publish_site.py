"""CMS-31 — publish_site MCP tool (superuser-only live publish)."""

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


def _make_token(user, *, token="tok-test", application=None):
    if application is None:
        application = _make_app()
    return AccessToken.objects.create(
        user=user,
        application=application,
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
class PublishSiteToolTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.staff = User.objects.create_user(
            "ops", "ops@ex.com", "x", is_staff=True
        )
        self.template = Template.objects.create(
            name="Neutral starter",
            html_source=SAMPLE_HTML,
        )
        owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.existing = Tenant.objects.create(
            name="Existing",
            subdomain="existing",
            template=self.template,
            owner=owner,
            content={},
            is_published=False,
        )
        TenantMembership.objects.create(
            tenant=self.existing,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        TenantMembership.objects.create(
            tenant=self.existing,
            user=self.staff,
            role=TenantMembership.ROLE_EDITOR,
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

    def _call(self, name: str, arguments: dict, *, token: str = "tok-admin"):
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            token=token,
        )

    def _create_client(self, **overrides):
        args = {
            "name": "New Co",
            "subdomain": "newco",
            "username": "newco-owner",
            "email": "owner@newco.test",
            "template_id": self.template.pk,
        }
        args.update(overrides)
        r = self._call("create_client_account", args)
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        return result["structuredContent"]

    def test_create_then_publish_renders_public_url(self):
        """AC1: MCP-created site publishes and the returned URL actually renders."""
        created = self._create_client(subdomain="liveco", username="live-owner")
        self.assertFalse(created["published"])

        # Fresh mint is still template defaults — customize before publish.
        tenant = Tenant.objects.get(subdomain="liveco")
        tenant.content = {"hero": {"title": "Live headline"}}
        tenant.save(update_fields=["content", "updated_at"])

        before = self.client.get("/site/liveco/")
        self.assertEqual(before.status_code, 404)

        r = self._call("publish_site", {"site": "liveco"})
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["published"])
        self.assertEqual(sc["url"], "https://liveco.sites.example.test/")

        tenant.refresh_from_db()
        self.assertTrue(tenant.is_published)

        rendered = self.client.get("/site/liveco/")
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(b"Live headline", rendered.content)

    def test_non_superuser_denied_identically_to_nonexistent(self):
        """AC2: member/staff denial matches nonexistent — no enumeration."""
        self._create_client(subdomain="secret", username="sec-owner")

        member_existing = self._call(
            "publish_site", {"site": "secret"}, token="tok-member"
        )
        member_missing = self._call(
            "publish_site", {"site": "nosuch"}, token="tok-member"
        )
        staff_existing = self._call(
            "publish_site", {"site": "secret"}, token="tok-staff-member"
        )
        staff_missing = self._call(
            "publish_site", {"site": "nosuch"}, token="tok-staff-member"
        )

        bodies = [
            member_existing.content,
            member_missing.content,
            staff_existing.content,
            staff_missing.content,
        ]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[0], bodies[2])
        self.assertEqual(bodies[0], bodies[3])

        result = member_existing.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(Tenant.objects.get(subdomain="secret").is_published)

    def test_audit_row_records_action_and_tenant(self):
        """AC3: one audit row with action + tenant on successful publish."""
        self._create_client(subdomain="audited", username="aud-owner")
        tenant = Tenant.objects.get(subdomain="audited")
        tenant.content = {"hero": {"title": "Edited"}}
        tenant.save(update_fields=["content", "updated_at"])
        McpAuditLog.objects.all().delete()

        r = self._call("publish_site", {"site": "audited"})
        self.assertFalse(r.json()["result"].get("isError", False))

        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.tenant_id, tenant.pk)
        self.assertEqual(row.action, "publish_site")
        self.assertEqual(row.performed_via, "MCP")

    def test_defaults_guard_refuses_without_force(self):
        """AC4: publishing unchanged template defaults is refused unless force."""
        self._create_client(subdomain="defaults", username="def-owner")
        r = self._call("publish_site", {"site": "defaults"})
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        text = " ".join(
            c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
        ).lower()
        self.assertIn("force", text)
        self.assertFalse(Tenant.objects.get(subdomain="defaults").is_published)

    def test_force_overrides_defaults_guard(self):
        """AC4: force=true publishes even when content is still defaults."""
        self._create_client(subdomain="forced", username="force-owner")
        r = self._call("publish_site", {"site": "forced", "force": True})
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        self.assertTrue(Tenant.objects.get(subdomain="forced").is_published)
        rendered = self.client.get("/site/forced/")
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(b"Welcome", rendered.content)

    def test_unpublish_is_not_offered(self):
        """AC4 decision: no unpublish tool — taking a live site offline via chat
        is worse than publishing; dashboard toggle remains the off-ramp."""
        listed = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token="tok-admin",
        )
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        self.assertIn("publish_site", names)
        self.assertNotIn("unpublish_site", names)
        self.assertNotIn("unpublish", names)

        missing = self._call("unpublish_site", {"site": "existing"})
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.json()["error"]["code"], -32601)

    def test_tools_list_advertises_publish_site_as_write(self):
        r = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token="tok-admin",
        )
        tools = r.json()["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "publish_site")
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])

    def test_non_superuser_denial_leaves_audit_without_tenant(self):
        self._create_client(subdomain="leak", username="leak-owner")
        McpAuditLog.objects.all().delete()
        r = self._call("publish_site", {"site": "leak"}, token="tok-member")
        self.assertTrue(r.json()["result"]["isError"])
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "publish_site")
        self.assertIsNone(row.tenant_id)
