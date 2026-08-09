"""CMS-36 — delete_page MCP tool (superuser-only inner-page delete)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.models import McpAuditLog
from core.models import Page, Template, TemplateVersion, Tenant, TenantMembership


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
class DeletePageToolTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.staff = User.objects.create_user(
            "ops", "ops@ex.com", "x", is_staff=True
        )
        self.home_template = Template.objects.create(
            name="Home", html_source=SAMPLE_HTML
        )
        owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.tenant = Tenant.objects.create(
            name="Existing",
            subdomain="existing",
            template=self.home_template,
            owner=owner,
            content={"hero": {"title": "Home"}},
            is_published=True,
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
        self.page_template = Template.objects.create(
            name="About",
            html_source=SAMPLE_HTML,
            tenant=self.tenant,
        )
        TemplateVersion.objects.create(
            template=self.page_template,
            number=1,
            html_source=self.page_template.html_source,
            schema=self.page_template.schema or {},
            label="Initial",
        )
        self.page = Page.objects.create(
            tenant=self.tenant,
            template=self.page_template,
            title="About",
            slug="about",
            content={},
            is_published=True,
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

    def test_happy_path_removes_page_and_orphan_template_and_versions(self):
        page_template_id = self.page_template.pk
        r = self._call("delete_page", {"site": "existing", "page": "about"})
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["deleted"])
        self.assertTrue(sc["template_deleted"])
        self.assertEqual(sc["site"], "existing")
        self.assertEqual(sc["page"], "about")

        self.assertFalse(Page.objects.filter(pk=self.page.pk).exists())
        self.assertFalse(Template.objects.filter(pk=page_template_id).exists())
        self.assertFalse(
            TemplateVersion.objects.filter(template_id=page_template_id).exists()
        )

    def test_template_kept_when_still_referenced_by_tenant(self):
        """A page pointed at the tenant's own home template: page goes,
        template survives because the site home still needs it."""
        self.page.template = self.home_template
        self.page.save(update_fields=["template"])

        r = self._call("delete_page", {"site": "existing", "page": "about"})
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["deleted"])
        self.assertFalse(sc["template_deleted"])

        self.assertFalse(Page.objects.filter(pk=self.page.pk).exists())
        self.assertTrue(Template.objects.filter(pk=self.home_template.pk).exists())
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.template_id, self.home_template.pk)

    def test_template_kept_when_still_referenced_by_a_clone(self):
        clone = self.page_template.clone_for(self.tenant)
        self.assertEqual(clone.cloned_from_id, self.page_template.pk)

        r = self._call("delete_page", {"site": "existing", "page": "about"})
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["deleted"])
        self.assertFalse(sc["template_deleted"])

        self.assertTrue(Template.objects.filter(pk=self.page_template.pk).exists())
        clone.refresh_from_db()
        self.assertEqual(clone.cloned_from_id, self.page_template.pk)

    def test_template_kept_when_still_referenced_by_another_page(self):
        sibling = Page.objects.create(
            tenant=self.tenant,
            template=self.page_template,
            title="Services",
            slug="services",
            content={},
            is_published=False,
        )

        r = self._call("delete_page", {"site": "existing", "page": "about"})
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["deleted"])
        self.assertFalse(sc["template_deleted"])

        self.assertFalse(Page.objects.filter(pk=self.page.pk).exists())
        self.assertTrue(Page.objects.filter(pk=sibling.pk).exists())
        self.assertTrue(Template.objects.filter(pk=self.page_template.pk).exists())

    def test_deleting_the_home_is_refused(self):
        r = self._call("delete_page", {"site": "existing"})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        text = " ".join(
            c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
        ).lower()
        self.assertIn("home", text)

        r_null = self._call("delete_page", {"site": "existing", "page": None})
        self.assertTrue(r_null.json()["result"]["isError"])

        # Nothing was touched.
        self.assertTrue(Tenant.objects.filter(pk=self.tenant.pk).exists())
        self.assertTrue(Template.objects.filter(pk=self.home_template.pk).exists())
        self.assertTrue(Page.objects.filter(pk=self.page.pk).exists())

    def test_unknown_page_refused(self):
        r = self._call("delete_page", {"site": "existing", "page": "nosuch"})
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        self.assertTrue(Page.objects.filter(pk=self.page.pk).exists())

    def test_unknown_site_and_unknown_page_refused_identically(self):
        unknown_site = self._call(
            "delete_page", {"site": "ghost", "page": "about"}
        )
        unknown_page = self._call(
            "delete_page", {"site": "existing", "page": "ghost"}
        )
        self.assertTrue(unknown_site.json()["result"]["isError"])
        self.assertEqual(unknown_site.content, unknown_page.content)

    def test_non_superuser_denied_identically_to_nonexistent(self):
        member = self._call(
            "delete_page", {"site": "existing", "page": "about"}, token="tok-member"
        )
        staff = self._call(
            "delete_page",
            {"site": "existing", "page": "about"},
            token="tok-staff-member",
        )
        missing = self._call(
            "delete_page", {"site": "ghost", "page": "about"}, token="tok-member"
        )
        self.assertEqual(member.content, staff.content)
        self.assertEqual(member.content, missing.content)

        result = member.json()["result"]
        self.assertTrue(result["isError"])
        self.assertTrue(Page.objects.filter(pk=self.page.pk).exists())

    def test_tools_list_advertises_delete_page_as_write(self):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r.json()["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "delete_page")
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])

    def test_audit_row_records_action_and_tenant(self):
        McpAuditLog.objects.all().delete()
        r = self._call("delete_page", {"site": "existing", "page": "about"})
        self.assertFalse(r.json()["result"].get("isError", False))

        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.tenant_id, self.tenant.pk)
        self.assertEqual(row.action, "delete_page")
        self.assertEqual(row.performed_via, "MCP")

    def test_non_superuser_denial_leaves_audit_without_tenant(self):
        McpAuditLog.objects.all().delete()
        r = self._call(
            "delete_page",
            {"site": "existing", "page": "about"},
            token="tok-member",
        )
        self.assertTrue(r.json()["result"]["isError"])
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "delete_page")
        self.assertIsNone(row.tenant_id)
