"""CMS-9 acceptance tests — Section 13 of the MCP endpoint design spec."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from core.models import Page, RESERVED_PAGE_SLUGS, Template, Tenant, TenantMembership


User = get_user_model()

CLAUDE_CLIENT_ID = "claude-test"
PROTOCOL = "2025-06-18"

SAMPLE_HTML = """
<section data-section="hero" data-label="Hero" data-group="Home">
  <h1 data-edit="hero.title" data-type="text" data-label="Headline">Welcome</h1>
  <p data-edit="hero.sub" data-type="text" data-label="Sub">Hello there</p>
</section>
"""


def _sha_stored(stored: dict) -> str:
    payload = json.dumps(
        stored, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_schema(instance, schema: dict) -> None:
    """Minimal JSON Schema subset validator for advertised outputSchema."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            raise AssertionError(f"expected object, got {type(instance)}")
        required = schema.get("required") or []
        for key in required:
            if key not in instance:
                raise AssertionError(f"missing required key {key!r}")
        props = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in props:
                _validate_schema(value, props[key])
            elif additional is False:
                raise AssertionError(f"unexpected key {key!r}")
            elif isinstance(additional, dict):
                _validate_schema(value, additional)
    elif t == "array":
        if not isinstance(instance, list):
            raise AssertionError(f"expected array, got {type(instance)}")
        item_schema = schema.get("items")
        if item_schema:
            for item in instance:
                _validate_schema(item, item_schema)
    elif t == "string":
        if not isinstance(instance, str):
            raise AssertionError(f"expected string, got {type(instance)}")
    elif t == "boolean":
        if not isinstance(instance, bool):
            raise AssertionError(f"expected bool, got {type(instance)}")
    elif t == "null":
        if instance is not None:
            raise AssertionError(f"expected null, got {instance!r}")
    elif isinstance(t, list):
        # union e.g. ["string", "null"]
        ok = False
        for opt in t:
            try:
                _validate_schema(instance, {**schema, "type": opt})
                ok = True
                break
            except AssertionError:
                continue
        if not ok:
            raise AssertionError(f"value {instance!r} matched none of {t}")


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


def _make_token(user, *, token="tok-test", application=None, expires_delta=timedelta(hours=1)):
    if application is None:
        application = _make_app()
    return AccessToken.objects.create(
        user=user,
        application=application,
        token=token,
        expires=timezone.now() + expires_delta,
        scope="read write",
    )


