"""CMS-34 — publish_page MCP tool (superuser-only inner-page publish)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from api.models import McpAuditLog
from core.models import Page, Template, Tenant, TenantMembership


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
class PublishPageToolTests(TestCase):
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
        self.page_template = Template.objects.create(
            name="About",
            html_source=SAMPLE_HTML,
            tenant=self.tenant,
        )
        self.page = Page.objects.create(
            tenant=self.tenant,
            template=self.page_template,
            title="About",
            slug="about",
            content={},
            is_published=False,
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

    def test_happy_path_flips_is_published_and_renders(self):
        before = self.client.get("/site/existing/about/")
        self.assertEqual(before.status_code, 404)

        r = self._call("publish_page", {"site": "existing", "page": "about"})
        self.assertEqual(r.status_code, 200, r.content)
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        self.assertTrue(sc["published"])
        self.assertEqual(sc["site"], "existing")
        self.assertEqual(sc["page"], "about")
        self.assertEqual(sc["url"], "https://existing.sites.example.test/about/")

        self.page.refresh_from_db()
        self.assertTrue(self.page.is_published)

        # The page is published, but a published inner page only renders publicly
        # once the SITE is published too (C1). Publish the site, then it renders.
        self.tenant.is_published = True
        self.tenant.save(update_fields=["is_published"])
        rendered = self.client.get("/site/existing/about/")
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(b"Welcome", rendered.content)

    def test_page_not_public_while_site_unpublished(self):
        """An unpublished site must not leak any inner page to the public, even a
        per-page-published one — matches the dashboard Unpublish copy (C1).
        publish_page still succeeds (it flips Page.is_published), but the page
        stays 404 to anonymous visitors until the SITE is published too."""
        self.assertFalse(self.tenant.is_published)
        r = self._call("publish_page", {"site": "existing", "page": "about"})
        self.assertFalse(r.json()["result"].get("isError", False))

        home = self.client.get("/site/existing/")
        self.assertEqual(home.status_code, 404)
        # Page is published, but the site is not → anonymous visitor gets 404.
        page = self.client.get("/site/existing/about/")
        self.assertEqual(page.status_code, 404)

        # Once the site is published, the published page becomes reachable.
        self.tenant.is_published = True
        self.tenant.save(update_fields=["is_published"])
        page_after = self.client.get("/site/existing/about/")
        self.assertEqual(page_after.status_code, 200)

    def test_non_superuser_denied_identically_to_nonexistent(self):
        """No enumeration: member/staff denial matches nonexistent site/page."""
        member_existing = self._call(
            "publish_page",
            {"site": "existing", "page": "about"},
            token="tok-member",
        )
        member_missing_page = self._call(
            "publish_page",
            {"site": "existing", "page": "nosuch"},
            token="tok-member",
        )
        member_missing_site = self._call(
            "publish_page", {"site": "nosuch", "page": "about"}, token="tok-member"
        )
        staff_existing = self._call(
            "publish_page",
            {"site": "existing", "page": "about"},
            token="tok-staff-member",
        )

        bodies = [
            member_existing.content,
            member_missing_page.content,
            member_missing_site.content,
            staff_existing.content,
        ]
        for body in bodies[1:]:
            self.assertEqual(bodies[0], body)

        result = member_existing.json()["result"]
        self.assertTrue(result["isError"])
        self.page.refresh_from_db()
        self.assertFalse(self.page.is_published)

    def test_unknown_site_and_unknown_page_refused_identically(self):
        unknown_site = self._call(
            "publish_page", {"site": "ghost", "page": "about"}
        )
        unknown_page = self._call(
            "publish_page", {"site": "existing", "page": "ghost"}
        )
        self.assertTrue(unknown_site.json()["result"]["isError"])
        self.assertEqual(unknown_site.content, unknown_page.content)

    def test_empty_html_refused_without_force(self):
        self.page_template.html_source = "   "
        self.page_template.save()

        r = self._call("publish_page", {"site": "existing", "page": "about"})
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        text = " ".join(
            c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
        ).lower()
        self.assertIn("force", text)
        self.page.refresh_from_db()
        self.assertFalse(self.page.is_published)

    def test_force_overrides_empty_html_guard(self):
        self.page_template.html_source = ""
        self.page_template.save()

        r = self._call(
            "publish_page", {"site": "existing", "page": "about", "force": True}
        )
        result = r.json()["result"]
        self.assertFalse(result.get("isError", False), result)
        self.page.refresh_from_db()
        self.assertTrue(self.page.is_published)

    def test_idempotent_republish(self):
        first = self._call("publish_page", {"site": "existing", "page": "about"})
        second = self._call("publish_page", {"site": "existing", "page": "about"})
        self.assertFalse(first.json()["result"].get("isError", False))
        self.assertFalse(second.json()["result"].get("isError", False))
        self.page.refresh_from_db()
        self.assertTrue(self.page.is_published)

    def test_missing_page_argument_refused_not_site_home(self):
        """Omitting page means "the site home" in every other tool's
        convention, but publish_page has no site-home shortcut — refuse with
        a clear message rather than crashing or silently publishing nothing.
        """
        r = self._call("publish_page", {"site": "existing"})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertTrue(result["isError"])
        text = " ".join(
            c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
        ).lower()
        self.assertIn("publish_site", text)

        r_null = self._call(
            "publish_page", {"site": "existing", "page": None}
        )
        result_null = r_null.json()["result"]
        self.assertTrue(result_null["isError"])

    def test_tools_list_advertises_publish_page_as_write(self):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = r.json()["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "publish_page")
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertFalse(tool["annotations"]["idempotentHint"])

    def test_audit_row_records_action_and_tenant(self):
        McpAuditLog.objects.all().delete()
        r = self._call("publish_page", {"site": "existing", "page": "about"})
        self.assertFalse(r.json()["result"].get("isError", False))

        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.tenant_id, self.tenant.pk)
        self.assertEqual(row.action, "publish_page")
        self.assertEqual(row.performed_via, "MCP")

    def test_non_superuser_denial_leaves_audit_without_tenant(self):
        McpAuditLog.objects.all().delete()
        r = self._call(
            "publish_page",
            {"site": "existing", "page": "about"},
            token="tok-member",
        )
        self.assertTrue(r.json()["result"]["isError"])
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "publish_page")
        self.assertIsNone(row.tenant_id)
