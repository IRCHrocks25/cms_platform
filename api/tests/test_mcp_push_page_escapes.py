"""CMS-62 — push_page must store backslash escape sequences byte-for-byte.

Reported: an inline <script> with ``'Sending\\u2026'`` came back from the CMS
as the literal ellipsis. This pins down where the escape is decoded: the
JSON-RPC layer (a ``\\u2026`` *JSON* escape decodes to the character, as the
JSON spec requires) versus the tool/storage path (which must not touch it).
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from core.models import Template, Tenant, TenantMembership

User = get_user_model()
CLAUDE_CLIENT_ID = "claude-test"
PROTOCOL = "2025-06-18"

# The page source as it sits in the repo: a JS string literal with a
# backslash-u escape, six ASCII bytes, no ellipsis character anywhere.
PAGE_SOURCE = (
    "<!doctype html><html><body><button>Send</button>"
    "<script>btn.textContent='Sending\\u2026';</script></body></html>"
)
assert "…" not in PAGE_SOURCE and "\\u2026" in PAGE_SOURCE


def _make_token(user, *, token):
    app, _ = Application.objects.get_or_create(
        name="Claude",
        defaults={
            "client_id": CLAUDE_CLIENT_ID,
            "client_type": Application.CLIENT_CONFIDENTIAL,
            "authorization_grant_type": Application.GRANT_AUTHORIZATION_CODE,
            "redirect_uris": "https://example.test/callback",
        },
    )
    return AccessToken.objects.create(
        user=user, application=app, token=token,
        expires=timezone.now() + timedelta(hours=1), scope="read write",
    )


@override_settings(
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
    MCP_ALLOWED_ORIGINS="https://claude.ai",
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
)
class PushPageEscapeRoundTripTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user("owner", "o@ex.com", "x")
        self.member = User.objects.create_user("editor", "e@ex.com", "x")
        tpl = Template.objects.create(name="Home", html_source="<html></html>")
        self.tenant = Tenant.objects.create(
            name="Alpha", subdomain="alpha", template=tpl, owner=self.owner
        )
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.member, role=TenantMembership.ROLE_EDITOR
        )
        _make_token(self.member, token="tok-member")

    def _rpc_raw(self, raw_body: str):
        """POST the exact bytes, so the test controls JSON escaping itself."""
        return self.client.post(
            "/mcp",
            data=raw_body,
            content_type="application/json",
            HTTP_HOST="localhost",
            HTTP_AUTHORIZATION="Bearer tok-member",
            HTTP_MCP_PROTOCOL_VERSION=PROTOCOL,
            HTTP_ACCEPT="application/json",
        )

    def _call(self, name, arguments):
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        resp = self._rpc_raw(body)
        self.assertEqual(resp.status_code, 200, resp.content)
        payload = resp.json()
        self.assertNotIn("error", payload)
        result = payload["result"]
        self.assertFalse(result.get("isError", False), result)
        return result["structuredContent"]

    def test_backslash_u_survives_push_and_read_back(self):
        # json.dumps encodes the backslash as \\ so the wire carries \\u2026
        # and the JSON decoder hands the tool the six ASCII characters.
        pushed = self._call(
            "push_page", {"site": "alpha", "page": "contact", "html": PAGE_SOURCE}
        )
        self.assertFalse(pushed["unchanged"])
        stored = Template.objects.get(pk=pushed["template_id"]).html_source
        self.assertIn("\\u2026", stored)
        self.assertNotIn("…", stored)

        back = self._call("get_page_html", {"site": "alpha", "page": "contact"})
        self.assertEqual(back["html_source"], PAGE_SOURCE)

    def test_a_json_unicode_escape_is_decoded_before_the_tool_sees_it(self):
        # Hand-written wire JSON with a bare …: per RFC 8259 this IS the
        # ellipsis character. The CMS stores what it was given; the client is
        # what has to double the backslash if it means a JS escape.
        wire = (
            '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"push_page",'
            '"arguments":{"site":"alpha","page":"contact",'
            '"html":"<html><body><script>x=\'Sending\\u2026\';</script></body></html>"}}}'
        )
        assert "\\u2026" in wire and "…" not in wire
        resp = self._rpc_raw(wire)
        self.assertEqual(resp.status_code, 200, resp.content)
        sc = resp.json()["result"]["structuredContent"]
        stored = Template.objects.get(pk=sc["template_id"]).html_source
        self.assertIn("…", stored)
        self.assertNotIn("\\u2026", stored)