def _make_tenant(owner, *, name, subdomain, content=None, published=False):
    tpl = Template.objects.create(name=f"tpl-{subdomain}", html_source=SAMPLE_HTML)
    return Tenant.objects.create(
        name=name,
        subdomain=subdomain,
        template=tpl,
        owner=owner,
        content=content or {},
        is_published=published,
    )


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="localhost",
    ALLOWED_HOSTS=["*"],
)
class McpEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.admin = User.objects.create_superuser("admin", "a@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        self.staff = User.objects.create_user("ops", "ops@ex.com", "x", is_staff=True)
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
            is_published=False,
        )
        self.page_b = Page.objects.create(
            tenant=self.tenant_b,
            template=self.tenant_b.template,
            title="About",
            slug="about",
            content={"hero": {"title": "About B"}},
            is_published=True,
        )
        _make_token(self.admin, token="tok-admin")
        _make_token(self.member, token="tok-member")
        _make_token(self.staff, token="tok-staff")

    def _post(
        self,
        body: dict,
        *,
        token: str | None = "tok-member",
        host: str = "localhost",
        headers: dict | None = None,
        protocol: str | None = PROTOCOL,
        accept: str = "application/json",
        origin: str | None = None,
    ):
        h = {
            "HTTP_HOST": host,
            "HTTP_ACCEPT": accept,
            "content_type": "application/json",
        }
        if token is not None:
            h["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if protocol is not None:
            h["HTTP_MCP_PROTOCOL_VERSION"] = protocol
        if origin is not None:
            h["HTTP_ORIGIN"] = origin
        if headers:
            h.update(headers)
        return self.client.post("/mcp", data=json.dumps(body), **h)

    def _rpc(self, method: str, params=None, *, id=1, **kwargs):
        body = {"jsonrpc": "2.0", "method": method}
        if id is not None:
            body["id"] = id
        if params is not None:
            body["params"] = params
        return self._post(body, **kwargs)

    def _call(self, tool: str, arguments=None, **kwargs):
        return self._rpc(
            "tools/call",
            {"name": tool, "arguments": arguments or {}},
            **kwargs,
        )

    def _result(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"]

    # --- Transport ---

    def test_ac01_initialize(self):
        r = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
            protocol=None,
        )
        result = self._result(r)
        self.assertEqual(result["protocolVersion"], PROTOCOL)
        self.assertEqual(result["serverInfo"]["name"], "katek-sites")
        self.assertIn("tools", result["capabilities"])

    def test_ac02_notifications_initialized_202(self):
        r = self._rpc("notifications/initialized", id=None)
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.content, b"")

    def test_ac03_missing_protocol_version_after_init(self):
        r = self._rpc("tools/list", protocol=None)
        self.assertEqual(r.status_code, 400)

    def test_ac04_unsupported_protocol_version(self):
        r = self._rpc("tools/list", protocol="1999-01-01")
        self.assertEqual(r.status_code, 400)

    def test_ac05_get_mcp_sse_stream(self):
        r = self.client.get(
            "/mcp",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION="Bearer tok-member",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/event-stream", r["Content-Type"])

    def test_ac06_sse_accept_returns_event_message(self):
        r = self._rpc(
            "tools/list",
            accept="text/event-stream",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/event-stream", r["Content-Type"])
        self.assertIn(b"event: message\n", r.content)
        self.assertIn(b"data: ", r.content)

    def test_ac07_origin_allowlist(self):
        bad = self._rpc("tools/list", origin="https://evil.example")
        self.assertEqual(bad.status_code, 403)
        ok = self._rpc("tools/list", origin=None)
        self.assertEqual(ok.status_code, 200)
        allowed = self._rpc("tools/list", origin="https://claude.ai")
        self.assertEqual(allowed.status_code, 200)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=20 * 1024 * 1024)
    def test_ac08_body_too_large(self):
        huge = b'{"jsonrpc":"2.0","id":1,"method":"tools/list","pad":"' + (
            b"x" * (16 * 1024 * 1024 + 100)
        ) + b'"}'
        r = self.client.post(
            "/mcp",
            data=huge,
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION="Bearer tok-member",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(r.status_code, 413)
        payload = json.loads(r.content)
        self.assertEqual(payload["error"]["code"], -32600)

    def test_ac09_put_method_not_allowed(self):
        r = self.client.put(
            "/mcp",
            data=b"{}",
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION="Bearer tok-member",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
        )
        self.assertEqual(r.status_code, 405)
        self.assertEqual(r["Allow"], "GET, POST")

    def test_ac10_tenant_host_404(self):
        tenant_r = self._rpc("tools/list", host="alpha.localhost")
        self.assertEqual(tenant_r.status_code, 404)
        agency_r = self._rpc("tools/list", host="localhost")
        self.assertEqual(agency_r.status_code, 200)

    def test_ac11_no_append_slash_redirect(self):
        r = self._rpc("tools/list")
        self.assertNotIn(r.status_code, (301, 302))
        self.assertEqual(r.status_code, 200)

    # --- Auth ---

    def test_ac12_unauthenticated_www_authenticate(self):
        r = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token=None,
        )
        self.assertEqual(r.status_code, 401)
        www = r["WWW-Authenticate"]
        self.assertIn('realm="katek-sites"', www)
        self.assertIn("resource_metadata=", www)

    def test_ac13_resource_metadata_on_request_host(self):
        r = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            token=None,
            host="sites.example.test",
        )
        self.assertEqual(r.status_code, 401)
        www = r["WWW-Authenticate"]
        self.assertIn(
            'resource_metadata="http://sites.example.test/.well-known/oauth-protected-resource/mcp"',
            www,
        )

    def test_ac14_wrong_oauth_client_401(self):
        other = Application.objects.create(
            name="Other",
            client_id="other-client",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="https://other.test/callback",
        )
        _make_token(self.member, token="tok-other", application=other)
        r = self._rpc("tools/list", token="tok-other")
        self.assertEqual(r.status_code, 401)

    @override_settings(CLAUDE_OAUTH_CLIENT_ID="")
    def test_ac15_unset_client_id_fails_closed(self):
        r = self._rpc("tools/list", token="tok-member")
        self.assertEqual(r.status_code, 401)

    def test_ac16_expired_revoked_inactive_401(self):
        _make_token(
            self.member, token="tok-exp", expires_delta=timedelta(hours=-1)
        )
        self.assertEqual(self._rpc("tools/list", token="tok-exp").status_code, 401)

        tok = _make_token(self.member, token="tok-rev")
        tok.revoke()
        self.assertEqual(self._rpc("tools/list", token="tok-rev").status_code, 401)

        inactive = User.objects.create_user("dead", "d@ex.com", "x", is_active=False)
        TenantMembership.objects.create(
            tenant=self.tenant_a, user=inactive, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(inactive, token="tok-inactive")
        self.assertEqual(
            self._rpc("tools/list", token="tok-inactive").status_code, 401
        )

    # --- Enumeration oracle ---

    def test_ac17_site_enumeration_oracle(self):
        # Same site name: exists-but-forbidden, then deleted → must be byte-identical.
        forbidden = self._call("list_pages", {"site": "beta"}, token="tok-member")
        self.tenant_b.delete()
        missing = self._call("list_pages", {"site": "beta"}, token="tok-member")
        self.assertEqual(forbidden.status_code, missing.status_code)
        self.assertEqual(forbidden.content, missing.content)

    def test_ac18_page_enumeration_oracle(self):
        # On a forbidden site, existing page vs nonexistent must be identical
        # (site-level denial — must not leak page existence).
        existing = self._call(
            "get_page", {"site": "beta", "page": "about"}, token="tok-member"
        )
        self.page_b.delete()
        missing = self._call(
            "get_page", {"site": "beta", "page": "about"}, token="tok-member"
        )
        self.assertEqual(existing.status_code, missing.status_code)
        self.assertEqual(existing.content, missing.content)

    # --- Authorization ---

    def test_ac19_staff_without_membership(self):
        # CMS-17: staff with no memberships do not resolve → 401 (denied every site).
        r = self._call("list_sites", token="tok-staff")
        self.assertEqual(r.status_code, 401)
        # Handler contract for an empty-scope principal:
        from api.auth import ResolvedAuth
        from api.mcp.tools import list_sites

        empty = ResolvedAuth(
            user=self.staff, platform_role=None, tenant_scopes=()
        )
        result = list_sites(empty)
        self.assertEqual(result["sites"], [])

    def test_ac20_member_of_a_denied_b(self):
        ok = self._call("list_pages", {"site": "alpha"}, token="tok-member")
        result = self._result(ok)
        self.assertTrue(result["structuredContent"]["pages"])
        denied = self._call("list_pages", {"site": "beta"}, token="tok-member")
        body = self._result(denied)
        self.assertTrue(body["isError"])

    def test_ac21_superuser_reads_any(self):
        r = self._call("list_pages", {"site": "beta"}, token="tok-admin")
        result = self._result(r)
        self.assertFalse(result.get("isError", False))
        sites = self._result(self._call("list_sites", token="tok-admin"))
        subs = {s["subdomain"] for s in sites["structuredContent"]["sites"]}
        self.assertIn("alpha", subs)
        self.assertIn("beta", subs)

    # --- Tools ---

    def test_ac22_tools_list_four_readonly(self):
        result = self._result(self._rpc("tools/list"))
        tools = result["tools"]
        self.assertEqual(len(tools), 4)
        names = {t["name"] for t in tools}
        self.assertEqual(
            names, {"list_sites", "list_pages", "get_page", "get_content"}
        )
        for t in tools:
            self.assertIn("inputSchema", t)
            self.assertIn("outputSchema", t)
            self.assertTrue(t["annotations"]["readOnlyHint"])

    def test_ac23_content_and_structured_content(self):
        result = self._result(self._call("list_sites", token="tok-member"))
        self.assertIn("content", result)
        self.assertIn("structuredContent", result)
        tools = self._result(self._rpc("tools/list"))["tools"]
        schema = next(t["outputSchema"] for t in tools if t["name"] == "list_sites")
        _validate_schema(result["structuredContent"], schema)

    def test_ac24_get_page_home_vs_slug(self):
        home = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )
        self.assertEqual(
            home["structuredContent"]["fields"]["hero.title"]["value"], "A"
        )
        about = self._result(
            self._call(
                "get_page", {"site": "alpha", "page": "about"}, token="tok-member"
            )
        )
        self.assertEqual(
            about["structuredContent"]["fields"]["hero.title"]["value"], "About A"
        )

    def test_ac25_is_default_flag(self):
        never = self._result(
            self._call(
                "get_content",
                {"site": "alpha", "field": "hero.sub"},
                token="tok-member",
            )
        )
        self.assertTrue(never["structuredContent"]["is_default"])
        edited = self._result(
            self._call(
                "get_content",
                {"site": "alpha", "field": "hero.title"},
                token="tok-member",
            )
        )
        self.assertFalse(edited["structuredContent"]["is_default"])

    def test_ac26_reads_return_merged_defaults(self):
        result = self._result(
            self._call(
                "get_content",
                {"site": "alpha", "field": "hero.sub"},
                token="tok-member",
            )
        )
        self.assertEqual(result["structuredContent"]["value"], "Hello there")

    def test_ac27_etag_stable_then_changes(self):
        a = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )
        b = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )
        self.assertEqual(
            a["structuredContent"]["etag"], b["structuredContent"]["etag"]
        )
        self.tenant_a.content = {"hero": {"title": "Changed"}}
        self.tenant_a.save(update_fields=["content"])
        c = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )
        self.assertNotEqual(
            a["structuredContent"]["etag"], c["structuredContent"]["etag"]
        )

    def test_ac28_template_default_change_does_not_change_etag(self):
        before = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )["structuredContent"]["etag"]
        self.assertEqual(before, _sha_stored(self.tenant_a.content))
        tpl = self.tenant_a.template
        tpl.html_source = SAMPLE_HTML.replace("Welcome", "Changed default")
        tpl.save()
        after = self._result(
            self._call("get_page", {"site": "alpha"}, token="tok-member")
        )["structuredContent"]["etag"]
        self.assertEqual(before, after)

    def test_ac29_unknown_tool_and_bad_args(self):
        unknown = self._call("nope_tool", {}, token="tok-member")
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.json()["error"]["code"], -32601)
        bad = self._call("list_pages", {}, token="tok-member")  # site required
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(bad.json()["error"]["code"], -32602)

    def test_ac30_draft_page_readable(self):
        self.assertFalse(self.page_a.is_published)
        result = self._result(
            self._call(
                "get_page", {"site": "alpha", "page": "about"}, token="tok-member"
            )
        )
        self.assertFalse(result.get("isError", False))
        self.assertEqual(
            result["structuredContent"]["fields"]["hero.title"]["value"], "About A"
        )

    def test_ac31_no_resolvedauth_scopes_left(self):
        import pathlib
        import re

        api_root = pathlib.Path(__file__).resolve().parents[1]
        # Skip this test file — it mentions the old name in assertions.
        skip = {pathlib.Path(__file__).resolve()}
        pattern = re.compile(
            r"ResolvedAuth\.scopes\b|principal\.scopes\b|ResolvedAuth\([^)]*\bscopes="
        )
        offenders = []
        for path in api_root.rglob("*.py"):
            if path.resolve() in skip:
                continue
            if pattern.search(path.read_text()):
                offenders.append(str(path))
        self.assertEqual(offenders, [])
        from api.auth import ResolvedAuth

        self.assertTrue(hasattr(ResolvedAuth, "__dataclass_fields__"))
        self.assertIn("tenant_scopes", ResolvedAuth.__dataclass_fields__)
        self.assertNotIn("scopes", ResolvedAuth.__dataclass_fields__)

    def test_ac32_mcp_reserved_page_slug(self):
        self.assertIn("mcp", RESERVED_PAGE_SLUGS)
        self.client.force_login(self.admin)
        r = self.client.post(
            f"/dashboard/sites/{self.tenant_a.pk}/pages/new/",
            {
                "title": "MCP Shadow",
                "slug": "mcp",
                "html_source": SAMPLE_HTML,
            },
            HTTP_HOST="localhost",
        )
        # Form should refuse — either 200 with errors or no Page created.
        self.assertFalse(
            Page.objects.filter(tenant=self.tenant_a, slug="mcp").exists()
        )
        if r.status_code == 200:
            self.assertIn(b"reserved", r.content.lower())

    def test_ac34_no_migrations(self):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        try:
            call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
        except SystemExit as exc:
            self.assertEqual(exc.code, 0, out.getvalue())
