"""CMS-30 acceptance — get_page_html pull + html etag alignment with push_page."""

from __future__ import annotations

import hashlib
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

HTML_RAW_V1 = "<html><body><h1>About us</h1><p>Plain copy</p></body></html>"
HTML_RAW_V2 = "<html><body><h1>About us (v2)</h1><p>Revised</p></body></html>"
HTML_RAW_V3 = "<html><body><h1>About us (v3)</h1><p>Again</p></body></html>"


def _html_etag(html: str) -> str:
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


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


def _make_tenant(owner, *, name, subdomain, html="<html></html>", content=None):
    tpl = Template.objects.create(
        name=f"tpl-{subdomain}",
        html_source=html,
        editing_mode=Template.EDITING_RAW,
        tenant=None,
    )
    tenant = Tenant.objects.create(
        name=name,
        subdomain=subdomain,
        template=tpl,
        owner=owner,
        content=content or {},
        is_published=True,
    )
    tpl.tenant = tenant
    tpl.save(update_fields=["tenant"])
    TemplateVersion.objects.create(
        template=tpl,
        number=1,
        html_source=tpl.html_source,
        schema=tpl.schema or {},
        label="seed",
    )
    return tenant


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
)
class GetPageHtmlTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.tenant_a = _make_tenant(
            self.owner, name="Alpha", subdomain="alpha", html=HTML_RAW_V1
        )
        self.tenant_b = _make_tenant(
            self.owner, name="Beta", subdomain="beta", html="<html>beta</html>"
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        _make_token(self.member, token="tok-member")

    def _post(self, body: dict, *, token: str = "tok-member"):
        return self.client.post(
            "/mcp",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
            HTTP_ACCEPT="application/json",
        )

    def _call(self, name: str, arguments=None, *, token="tok-member", id=1):
        body = {
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        return self._post(body, token=token)

    def _result(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"]

    def _push(self, arguments, *, token="tok-member"):
        return self._result(self._call("push_page", arguments, token=token))

    def _get_html(self, arguments, *, token="tok-member"):
        return self._result(self._call("get_page_html", arguments, token=token))

    def test_listed_as_readonly_in_tools_list(self):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        result = self._result(self._post(body, token="tok-member"))
        tools = {t["name"]: t for t in result["tools"]}
        self.assertIn("get_page_html", tools)
        ann = tools["get_page_html"]["annotations"]
        self.assertTrue(ann["readOnlyHint"])

    def test_pull_modify_repush_with_returned_etag_accepted(self):
        """AC1: blind session can pull → modify → push with if_match accepted."""
        self._push(
            {
                "site": "alpha",
                "page": "about",
                "title": "About",
                "html": HTML_RAW_V1,
            }
        )
        pulled = self._get_html({"site": "alpha", "page": "about"})
        self.assertFalse(pulled.get("isError", False), pulled)
        sc = pulled["structuredContent"]
        self.assertEqual(sc["html_source"], HTML_RAW_V1)
        etag = sc["etag"]

        modified = HTML_RAW_V1.replace("Plain copy", "Session revise")
        pushed = self._push(
            {
                "site": "alpha",
                "page": "about",
                "html": modified,
                "if_match": etag,
            }
        )
        self.assertFalse(pushed.get("isError", False), pushed)
        self.assertEqual(
            pushed["structuredContent"]["etag"], _html_etag(modified)
        )

    def test_raw_round_trip_pull_push_pull(self):
        """AC2: raw (un-annotated) page round-trips."""
        self._push(
            {
                "site": "alpha",
                "page": "about",
                "title": "About",
                "html": HTML_RAW_V1,
            }
        )
        first = self._get_html({"site": "alpha", "page": "about"})
        self.assertEqual(first["structuredContent"]["html_source"], HTML_RAW_V1)

        self._push(
            {
                "site": "alpha",
                "page": "about",
                "html": HTML_RAW_V2,
                "if_match": first["structuredContent"]["etag"],
            }
        )
        second = self._get_html({"site": "alpha", "page": "about"})
        self.assertFalse(second.get("isError", False), second)
        self.assertEqual(second["structuredContent"]["html_source"], HTML_RAW_V2)

    def test_etag_equals_what_push_page_compares(self):
        """AC3: assert equality with html_etag / push_page's guard, not eyeball."""
        from api.mcp.content import html_etag

        self._push(
            {
                "site": "alpha",
                "page": "about",
                "title": "About",
                "html": HTML_RAW_V1,
            }
        )
        page = Page.objects.get(tenant=self.tenant_a, slug="about")
        expected = html_etag(page.template.html_source)
        sc = self._get_html({"site": "alpha", "page": "about"})[
            "structuredContent"
        ]
        self.assertEqual(sc["etag"], expected)
        # Wrong (content) hash must not equal the html etag.
        content_hash = hashlib.sha256(b"{}").hexdigest()
        self.assertNotEqual(sc["etag"], content_hash)

    def test_cross_tenant_and_missing_indistinguishable(self):
        """AC4: forbidden and nonexistent stay the same shape/message."""
        # Site-level: member lacks beta; after delete, same denial.
        forbidden = self._call(
            "get_page_html", {"site": "beta"}, token="tok-member"
        )
        self.tenant_b.delete()
        missing_site = self._call(
            "get_page_html", {"site": "beta"}, token="tok-member", id=2
        )
        self.assertEqual(forbidden.status_code, missing_site.status_code)
        self.assertEqual(
            forbidden.json()["result"],
            missing_site.json()["result"],
        )
        self.assertTrue(forbidden.json()["result"]["isError"])

        # Page-level on an accessible site.
        missing_page = self._get_html({"site": "alpha", "page": "nope"})
        self.assertTrue(missing_page.get("isError"))
        self.assertIn("No accessible page", missing_page["content"][0]["text"])

    def test_historical_version_pull_while_etag_is_current(self):
        """Optional version: v1/v2 can be diffed; etag stays push_page's current."""
        from api.mcp.content import html_etag

        self._push(
            {
                "site": "alpha",
                "page": "about",
                "title": "About",
                "html": HTML_RAW_V1,
            }
        )
        page = Page.objects.get(tenant=self.tenant_a, slug="about")
        v1 = page.template.versions.get(number=1)
        self.assertEqual(v1.html_source, HTML_RAW_V1)

        mid = self._get_html({"site": "alpha", "page": "about"})
        self._push(
            {
                "site": "alpha",
                "page": "about",
                "html": HTML_RAW_V2,
                "if_match": mid["structuredContent"]["etag"],
            }
        )
        page.template.refresh_from_db()
        current_etag = html_etag(page.template.html_source)

        v1_pull = self._get_html(
            {"site": "alpha", "page": "about", "version": 1}
        )["structuredContent"]
        v2_pull = self._get_html(
            {"site": "alpha", "page": "about", "version": 2}
        )["structuredContent"]
        latest = self._get_html({"site": "alpha", "page": "about"})[
            "structuredContent"
        ]

        self.assertEqual(v1_pull["html_source"], HTML_RAW_V1)
        self.assertEqual(v1_pull["version"], 1)
        self.assertEqual(v2_pull["html_source"], HTML_RAW_V2)
        self.assertEqual(v2_pull["version"], 2)
        self.assertEqual(latest["html_source"], HTML_RAW_V2)
        # etag is always the live template hash so if_match still works.
        self.assertEqual(v1_pull["etag"], current_etag)
        self.assertEqual(v2_pull["etag"], current_etag)
        self.assertEqual(latest["etag"], current_etag)

        # Restore-style push of modified v1 using current etag succeeds.
        restored = HTML_RAW_V3
        pushed = self._push(
            {
                "site": "alpha",
                "page": "about",
                "html": restored,
                "if_match": v1_pull["etag"],
            }
        )
        self.assertFalse(pushed.get("isError", False), pushed)

    def test_home_page_pull(self):
        sc = self._get_html({"site": "alpha"})["structuredContent"]
        self.assertEqual(sc["html_source"], HTML_RAW_V1)
        self.assertEqual(sc["version"], 1)
        self.assertEqual(sc["etag"], _html_etag(HTML_RAW_V1))
        self.assertIsNone(sc.get("page"))

    def test_content_tools_rename_etag_to_content_etag(self):
        """Contract change: field-value tools no longer share the name 'etag'."""
        tpl = self.tenant_a.template
        tpl.html_source = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Hi</h1>
</section>
"""
        tpl.editing_mode = Template.EDITING_EDITABLE
        tpl.save()

        get_page = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )
        self.assertIn("content_etag", get_page["structuredContent"])
        self.assertNotIn("etag", get_page["structuredContent"])

        get_content = self._result(
            self._call(
                "get_content",
                {"site": "alpha", "field": "hero.title"},
                token="tok-member",
            )
        )
        self.assertIn("content_etag", get_content["structuredContent"])
        self.assertNotIn("etag", get_content["structuredContent"])

    def test_audit_one_row_per_call(self):
        self.assertEqual(McpAuditLog.objects.count(), 0)
        self._get_html({"site": "alpha"})
        self.assertEqual(McpAuditLog.objects.count(), 1)
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "get_page_html")
        self.assertEqual(row.tenant_id, self.tenant_a.pk)
        self.assertEqual(row.performed_via, "MCP")
