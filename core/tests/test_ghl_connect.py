from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.ghl_crypto import decrypt_token, encrypt_token
from core.models import GhlAgencyInstall, GhlInstall, Tenant, Template

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app-ver", GHL_CLIENT_SECRET="s")
class BindLocationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pw")
        self.template = Template.objects.create(name="T", html_source="<div></div>")
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.template, owner=self.owner
        )
        self.agency = GhlAgencyInstall.objects.create(
            company_id="co_1",
            access_token=encrypt_token("agency-access"),
            refresh_token=encrypt_token("agency-refresh"),
            expires_at=timezone.now() + timedelta(hours=1),
            available_locations=[{"id": "loc_a", "name": "Acme HQ"}],
        )

    def test_bind_mints_and_links(self):
        from core.services import ghl_connect

        mint = {"access_token": "loc-access", "refresh_token": "loc-refresh",
                "expires_in": 86400, "scope": "locations.readonly"}
        with mock.patch("core.ghl_oauth.mint_location_token", return_value=mint) as m:
            install = ghl_connect.bind_location(
                agency=self.agency, location_id="loc_a", tenant=self.tenant
            )
        m.assert_called_once()
        self.assertEqual(install.tenant, self.tenant)
        self.assertEqual(install.location_name, "Acme HQ")
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)
        self.assertEqual(decrypt_token(install.access_token), "loc-access")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.ghl_location_id, "loc_a")

    def test_ensure_fresh_agency_token_refreshes_when_expired(self):
        from core.services import ghl_connect

        self.agency.expires_at = timezone.now() - timedelta(seconds=10)
        self.agency.save(update_fields=["expires_at"])
        refreshed = {"access_token": "fresh-access", "refresh_token": "fresh-refresh",
                     "expires_in": 86400}
        with mock.patch("core.ghl_oauth.refresh_access_token", return_value=refreshed) as r:
            token = ghl_connect.ensure_fresh_agency_token(self.agency)
        r.assert_called_once()
        self.assertEqual(token, "fresh-access")
        self.agency.refresh_from_db()
        self.assertEqual(decrypt_token(self.agency.access_token), "fresh-access")
