"""CMS-7 acceptance — patch_content write path."""

from __future__ import annotations

import copy
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from core.models import ContentVersion, Page, Template, Tenant, TenantMembership
from core.renderer import merge_with_defaults
from core.services import content_versions as cv
from api.mcp.content import content_etag


User = get_user_model()

CLAUDE_CLIENT_ID = "claude-test"
PROTOCOL = "2025-06-18"

SAMPLE_HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
  <p data-edit="hero.sub" data-type="text" data-label="Sub">Hello there</p>
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


def _make_tenant(owner, *, name, subdomain, content=None):
    tpl = Template.objects.create(
        name=f"tpl-{subdomain}",
        html_source=SAMPLE_HTML,
        editing_mode=Template.EDITING_EDITABLE,
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
    return tenant


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
)
class PatchContentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.tenant_a = _make_tenant(
            self.owner, name="Alpha", subdomain="alpha", content={"hero": {"title": "A"}}
        )
        self.tenant_b = _make_tenant(
            self.owner, name="Beta", subdomain="beta", content={"hero": {"title": "B"}}
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        self.page_a = Page.objects.create(
            tenant=self.tenant_a,
            template=self.tenant_a.template,
            title="About",
            slug="about",
            content={"hero": {"title": "About A"}},
            is_published=True,
        )
        _make_token(self.admin, token="tok-admin")
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

    def _call(self, tool: str, arguments=None, *, token="tok-member", id=1):
        body = {
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
        return self._post(body, token=token)

    def _result(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"]

    def test_patch_writes_returns_etag_and_site_url(self):
        etag = content_etag(self.tenant_a.content)
        result = self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "field": "hero.title",
                    "value": "Patched",
                    "if_match": etag,
                },
            )
        )
        self.assertFalse(result.get("isError", False))
        sc = result["structuredContent"]
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content["hero"]["title"], "Patched")
        self.assertEqual(sc["etag"], content_etag(self.tenant_a.content))
        self.assertEqual(sc["url"], "https://alpha.sites.katek.app/")
        self.assertNotEqual(sc["etag"], etag)

    def test_stale_if_match_returns_409_and_writes_nothing(self):
        before = copy.deepcopy(self.tenant_a.content)
        response = self._call(
            "patch_content",
            {
                "site": "alpha",
                "field": "hero.title",
                "value": "Nope",
                "if_match": "deadbeef" * 8,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content, before)

    def test_patch_creates_content_version_via_shared_service(self):
        etag = content_etag(self.tenant_a.content)
        self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "field": "hero.title",
                    "value": "V1",
                    "if_match": etag,
                },
            )
        )
        versions = list(ContentVersion.objects.filter(tenant=self.tenant_a))
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].source, cv.SOURCE_MCP)
        self.assertEqual(versions[0].snapshot, {"hero": {"title": "A"}})

    def test_mcp_burst_does_not_flush_human_undo(self):
        # Seed human history through the shared service (dashboard path).
        for i in range(10):
            cv.save_tenant_content(
                self.tenant_a,
                {"hero": {"title": f"H{i}"}},
                user=self.member,
                source=cv.SOURCE_DASHBOARD,
            )
        human_ids = set(
            ContentVersion.objects.filter(
                tenant=self.tenant_a, source=cv.SOURCE_DASHBOARD
            ).values_list("id", flat=True)
        )
        self.assertEqual(len(human_ids), 10)

        # Ten rapid MCP field patches in one "conversation".
        for i in range(10):
            self.tenant_a.refresh_from_db()
            etag = content_etag(self.tenant_a.content)
            self._result(
                self._call(
                    "patch_content",
                    {
                        "site": "alpha",
                        "field": "hero.title",
                        "value": f"AI{i}",
                        "if_match": etag,
                    },
                )
            )

        remaining = set(
            ContentVersion.objects.filter(
                tenant=self.tenant_a, source=cv.SOURCE_DASHBOARD
            ).values_list("id", flat=True)
        )
        self.assertEqual(remaining, human_ids)
        self.assertEqual(
            ContentVersion.objects.filter(
                tenant=self.tenant_a, source=cv.SOURCE_MCP
            ).count(),
            1,
        )

    def test_cross_tenant_write_indistinguishable_from_missing(self):
        etag = content_etag(self.tenant_b.content)
        forbidden = self._call(
            "patch_content",
            {
                "site": "beta",
                "field": "hero.title",
                "value": "X",
                "if_match": etag,
            },
            token="tok-member",
        )
        self.tenant_b.delete()
        missing = self._call(
            "patch_content",
            {
                "site": "beta",
                "field": "hero.title",
                "value": "X",
                "if_match": etag,
            },
            token="tok-member",
        )
        self.assertEqual(forbidden.status_code, missing.status_code)
        self.assertEqual(forbidden.content, missing.content)
        body = forbidden.json()["result"]
        self.assertTrue(body["isError"])

    def test_nested_content_never_flat_dotted_key(self):
        etag = content_etag(self.tenant_a.content)
        self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "field": "hero.title",
                    "value": "Nested",
                    "if_match": etag,
                },
            )
        )
        self.tenant_a.refresh_from_db()
        content = self.tenant_a.content
        self.assertNotIn("hero.title", content)
        self.assertIsInstance(content.get("hero"), dict)
        self.assertEqual(content["hero"]["title"], "Nested")
        # merge_with_defaults must not raise (the 2026-08-06 outage mode).
        merged = merge_with_defaults(self.tenant_a.template.schema, content)
        self.assertEqual(merged["hero"]["title"], "Nested")

    def test_inner_page_patch_refused(self):
        etag = content_etag(self.page_a.content)
        result = self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "page": "about",
                    "field": "hero.title",
                    "value": "Nope",
                    "if_match": etag,
                },
            )
        )
        self.assertTrue(result["isError"])
        self.page_a.refresh_from_db()
        self.assertEqual(self.page_a.content["hero"]["title"], "About A")
        self.assertEqual(ContentVersion.objects.filter(tenant=self.tenant_a).count(), 0)

    def test_tools_list_includes_patch_content_writable(self):
        response = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        tools = response.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("patch_content", names)
        self.assertIn("create_client_account", names)
        patch = next(t for t in tools if t["name"] == "patch_content")
        self.assertFalse(patch["annotations"]["readOnlyHint"])
        self.assertFalse(patch["annotations"]["idempotentHint"])

    def test_audit_row_for_patch(self):
        from api.models import McpAuditLog

        etag = content_etag(self.tenant_a.content)
        self._result(
            self._call(
                "patch_content",
                {
                    "site": "alpha",
                    "field": "hero.title",
                    "value": "Audited",
                    "if_match": etag,
                },
            )
        )
        row = McpAuditLog.objects.get()
        self.assertEqual(row.action, "patch_content")
        self.assertEqual(row.tenant_id, self.tenant_a.pk)
        self.assertEqual(row.actor_id, self.member.pk)
