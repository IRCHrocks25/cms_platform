from unittest import mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from core.ghl_crypto import decrypt_token
from core.models import GhlAgencyInstall, GhlInstall

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app123-ver",
                   GHL_CLIENT_SECRET="s", ALLOWED_HOSTS=["testserver"])
class CallbackCompanyBranchTests(TestCase):
    def test_company_install_enumerates_and_stores_agency(self):
        token = {"userType": "Company", "companyId": "co_9", "access_token": "a",
                 "refresh_token": "r", "expires_in": 86400, "scope": "locations.readonly",
                 "companyName": "Acme Agency"}
        locations = [{"id": "loc_a", "name": "Acme"}, {"id": "loc_b", "name": "Beta"}]
        with mock.patch("core.ghl_oauth.exchange_code", return_value=token), \
             mock.patch("core.ghl_oauth.list_installed_locations", return_value=locations):
            resp = self.client.get(reverse("ghl_oauth_callback"), {"code": "c"})
        self.assertEqual(resp.status_code, 302)
        agency = GhlAgencyInstall.objects.get(company_id="co_9")
        self.assertEqual(decrypt_token(agency.access_token), "a")
        self.assertEqual(agency.available_locations, locations)
        self.assertEqual(agency.company_name, "Acme Agency")

    def test_location_install_stores_encrypted_install(self):
        token = {"userType": "Location", "locationId": "loc_x", "access_token": "aa",
                 "refresh_token": "rr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.exchange_code", return_value=token):
            resp = self.client.get(reverse("ghl_oauth_callback"), {"code": "c"})
        self.assertEqual(resp.status_code, 302)
        install = GhlInstall.objects.get(location_id="loc_x")
        self.assertEqual(decrypt_token(install.access_token), "aa")
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)

    @override_settings(GHL_TOKEN_ENCRYPTION_KEY="")
    def test_callback_returns_503_when_encryption_key_unset(self):
        token = {"userType": "Location", "locationId": "loc_z", "access_token": "aa",
                 "refresh_token": "rr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.exchange_code", return_value=token):
            resp = self.client.get(reverse("ghl_oauth_callback"), {"code": "c"})
        self.assertEqual(resp.status_code, 503)
