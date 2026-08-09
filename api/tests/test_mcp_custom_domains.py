"""CMS-32 — add_custom_domain / verify_custom_domain MCP tools.

DNS resolution is always mocked via ``core.services.custom_domains.
resolve_a_records`` — these tests must never make a real DNS query.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.models import McpAuditLog
from core.models import CustomDomain, Template, Tenant, TenantMembership


User = get_user_model()

CLAUDE_CLIENT_ID = "claude-test"
PROTOCOL = "2025-06-18"
TARGET_IP = "203.0.113.7"

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
    CUSTOM_DOMAIN_TARGET_IP=TARGET_IP,
)
class CustomDomainToolsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.staff = User.objects.create_user(
            "ops", "ops@ex.com", "x", is_staff=True
        )
        self.template = Template.objects.create(
            name="Neutral starter", html_source=SAMPLE_HTML
        )
        owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.tenant = Tenant.objects.create(
            name="Existing",
            subdomain="existing",
            template=self.template,
            owner=owner,
            content={},
            is_published=False,
        )
        self.other = Tenant.objects.create(
            name="Other",
            subdomain="other",
            template=self.template,
            owner=owner,
            content={},
            is_published=False,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
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

    # ------------------------------------------------------------------ #
    # add_custom_domain                                                   #
    # ------------------------------------------------------------------ #

    def test_add_happy_path_creates_unverified_domain(self):
        r = self._call(
            "add_custom_domain", {"site": "existing", "domain": "www.acme.com"}
        )
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertEqual(sc["site"], "existing")
        self.assertEqual(sc["domain"], "www.acme.com")
        self.assertFalse(sc["is_verified"])

        row = CustomDomain.objects.get(domain="www.acme.com")
        self.assertEqual(row.tenant_id, self.tenant.pk)
        self.assertFalse(row.is_verified)

    def test_add_normalises_trailing_dot_and_uppercase(self):
        r = self._call(
            "add_custom_domain",
            {"site": "existing", "domain": "WWW.Acme.COM."},
        )
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        self.assertEqual(result["structuredContent"]["domain"], "www.acme.com")
        self.assertTrue(
            CustomDomain.objects.filter(domain="www.acme.com").exists()
        )

    def test_add_invalid_domain_refused(self):
        r = self._call(
            "add_custom_domain", {"site": "existing", "domain": "not a domain"}
        )
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(CustomDomain.objects.exists())

    def test_add_duplicate_domain_refused(self):
        CustomDomain.objects.create(
            tenant=self.other, domain="taken.com", is_verified=True
        )
        r = self._call(
            "add_custom_domain", {"site": "existing", "domain": "taken.com"}
        )
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            CustomDomain.objects.filter(domain="taken.com").count(), 1
        )
        self.assertEqual(
            CustomDomain.objects.get(domain="taken.com").tenant_id, self.other.pk
        )

    def test_add_non_superuser_denied_identically_to_nonexistent_site(self):
        member = self._call(
            "add_custom_domain",
            {"site": "existing", "domain": "www.acme.com"},
            token="tok-member",
        )
        staff = self._call(
            "add_custom_domain",
            {"site": "existing", "domain": "www.acme.com"},
            token="tok-staff-member",
        )
        missing = self._call(
            "add_custom_domain",
            {"site": "ghost", "domain": "www.acme.com"},
            token="tok-member",
        )
        self.assertEqual(member.content, staff.content)
        self.assertEqual(member.content, missing.content)
        self.assertTrue(member.json()["result"]["isError"])
        self.assertFalse(CustomDomain.objects.exists())

    def test_add_unknown_site_refused(self):
        r = self._call(
            "add_custom_domain", {"site": "ghost", "domain": "www.acme.com"}
        )
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(CustomDomain.objects.exists())

    def test_add_audit_row_records_action_and_tenant(self):
        McpAuditLog.objects.all().delete()
        r = self._call(
            "add_custom_domain", {"site": "existing", "domain": "www.acme.com"}
        )
        self.assertFalse(r.json()["result"].get("isError", False))
        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.tenant_id, self.tenant.pk)
        self.assertEqual(row.action, "add_custom_domain")

    def test_add_non_superuser_denial_leaves_audit_without_tenant(self):
        McpAuditLog.objects.all().delete()
        r = self._call(
            "add_custom_domain",
            {"site": "existing", "domain": "www.acme.com"},
            token="tok-member",
        )
        self.assertTrue(r.json()["result"]["isError"])
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "add_custom_domain")
        self.assertIsNone(row.tenant_id)

    def test_tools_list_advertises_add_custom_domain_as_write(self):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r.json()["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "add_custom_domain")
        self.assertFalse(tool["annotations"]["readOnlyHint"])

    # ------------------------------------------------------------------ #
    # verify_custom_domain                                                #
    # ------------------------------------------------------------------ #

    def test_verify_resolves_to_target_ip_marks_verified(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records",
            return_value=[TARGET_IP],
        ):
            r = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "www.acme.com"},
            )
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["is_verified"])
        self.assertEqual(sc["resolved"], [TARGET_IP])
        self.assertEqual(sc["target_ip"], TARGET_IP)

        row = CustomDomain.objects.get(domain="www.acme.com")
        self.assertTrue(row.is_verified)

    def test_verify_resolves_elsewhere_stays_unverified_and_names_ips(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        other_ips = ["104.21.1.1", "172.67.2.2"]
        with patch(
            "core.services.custom_domains.resolve_a_records",
            return_value=other_ips,
        ):
            r = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "www.acme.com"},
            )
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertFalse(sc["is_verified"])
        self.assertEqual(sorted(sc["resolved"]), sorted(other_ips))

        row = CustomDomain.objects.get(domain="www.acme.com")
        self.assertFalse(row.is_verified)

    def test_verify_no_a_record_stays_unverified_with_empty_resolved(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records", return_value=[]
        ):
            r = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "www.acme.com"},
            )
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertFalse(sc["is_verified"])
        self.assertEqual(sc["resolved"], [])

    def test_verify_non_superuser_denied_identically_to_nonexistent_site(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records",
            return_value=[TARGET_IP],
        ):
            member = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "www.acme.com"},
                token="tok-member",
            )
            staff = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "www.acme.com"},
                token="tok-staff-member",
            )
            missing = self._call(
                "verify_custom_domain",
                {"site": "ghost", "domain": "www.acme.com"},
                token="tok-member",
            )
        self.assertEqual(member.content, staff.content)
        self.assertEqual(member.content, missing.content)
        self.assertTrue(member.json()["result"]["isError"])
        self.assertFalse(
            CustomDomain.objects.get(domain="www.acme.com").is_verified
        )

    def test_verify_unknown_site_refused(self):
        with patch(
            "core.services.custom_domains.resolve_a_records",
            return_value=[TARGET_IP],
        ):
            r = self._call(
                "verify_custom_domain",
                {"site": "ghost", "domain": "www.acme.com"},
            )
        result = r.json()["result"]
        self.assertTrue(result["isError"])

    def test_verify_unknown_domain_on_known_site_refused(self):
        r = self._call(
            "verify_custom_domain",
            {"site": "existing", "domain": "nosuch.com"},
        )
        result = r.json()["result"]
        self.assertTrue(result["isError"])

    def test_verify_rejects_domain_registered_to_another_tenant(self):
        """A domain that exists but belongs to a different site is refused as
        if unregistered — this tool never acts across tenant boundaries."""
        CustomDomain.objects.create(
            tenant=self.other, domain="foreign.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records",
            return_value=[TARGET_IP],
        ):
            r = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "foreign.com"},
            )
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(
            CustomDomain.objects.get(domain="foreign.com").is_verified
        )

    def test_verify_audit_row_records_action_and_tenant(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        McpAuditLog.objects.all().delete()
        with patch(
            "core.services.custom_domains.resolve_a_records",
            return_value=[TARGET_IP],
        ):
            r = self._call(
                "verify_custom_domain",
                {"site": "existing", "domain": "www.acme.com"},
            )
        self.assertFalse(r.json()["result"].get("isError", False))
        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.tenant_id, self.tenant.pk)
        self.assertEqual(row.action, "verify_custom_domain")

    def test_verify_non_superuser_denial_leaves_audit_without_tenant(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        McpAuditLog.objects.all().delete()
        r = self._call(
            "verify_custom_domain",
            {"site": "existing", "domain": "www.acme.com"},
            token="tok-member",
        )
        self.assertTrue(r.json()["result"]["isError"])
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "verify_custom_domain")
        self.assertIsNone(row.tenant_id)

    def test_verify_tool_description_warns_about_proxied_dns(self):
        """CMS-32: without this in the description, a proxied (Cloudflare
        orange-cloud) A record looks like a propagation delay forever."""
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r.json()["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "verify_custom_domain")
        description = tool["description"].lower()
        self.assertIn("dns-only", description)
        self.assertIn("cloudflare", description)
        self.assertIn("proxied", description)

    # ------------------------------------------------------------------ #
    # create_client_account's custom_domain param (CMS-37)               #
    # ------------------------------------------------------------------ #

    def test_create_client_account_domain_param_describes_unverified_state(self):
        """CMS-37: create_tenant_account now attaches a real but unverified
        CustomDomain row — the tool description must say so, and must repeat
        the DNS-only warning so callers don't wait forever on a proxied
        record (same failure mode as verify_custom_domain)."""
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r.json()["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "create_client_account")
        description = tool["inputSchema"]["properties"]["custom_domain"][
            "description"
        ].lower()
        self.assertIn("unverified", description)
        self.assertIn("dns-only", description)
        self.assertIn("cloudflare", description)
        self.assertIn("proxied", description)
        self.assertIn("custom_domain_target_ip", description)
