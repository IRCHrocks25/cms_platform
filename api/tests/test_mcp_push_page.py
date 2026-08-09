"""CMS-10 acceptance — push_page whole-HTML create / re-push."""

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

HTML_FULL = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
  <p data-edit="hero.sub" data-type="text" data-label="Sub">Hello</p>
</section>
"""

HTML_SHRUNK = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
</section>
"""

HTML_RAW = "<html><body><h1>About us</h1><p>Plain copy</p></body></html>"


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


def _make_tenant(owner, *, name, subdomain, html=HTML_FULL, content=None, published=True):
    tpl = Template.objects.create(
        name=f"tpl-{subdomain}",
        html_source=html,
        editing_mode=Template.EDITING_EDITABLE,
    )
    tenant = Tenant.objects.create(
        name=name,
        subdomain=subdomain,
        template=tpl,
        owner=owner,
        content=content or {},
        is_published=published,
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
class PushPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.outsider = User.objects.create_user("out", "out@ex.com", "x")
        self.tenant_a = _make_tenant(
            self.owner,
            name="Alpha",
            subdomain="alpha",
            content={"hero": {"title": "Live", "sub": "Keep"}},
            published=True,
        )
        self.tenant_b = _make_tenant(
            self.owner, name="Beta", subdomain="beta", content={}
        )
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.member,
            role=TenantMembership.ROLE_EDITOR,
        )
        _make_token(self.admin, token="tok-admin")
        _make_token(self.member, token="tok-member")
        _make_token(self.outsider, token="tok-out")

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

    def _call(self, arguments=None, *, token="tok-member", id=1):
        body = {
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {"name": "push_page", "arguments": arguments or {}},
        }
        return self._post(body, token=token)

    def _result(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"]

    def test_listed_in_tools_list(self):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        result = self._result(self._post(body, token="tok-member"))
        names = [t["name"] for t in result["tools"]]
        self.assertIn("push_page", names)

    def test_first_push_creates_tenant_owned_raw_template_and_page(self):
        result = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "title": "About",
                    "html": HTML_RAW,
                }
            )
        )
        self.assertFalse(result.get("isError", False), result)
        sc = result["structuredContent"]
        page = Page.objects.get(tenant=self.tenant_a, slug="about")
        tpl = page.template
        self.assertEqual(tpl.tenant_id, self.tenant_a.pk)
        self.assertEqual(tpl.editing_mode, Template.EDITING_RAW)
        self.assertEqual(tpl.html_source, HTML_RAW)
        self.assertEqual(sc["url"], "https://alpha.sites.katek.app/about/")
        self.assertEqual(sc["page"], "about")
        self.assertEqual(sc["editing_mode"], "raw")
        self.assertEqual(sc["etag"], _html_etag(HTML_RAW))

    def test_repush_appends_template_version(self):
        self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "title": "About",
                    "html": HTML_RAW,
                }
            )
        )
        page = Page.objects.get(tenant=self.tenant_a, slug="about")
        tpl = page.template
        v1_count = tpl.versions.count()
        old_html = tpl.html_source
        etag = _html_etag(old_html)

        revised = HTML_RAW.replace("About us", "About us (v2)")
        result = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "html": revised,
                    "if_match": etag,
                }
            )
        )
        self.assertFalse(result.get("isError", False), result)
        tpl.refresh_from_db()
        self.assertEqual(tpl.versions.count(), v1_count + 1)
        self.assertEqual(tpl.html_source, revised)
        # Previous HTML still retrievable from an older version row.
        archived = TemplateVersion.objects.filter(
            template=tpl, html_source=old_html
        ).exists()
        self.assertTrue(archived)
        self.assertEqual(result["structuredContent"]["etag"], _html_etag(revised))

    def test_field_loss_refused_without_flag_then_allowed(self):
        # Seed an editable published page using HTML_FULL fields.
        tpl = Template.objects.create(
            name="about-tpl",
            html_source=HTML_FULL,
            tenant=self.tenant_a,
            editing_mode=Template.EDITING_EDITABLE,
        )
        TemplateVersion.objects.create(
            template=tpl,
            number=1,
            html_source=tpl.html_source,
            schema=tpl.schema or {},
        )
        Page.objects.create(
            tenant=self.tenant_a,
            template=tpl,
            title="About",
            slug="about",
            content={"hero": {"title": "Live", "sub": "Used"}},
            is_published=True,
        )
        etag = _html_etag(HTML_FULL)

        denied = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "html": HTML_SHRUNK,
                    "if_match": etag,
                }
            )
        )
        self.assertTrue(denied.get("isError"))
        self.assertIn("allow_field_loss", denied["content"][0]["text"])
        tpl.refresh_from_db()
        self.assertEqual(tpl.html_source, HTML_FULL)

        ok = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "html": HTML_SHRUNK,
                    "if_match": etag,
                    "allow_field_loss": True,
                }
            )
        )
        self.assertFalse(ok.get("isError"), ok)
        tpl.refresh_from_db()
        self.assertEqual(tpl.html_source, HTML_SHRUNK)

    def test_cross_tenant_template_indistinguishable_from_missing(self):
        foreign = self.tenant_b.template
        denied_foreign = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "html": HTML_RAW,
                    "template_id": foreign.pk,
                }
            )
        )
        denied_missing = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "html": HTML_RAW,
                    "template_id": 9_999_999,
                },
                id=2,
            )
        )
        self.assertTrue(denied_foreign.get("isError"))
        self.assertTrue(denied_missing.get("isError"))
        self.assertEqual(
            denied_foreign["content"][0]["text"],
            denied_missing["content"][0]["text"],
        )
        self.assertFalse(
            Page.objects.filter(tenant=self.tenant_a, slug="about").exists()
        )

    def test_reserved_slug_privacy_and_terms_refused(self):
        for slug in ("privacy", "terms"):
            result = self._result(
                self._call(
                    {
                        "site": "alpha",
                        "page": slug,
                        "title": slug.title(),
                        "html": HTML_RAW,
                    },
                    id=hash(slug) % 1000,
                )
            )
            self.assertTrue(result.get("isError"), slug)
            text = result["content"][0]["text"].lower()
            self.assertIn("reserved", text)
            self.assertIn(slug, text)
            self.assertFalse(
                Page.objects.filter(tenant=self.tenant_a, slug=slug).exists()
            )

    def test_returns_url_and_writes_audit_row(self):
        before = McpAuditLog.objects.count()
        result = self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "services",
                    "title": "Services",
                    "html": HTML_RAW,
                }
            )
        )
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(
            result["structuredContent"]["url"],
            "https://alpha.sites.katek.app/services/",
        )
        self.assertEqual(McpAuditLog.objects.count(), before + 1)
        row = McpAuditLog.objects.latest("id")
        self.assertEqual(row.action, "push_page")
        self.assertEqual(row.tenant_id, self.tenant_a.pk)
        self.assertEqual(row.actor_id, self.member.pk)

    def test_forbidden_site_matches_nonexistent(self):
        # Same subdomain string: member lacks scope on beta, then beta is gone.
        forbidden = self._call(
            {
                "site": "beta",
                "page": "x",
                "html": HTML_RAW,
            },
        )
        self.tenant_b.delete()
        missing = self._call(
            {
                "site": "beta",
                "page": "x",
                "html": HTML_RAW,
            },
            id=2,
        )
        self.assertEqual(forbidden.status_code, missing.status_code)
        self.assertEqual(
            forbidden.json()["result"],
            missing.json()["result"],
        )
        self.assertTrue(forbidden.json()["result"]["isError"])

    def test_stale_if_match_returns_409(self):
        self._result(
            self._call(
                {
                    "site": "alpha",
                    "page": "about",
                    "title": "About",
                    "html": HTML_RAW,
                }
            )
        )
        response = self._call(
            {
                "site": "alpha",
                "page": "about",
                "html": HTML_RAW.replace("us", "them"),
                "if_match": "deadbeef" * 8,
            },
            id=2,
        )
        self.assertEqual(response.status_code, 409)
        page = Page.objects.get(tenant=self.tenant_a, slug="about")
        self.assertEqual(page.template.html_source, HTML_RAW)

    def test_home_push_versions_tenant_template_keeps_editing_mode(self):
        # Re-push home must version HTML but must NOT flip editing_mode as a
        # side effect (annotation hand-over is explicit, not automatic).
        home = self.tenant_a.template
        home.editing_mode = Template.EDITING_EDITABLE
        home.save(update_fields=["editing_mode"])
        # Clear published content so field-loss does not block this mode check.
        self.tenant_a.content = {}
        self.tenant_a.is_published = False
        self.tenant_a.save(update_fields=["content", "is_published"])
        etag = _html_etag(home.html_source)
        result = self._result(
            self._call(
                {
                    "site": "alpha",
                    "html": HTML_RAW,
                    "if_match": etag,
                }
            )
        )
        self.assertFalse(result.get("isError"), result)
        home.refresh_from_db()
        self.assertEqual(home.html_source, HTML_RAW)
        self.assertEqual(home.editing_mode, Template.EDITING_EDITABLE)
        self.assertGreaterEqual(home.versions.count(), 2)
        self.assertEqual(result["structuredContent"]["url"], "https://alpha.sites.katek.app/")
        self.assertIsNone(result["structuredContent"]["page"])
