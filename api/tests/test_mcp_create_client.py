"""CMS-8 + CMS-11 — create_client_account MCP tool + one-time secret return."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.models import McpAuditLog
from core.models import CustomDomain, Template, Tenant, TenantMembership


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
class CreateClientAccountToolTests(TestCase):
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
        # Existing site so staff can hold a membership (scoped, not platform).
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

    def _call(self, arguments: dict, *, token: str = "tok-admin"):
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_client_account",
                    "arguments": arguments,
                },
            },
            token=token,
        )

    def _valid_args(self, **overrides):
        args = {
            "name": "New Co",
            "subdomain": "newco",
            "username": "newco-owner",
            "email": "owner@newco.test",
            "template_id": self.template.pk,
        }
        args.update(overrides)
        return args

    def test_superuser_creates_tenant_and_returns_site_url(self):
        r = self._call(self._valid_args())
        self.assertEqual(r.status_code, 200, r.content)
        payload = r.json()
        self.assertNotIn("error", payload)
        result = payload["result"]
        self.assertFalse(result.get("isError", False))
        sc = result["structuredContent"]
        self.assertEqual(sc["subdomain"], "newco")
        self.assertEqual(sc["username"], "newco-owner")
        self.assertIn("password", sc)
        self.assertEqual(len(sc["password"]), 16)
        self.assertEqual(sc["site_url"], "https://newco.sites.example.test/")

        tenant = Tenant.objects.get(subdomain="newco")
        self.assertEqual(tenant.name, "New Co")
        self.assertFalse(tenant.is_published)
        # Library templates are cloned into the new tenant (§8).
        self.assertNotEqual(tenant.template_id, self.template.pk)
        self.assertEqual(tenant.template.cloned_from_id, self.template.pk)
        self.assertEqual(tenant.template.tenant_id, tenant.pk)
        self.assertEqual(sc["template_id"], tenant.template_id)
        owner = User.objects.get(username="newco-owner")
        self.assertEqual(tenant.owner_id, owner.pk)
        self.assertTrue(owner.check_password(sc["password"]))
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=tenant,
                user=owner,
                role=TenantMembership.ROLE_OWNER,
            ).exists()
        )

    def test_password_returned_once_absent_from_audit_and_logs(self):
        with self.assertLogs("api.mcp", level=logging.DEBUG) as captured:
            # Ensure the logger is enabled even if no DEBUG handlers exist yet.
            logging.getLogger("api.mcp").debug("probe")
            r = self._call(self._valid_args(subdomain="secretco", username="sec-owner"))
        self.assertEqual(r.status_code, 200, r.content)
        password = r.json()["result"]["structuredContent"]["password"]
        self.assertTrue(password)

        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        # Model has no secret field — still assert the password is nowhere
        # in the persisted row's concrete values.
        for field in row._meta.fields:
            value = getattr(row, field.name)
            self.assertNotEqual(value, password)
            self.assertNotIn(password, str(value))

        joined_logs = "\n".join(captured.output)
        self.assertNotIn(password, joined_logs)

    def test_audit_row_stamps_new_tenant_and_actor(self):
        r = self._call(self._valid_args(subdomain="audited", username="aud-owner"))
        self.assertFalse(r.json()["result"].get("isError", False))
        tenant = Tenant.objects.get(subdomain="audited")
        row = McpAuditLog.objects.get()
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.tenant_id, tenant.pk)
        self.assertEqual(row.action, "create_client_account")
        self.assertEqual(row.performed_via, "MCP")

    def test_non_superuser_member_denied_identically(self):
        member_r = self._call(self._valid_args(), token="tok-member")
        staff_r = self._call(self._valid_args(), token="tok-staff-member")
        self.assertEqual(member_r.status_code, 200)
        self.assertEqual(staff_r.status_code, 200)
        member_body = member_r.content
        staff_body = staff_r.content
        self.assertEqual(member_body, staff_body)
        result = member_r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(Tenant.objects.filter(subdomain="newco").exists())
        # Denial still audits, without leaking a tenant stamp.
        self.assertEqual(McpAuditLog.objects.count(), 2)
        for row in McpAuditLog.objects.all():
            self.assertEqual(row.action, "create_client_account")
            self.assertIsNone(row.tenant_id)

    def test_requires_template_id_explicitly(self):
        args = self._valid_args()
        del args["template_id"]
        r = self._call(args)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["error"]["code"], -32602)
        self.assertFalse(Tenant.objects.filter(subdomain="newco").exists())

    def test_tools_list_advertises_create_client_account_as_write(self):
        r = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token="tok-admin",
        )
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("create_client_account", names)
        tool = next(t for t in tools if t["name"] == "create_client_account")
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])

    def test_rejects_client_owned_template_id(self):
        owned = Template.objects.create(
            name="Client private",
            html_source=SAMPLE_HTML,
            tenant=self.existing,
        )
        r = self._call(self._valid_args(template_id=owned.pk, subdomain="nope"))
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        text = " ".join(
            c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
        ).lower()
        self.assertIn("client-owned", text)
        self.assertFalse(Tenant.objects.filter(subdomain="nope").exists())

    def test_clones_library_template_on_create(self):
        r = self._call(self._valid_args(subdomain="clone-me", username="clone-owner"))
        self.assertFalse(r.json()["result"].get("isError", False))
        tenant = Tenant.objects.get(subdomain="clone-me")
        self.assertEqual(tenant.template.cloned_from_id, self.template.pk)
        self.assertIsNone(self.template.tenant_id)

    # ------------------------------------------------------------------ #
    # custom_domain at creation (CMS-37)                                  #
    # ------------------------------------------------------------------ #

    def test_custom_domain_creates_unverified_row_linked_to_new_tenant(self):
        r = self._call(
            self._valid_args(
                subdomain="withdomain",
                username="withdomain-owner",
                custom_domain="www.withdomain.com",
            )
        )
        self.assertFalse(r.json()["result"].get("isError", False), r.json())
        tenant = Tenant.objects.get(subdomain="withdomain")

        row = CustomDomain.objects.get(domain="www.withdomain.com")
        self.assertEqual(row.tenant_id, tenant.pk)
        self.assertFalse(row.is_verified)

    def test_no_custom_domain_creates_no_row(self):
        r = self._call(
            self._valid_args(subdomain="nodomain", username="nodomain-owner")
        )
        self.assertFalse(r.json()["result"].get("isError", False), r.json())
        self.assertFalse(CustomDomain.objects.exists())

    def test_invalid_custom_domain_refuses_whole_creation(self):
        r = self._call(
            self._valid_args(
                subdomain="baddomain",
                username="baddomain-owner",
                custom_domain="not a domain",
            )
        )
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(Tenant.objects.filter(subdomain="baddomain").exists())
        self.assertFalse(User.objects.filter(username="baddomain-owner").exists())
        self.assertFalse(CustomDomain.objects.exists())

    def test_domain_registered_to_another_tenant_refuses_whole_creation(self):
        CustomDomain.objects.create(
            tenant=self.existing, domain="taken.com", is_verified=True
        )
        r = self._call(
            self._valid_args(
                subdomain="latecomer",
                username="latecomer-owner",
                custom_domain="taken.com",
            )
        )
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertFalse(Tenant.objects.filter(subdomain="latecomer").exists())
        self.assertFalse(User.objects.filter(username="latecomer-owner").exists())
        self.assertEqual(
            CustomDomain.objects.filter(domain="taken.com").count(), 1
        )
        self.assertEqual(
            CustomDomain.objects.get(domain="taken.com").tenant_id,
            self.existing.pk,
        )
