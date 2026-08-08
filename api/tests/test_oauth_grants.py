"""CMS-20: only authorization_code + refresh_token are advertised and accepted."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from oauth2_provider.models import AccessToken, Application


User = get_user_model()

REDIRECT_URI = "https://example.test/callback"


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@override_settings(
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
)
class OAuthGrantRestrictionTests(TestCase):
    def test_metadata_advertises_only_authorization_code_and_refresh_token(self):
        response = self.client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["grant_types_supported"],
            ["authorization_code", "refresh_token"],
        )
        self.assertEqual(body["response_types_supported"], ["code"])
        self.assertEqual(body["code_challenge_methods_supported"], ["S256"])
        for forbidden in (
            "implicit",
            "password",
            "client_credentials",
            "urn:ietf:params:oauth:grant-type:device_code",
        ):
            self.assertNotIn(forbidden, body["grant_types_supported"])
        self.assertNotIn("token", body["response_types_supported"])

    def test_password_grant_token_request_is_rejected(self):
        """Prove the server refuses password grants even for a password Application."""
        user = User.objects.create_user("ro-user", password="ro-pass-123")
        Application.objects.create(
            name="PasswordApp",
            client_id="password-client",
            client_secret="test-only-secret",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_PASSWORD,
            redirect_uris="",
        )

        response = self.client.post(
            "/token/",
            {
                "grant_type": "password",
                "username": "ro-user",
                "password": "ro-pass-123",
                "client_id": "password-client",
                "client_secret": "test-only-secret",
                "scope": "read",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(response.json().get("error"), {"unauthorized_client", "invalid_grant", "unsupported_grant_type"})
        self.assertEqual(AccessToken.objects.count(), 0)
        # Username kept only for the request; do not assert the secret value.
        self.assertEqual(user.username, "ro-user")

    def test_implicit_grant_authorization_request_is_rejected(self):
        user = User.objects.create_user("imp-user", password="x")
        Application.objects.create(
            name="ImplicitApp",
            client_id="implicit-client",
            client_secret="",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_IMPLICIT,
            redirect_uris=REDIRECT_URI,
        )
        self.client.force_login(user)

        response = self.client.get(
            "/authorize/",
            {
                "response_type": "token",
                "client_id": "implicit-client",
                "redirect_uri": REDIRECT_URI,
                "scope": "read",
            },
        )

        # Must not render consent or redirect with a fragment access_token.
        if response.status_code == 302:
            location = response["Location"]
            self.assertTrue(
                "error=" in location or "access_token" not in location,
                msg=f"unexpected redirect: {location}",
            )
            self.assertNotIn("access_token", location)
            self.assertIn("error", parse_qs(urlparse(location).query) or parse_qs(urlparse(location).fragment))
        else:
            self.assertIn(response.status_code, (400, 403))
            self.assertNotContains(response, "Authorize", status_code=response.status_code)

    def test_authorization_code_pkce_flow_still_works(self):
        user = User.objects.create_user("owner", password="password123")
        app = Application.objects.create(
            name="Claude",
            client_id="claude-grants-client",
            client_secret="test-only-secret",
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=REDIRECT_URI,
            skip_authorization=False,
        )
        verifier, challenge = _pkce_pair()
        self.assertTrue(self.client.login(username="owner", password="password123"))

        get_resp = self.client.get(
            "/authorize/",
            {
                "response_type": "code",
                "client_id": app.client_id,
                "redirect_uri": REDIRECT_URI,
                "scope": "read",
                "state": "grants-state",
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
        self.assertTrue(params["code"][0])

        token_resp = self.client.post(
            "/token/",
            {
                "grant_type": "authorization_code",
                "code": params["code"][0],
                "redirect_uri": REDIRECT_URI,
                "client_id": app.client_id,
                "client_secret": "test-only-secret",
                "code_verifier": verifier,
            },
        )
        self.assertEqual(token_resp.status_code, 200)
        token_body = token_resp.json()
        self.assertIn("access_token", token_body)
        self.assertEqual(token_body.get("token_type", "").lower(), "bearer")
        self.assertTrue(user.check_password("password123"))
