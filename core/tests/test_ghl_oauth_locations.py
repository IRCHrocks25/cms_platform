from unittest import mock

import httpx
from django.test import TestCase, override_settings

from core import ghl_oauth


def _resp(status, json_body):
    request = httpx.Request("GET", "https://services.leadconnectorhq.com/x")
    return httpx.Response(status, json=json_body, request=request)


@override_settings(GHL_CLIENT_ID="app123-ver", GHL_CLIENT_SECRET="secret")
class InstalledLocationsTests(TestCase):
    def test_parses_locations(self):
        body = {"locations": [
            {"_id": "loc_a", "name": "Acme"},
            {"id": "loc_b", "name": "Beta"},
            {"name": "no id — skipped"},
        ]}
        with mock.patch.object(httpx, "get", return_value=_resp(200, body)):
            out = ghl_oauth.list_installed_locations(
                agency_access_token="tok", company_id="co", app_id="app123"
            )
        self.assertEqual(out, [
            {"id": "loc_a", "name": "Acme"},
            {"id": "loc_b", "name": "Beta"},
        ])

    def test_raises_on_error_status(self):
        with mock.patch.object(httpx, "get", return_value=_resp(401, {"error": "nope"})):
            with self.assertRaises(ghl_oauth.TokenExchangeFailed):
                ghl_oauth.list_installed_locations(
                    agency_access_token="tok", company_id="co", app_id="app123"
                )

    def test_asserts_request_shape(self):
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["params"] = params
            captured["headers"] = headers
            return _resp(200, {"locations": []})

        with mock.patch.object(httpx, "get", side_effect=fake_get):
            ghl_oauth.list_installed_locations(
                agency_access_token="tok", company_id="co", app_id="app123"
            )
        self.assertEqual(captured["params"], {"companyId": "co", "appId": "app123"})
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tok")

    def test_raises_on_network_error(self):
        with mock.patch.object(httpx, "get", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(ghl_oauth.TokenExchangeFailed):
                ghl_oauth.list_installed_locations(
                    agency_access_token="tok", company_id="co", app_id="app123"
                )


@override_settings(GHL_CLIENT_ID="app123-ver", GHL_CLIENT_SECRET="secret")
class RefreshTokenTests(TestCase):
    def test_posts_refresh_grant(self):
        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            return _resp(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 86400})

        with mock.patch.object(httpx, "post", side_effect=fake_post):
            out = ghl_oauth.refresh_access_token(refresh_token="r1", user_type="Company")
        self.assertEqual(out["access_token"], "new")
        self.assertEqual(captured["data"]["grant_type"], "refresh_token")
        self.assertEqual(captured["data"]["user_type"], "Company")

    @override_settings(GHL_CLIENT_ID="", GHL_CLIENT_SECRET="")
    def test_missing_credentials_raises_runtimeerror(self):
        with self.assertRaises(RuntimeError):
            ghl_oauth.refresh_access_token(refresh_token="r1")


@override_settings(GHL_CLIENT_ID="app123-ver", GHL_CLIENT_SECRET="secret")
class MintLocationTokenTests(TestCase):
    def test_raises_on_network_error(self):
        with mock.patch.object(httpx, "post", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(ghl_oauth.TokenExchangeFailed):
                ghl_oauth.mint_location_token(
                    agency_access_token="tok", company_id="co", location_id="loc"
                )
