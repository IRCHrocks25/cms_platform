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
class FormsApiTests(TestCase):
    def test_default_scopes_include_forms_readonly(self):
        self.assertIn("forms.readonly", ghl_oauth.DEFAULT_SCOPES)

    def test_lists_and_normalizes_forms_for_exact_location(self):
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured.update(
                {"url": url, "params": params, "headers": headers, "timeout": timeout}
            )
            return _resp(
                200,
                {
                    "forms": [
                        {"id": "form_a", "name": "Contact us"},
                        {"_id": "form_b", "name": "予約フォーム"},
                        {"name": "missing id"},
                    ]
                },
            )

        with mock.patch.object(httpx, "get", side_effect=fake_get):
            forms = ghl_oauth.list_forms(
                access_token="location-token", location_id="loc_authorized"
            )

        self.assertEqual(
            forms,
            [
                {"id": "form_a", "name": "Contact us"},
                {"id": "form_b", "name": "予約フォーム"},
            ],
        )
        self.assertEqual(captured["url"], "https://services.leadconnectorhq.com/forms/")
        self.assertEqual(captured["params"], {"locationId": "loc_authorized"})
        self.assertEqual(captured["headers"]["Authorization"], "Bearer location-token")
        self.assertEqual(captured["headers"]["Version"], ghl_oauth.GHL_API_VERSION)

    def test_unauthorized_forms_response_has_typed_error_without_body(self):
        with mock.patch.object(
            httpx,
            "get",
            return_value=_resp(401, {"error": "secret upstream diagnostic"}),
        ):
            with self.assertRaises(ghl_oauth.GhlFormsAuthorizationFailed) as raised:
                ghl_oauth.list_forms(access_token="token", location_id="loc")
        self.assertNotIn("secret", str(raised.exception))

    def test_network_failure_has_typed_forms_error(self):
        with mock.patch.object(
            httpx, "get", side_effect=httpx.ConnectError("network details")
        ):
            with self.assertRaises(ghl_oauth.GhlFormsRequestFailed):
                ghl_oauth.list_forms(access_token="token", location_id="loc")


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
