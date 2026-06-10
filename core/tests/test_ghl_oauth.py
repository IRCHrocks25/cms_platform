"""Tests for the GHL OAuth install flow.

We don't hit GHL's live endpoints — exchange_code is mocked. What we cover:
- State token signs + verifies, expires after TTL.
- /connect/install/ redirects to the leadconnectorhq.com chooselocation URL
  with our state in the query.
- /connect/callback/ verifies state, exchanges the code, persists a
  GhlInstall, and renders the success page.
- /connect/callback/ rejects missing/expired state with a clear message
  instead of stack-trace 500.
"""
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import Client, TestCase, override_settings

from core import ghl_oauth
from core.models import GhlInstall


CLIENT_ID = "appid-versionsuffix"
CLIENT_SECRET = "shhhh"


@override_settings(GHL_CLIENT_ID=CLIENT_ID, GHL_CLIENT_SECRET=CLIENT_SECRET)
class StateTokenTests(TestCase):
    def test_signs_and_verifies_roundtrip(self):
        token = ghl_oauth.sign_state({"foo": "bar"})
        body = ghl_oauth.verify_state(token)
        self.assertEqual(body["foo"], "bar")
        self.assertIn("iat", body)

    def test_expired_token_rejected(self):
        with mock.patch("time.time", return_value=1_000_000):
            token = ghl_oauth.sign_state({"foo": "bar"})
        with self.assertRaises(ghl_oauth.StateInvalid):
            ghl_oauth.verify_state(token, max_age=1)

    def test_tampered_token_rejected(self):
        token = ghl_oauth.sign_state({"foo": "bar"})
        with self.assertRaises(ghl_oauth.StateInvalid):
            ghl_oauth.verify_state(token[:-2] + "xx")


@override_settings(GHL_CLIENT_ID=CLIENT_ID, GHL_CLIENT_SECRET=CLIENT_SECRET)
class InstallRedirectTests(TestCase):
    def test_install_redirects_to_chooselocation_with_state(self):
        client = Client()
        r = client.get("/connect/install/")
        self.assertEqual(r.status_code, 302)
        u = urlparse(r["Location"])
        self.assertEqual(u.scheme, "https")
        self.assertIn("leadconnectorhq.com", u.netloc)
        q = parse_qs(u.query)
        self.assertEqual(q["client_id"], [CLIENT_ID])
        self.assertEqual(q["response_type"], ["code"])
        self.assertIn("state", q)
        # version_id must be the app-id prefix (everything before the first "-")
        self.assertEqual(q["version_id"], [CLIENT_ID.split("-")[0]])
        # State must verify
        ghl_oauth.verify_state(q["state"][0])

    @override_settings(GHL_CLIENT_ID="")
    def test_install_503_when_client_id_missing(self):
        client = Client()
        r = client.get("/connect/install/")
        self.assertEqual(r.status_code, 503)


@override_settings(GHL_CLIENT_ID=CLIENT_ID, GHL_CLIENT_SECRET=CLIENT_SECRET)
class CallbackTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.state = ghl_oauth.sign_state({"source": "install"})

    def test_callback_missing_code_returns_400(self):
        r = self.client.get(f"/connect/callback/?state={self.state}")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"missing code", r.content.lower())

    def test_callback_marketplace_install_accepts_missing_state(self):
        """Marketplace-initiated installs come straight from GHL with no state.
        The user's click on 'Install' inside GHL is the authorization signal,
        so we accept those callbacks without state verification.
        """
        with mock.patch.object(ghl_oauth, "exchange_code", return_value={
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "scope": "locations.readonly",
            "userType": "Location",
            "locationId": "LOC_MARKETPLACE",
            "companyId": "C1",
        }):
            r = self.client.get("/connect/callback/?code=abc")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(GhlInstall.objects.filter(location_id="LOC_MARKETPLACE").exists())

    def test_callback_expired_state_friendly_message(self):
        with mock.patch("time.time", return_value=time.time() - ghl_oauth.STATE_TTL_SECONDS - 60):
            stale = ghl_oauth.sign_state({"source": "install"})
        r = self.client.get(f"/connect/callback/?code=abc&state={stale}")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"expired", r.content.lower())

    def test_callback_persists_install_on_success(self):
        with mock.patch.object(ghl_oauth, "exchange_code", return_value={
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "scope": "locations.readonly users.readonly",
            "userType": "Location",
            "locationId": "LOC_INSTALLED",
            "companyId": "C1",
        }):
            r = self.client.get(f"/connect/callback/?code=abc&state={self.state}")
        self.assertEqual(r.status_code, 200)
        install = GhlInstall.objects.get(location_id="LOC_INSTALLED")
        self.assertEqual(install.access_token, "AT")
        self.assertEqual(install.refresh_token, "RT")
        self.assertEqual(install.user_type, "Location")
        self.assertIn("locations.readonly", install.scopes)

    def test_callback_re_install_updates_existing_row(self):
        GhlInstall.objects.create(
            location_id="LOC_INSTALLED", access_token="OLD",
        )
        with mock.patch.object(ghl_oauth, "exchange_code", return_value={
            "access_token": "NEW",
            "userType": "Location",
            "locationId": "LOC_INSTALLED",
        }):
            r = self.client.get(f"/connect/callback/?code=abc&state={self.state}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(GhlInstall.objects.count(), 1)
        self.assertEqual(
            GhlInstall.objects.get(location_id="LOC_INSTALLED").access_token, "NEW"
        )
