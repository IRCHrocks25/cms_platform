"""CMS-24: OAuth Dynamic Client Registration (RFC 7591)."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from core.models import Template, Tenant, TenantMembership


User = get_user_model()

REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
CLAUDE_CLIENT_ID = "claude-static-client"


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _register_body(**overrides):
    body = {
        "redirect_uris": [REDIRECT_URI],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "Claude",
    }
    body.update(overrides)
    return body


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
)
class OAuthDcrMetadataTests(TestCase):
    def test_authorization_server_metadata_advertises_registration_endpoint(self):
        response = self.client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(
            body["registration_endpoint"].endswith("/oauth/register"),
            msg=body.get("registration_endpoint"),
        )
        self.assertEqual(
            body["grant_types_supported"],
            ["authorization_code", "refresh_token"],
        )
        self.assertEqual(body["response_types_supported"], ["code"])


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
)
class OAuthDcrRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_post_register_returns_client_id_and_redirect_uri(self):
        response = self.client.post(
            "/oauth/register",
            data=_register_body(),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["client_id"])
        self.assertEqual(body["redirect_uris"], [REDIRECT_URI])
        self.assertEqual(body["grant_types"], ["authorization_code", "refresh_token"])
        self.assertEqual(body.get("response_types"), ["code"])
        self.assertEqual(body["token_endpoint_auth_method"], "none")

        app = Application.objects.get(client_id=body["client_id"])
        self.assertEqual(app.registration_source, Application.RegistrationSource.DCR)
        self.assertEqual(app.client_type, Application.CLIENT_PUBLIC)
        self.assertEqual(
            app.authorization_grant_type,
            Application.GRANT_AUTHORIZATION_CODE,
        )

    def test_dcr_client_completes_authorization_code_pkce_flow(self):
        reg = self.client.post(
            "/oauth/register",
            data=_register_body(),
            content_type="application/json",
        )
        self.assertEqual(reg.status_code, 201)
        client_id = reg.json()["client_id"]

        user = User.objects.create_user("owner", password="password123")
        verifier, challenge = _pkce_pair()
        self.assertTrue(self.client.login(username="owner", password="password123"))

        get_resp = self.client.get(
            "/authorize/",
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "scope": "read",
                "state": "dcr-state",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        self.assertEqual(get_resp.status_code, 200)
        form = get_resp.context["form"]
        post_data = {
            "redirect_uri": form["redirect_uri"].value(),
            "scope": form["scope"].value(),
            "client_id": form["client_id"].value(),
            "state": form["state"].value(),
            "response_type": form["response_type"].value(),
            "code_challenge": form["code_challenge"].value(),
            "code_challenge_method": form["code_challenge_method"].value(),
            "allow": "Authorize",
        }
        for name in ("nonce", "claims", "resource"):
            if name in form.fields and form[name].value() not in (None, ""):
                post_data[name] = form[name].value()

        post_resp = self.client.post("/authorize/", post_data)
        self.assertEqual(post_resp.status_code, 302)
        params = parse_qs(urlparse(post_resp["Location"]).query)
        self.assertIn("code", params)

        token_resp = self.client.post(
            "/token/",
            {
                "grant_type": "authorization_code",
                "code": params["code"][0],
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        self.assertEqual(token_resp.status_code, 200)
        self.assertIn("access_token", token_resp.json())
        self.assertTrue(user.check_password("password123"))

    def test_registration_requesting_password_grant_is_rejected(self):
        response = self.client.post(
            "/oauth/register",
            data=_register_body(grant_types=["password"]),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "invalid_client_metadata")
        self.assertEqual(Application.objects.count(), 0)

    def test_registration_requesting_implicit_grant_is_rejected(self):
        response = self.client.post(
            "/oauth/register",
            data=_register_body(
                grant_types=["implicit"],
                response_types=["token"],
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "invalid_client_metadata")
        self.assertEqual(Application.objects.count(), 0)

    def test_registration_requesting_confidential_client_is_rejected(self):
        response = self.client.post(
            "/oauth/register",
            data=_register_body(token_endpoint_auth_method="client_secret_basic"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "invalid_client_metadata")
        self.assertEqual(Application.objects.count(), 0)

    def test_registration_is_rate_limited(self):
        for i in range(30):
            response = self.client.post(
                "/oauth/register",
                data=_register_body(client_name=f"client-{i}"),
                content_type="application/json",
            )
            if response.status_code == 429:
                break
        else:
            self.fail("expected rate limit after repeated unauthenticated registrations")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json().get("error"), "temporarily_unavailable")

    def test_rate_limit_is_independent_per_forwarded_client_ip(self):
        """Two clients behind the same trusted proxy get separate budgets."""
        for i in range(10):
            response = self.client.post(
                "/oauth/register",
                data=_register_body(client_name=f"a-{i}"),
                content_type="application/json",
                HTTP_X_FORWARDED_FOR="203.0.113.10",
                REMOTE_ADDR="10.0.0.2",
            )
            self.assertEqual(response.status_code, 201, msg=response.content)

        blocked = self.client.post(
            "/oauth/register",
            data=_register_body(client_name="a-blocked"),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="203.0.113.10",
            REMOTE_ADDR="10.0.0.2",
        )
        self.assertEqual(blocked.status_code, 429)

        other = self.client.post(
            "/oauth/register",
            data=_register_body(client_name="b-ok"),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="203.0.113.20",
            REMOTE_ADDR="10.0.0.2",
        )
        self.assertEqual(other.status_code, 201, msg=other.content)

    def test_spoofed_forwarded_ip_from_untrusted_source_cannot_evade_limit(self):
        """Ignore client-IP headers unless REMOTE_ADDR is a known proxy."""
        for i in range(10):
            response = self.client.post(
                "/oauth/register",
                data=_register_body(client_name=f"spoof-{i}"),
                content_type="application/json",
                HTTP_X_FORWARDED_FOR="198.51.100.1",
                REMOTE_ADDR="203.0.113.50",
            )
            self.assertEqual(response.status_code, 201, msg=response.content)

        still_limited = self.client.post(
            "/oauth/register",
            data=_register_body(client_name="spoof-new-xff"),
            content_type="application/json",
            HTTP_X_FORWARDED_FOR="198.51.100.99",
            REMOTE_ADDR="203.0.113.50",
        )
        self.assertEqual(still_limited.status_code, 429)
        self.assertEqual(still_limited.json().get("error"), "temporarily_unavailable")


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    CLAUDE_OAUTH_CLIENT_ID=CLAUDE_CLIENT_ID,
)
class OAuthDcrTokenBindingTests(TestCase):
    """CMS-16 binding must accept DCR clients without weakening membership scope."""

    def setUp(self):
        cache.clear()

    def _make_tenant(self, owner, *, name, subdomain):
        tpl = Template.objects.create(name=f"tpl-{subdomain}", html_source="<div></div>")
        return Tenant.objects.create(
            name=name,
            subdomain=subdomain,
            template=tpl,
            owner=owner,
        )

    def test_token_from_dcr_client_resolves_when_membership_ok(self):
        from api.auth import resolve_access_token

        reg = self.client.post(
            "/oauth/register",
            data=_register_body(),
            content_type="application/json",
        )
        self.assertEqual(reg.status_code, 201)
        app = Application.objects.get(client_id=reg.json()["client_id"])

        owner = User.objects.create_user("owner", "o@ex.com", "x")
        member = User.objects.create_user("editor", "e@ex.com", "x")
        tenant = self._make_tenant(owner, name="Acme", subdomain="acme")
        TenantMembership.objects.create(
            tenant=tenant, user=member, role=TenantMembership.ROLE_EDITOR
        )
        AccessToken.objects.create(
            user=member,
            application=app,
            token="tok-dcr",
            expires=timezone.now() + timedelta(hours=1),
            scope="read",
        )

        principal = resolve_access_token("tok-dcr")

        self.assertIsNotNone(principal)
        self.assertEqual(principal.user, member)
        self.assertEqual(principal.tenant_scopes[0].tenant, tenant)

    def test_token_from_manual_non_claude_app_still_rejected(self):
        from api.auth import resolve_access_token

        other = Application.objects.create(
            name="Manual Other",
            client_id="manual-other",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT_URI,
            registration_source=Application.RegistrationSource.MANUAL,
        )
        owner = User.objects.create_user("owner", "o@ex.com", "x")
        member = User.objects.create_user("editor", "e@ex.com", "x")
        tenant = self._make_tenant(owner, name="Acme", subdomain="acme")
        TenantMembership.objects.create(
            tenant=tenant, user=member, role=TenantMembership.ROLE_EDITOR
        )
        AccessToken.objects.create(
            user=member,
            application=other,
            token="tok-manual",
            expires=timezone.now() + timedelta(hours=1),
            scope="read",
        )

        self.assertIsNone(resolve_access_token("tok-manual"))

    def test_static_claude_client_token_still_resolves(self):
        from api.auth import resolve_access_token

        app = Application.objects.create(
            name="Claude",
            client_id=CLAUDE_CLIENT_ID,
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT_URI,
            registration_source=Application.RegistrationSource.MANUAL,
        )
        user = User.objects.create_superuser("admin", "a@ex.com", "x")
        AccessToken.objects.create(
            user=user,
            application=app,
            token="tok-static",
            expires=timezone.now() + timedelta(hours=1),
            scope="read",
        )

        principal = resolve_access_token("tok-static")

        self.assertIsNotNone(principal)
        self.assertEqual(principal.platform_role, "superadmin")
