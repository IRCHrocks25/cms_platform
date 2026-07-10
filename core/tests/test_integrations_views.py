from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.ghl_crypto import encrypt_token
from core.models import GhlAgencyInstall, GhlInstall, Tenant, Template

KEY = Fernet.generate_key().decode()


@override_settings(GHL_TOKEN_ENCRYPTION_KEY=KEY, GHL_CLIENT_ID="app-ver",
                   GHL_CLIENT_SECRET="s", ALLOWED_HOSTS=["testserver"], TENANT_BASE_DOMAIN="localhost")
class IntegrationsViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.owner = User.objects.create_user("client", password="pw")
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

    def test_integrations_page_lists_agency(self):
        resp = self.client.get(reverse("dashboard:integrations"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "co_1")
        self.assertContains(resp, "Acme HQ")

    def test_bind_creates_install(self):
        mint = {"access_token": "la", "refresh_token": "lr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.mint_location_token", return_value=mint):
            resp = self.client.post(reverse("dashboard:integrations_bind"), {
                "agency_id": self.agency.pk, "location_id": "loc_a", "tenant_id": self.tenant.pk,
            })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(GhlInstall.objects.filter(location_id="loc_a", tenant=self.tenant).exists())

    def test_bind_rejects_location_already_on_other_tenant(self):
        other = Tenant.objects.create(name="Beta", subdomain="beta", template=self.template,
                                      owner=self.owner, ghl_location_id="loc_a")
        resp = self.client.post(reverse("dashboard:integrations_bind"), {
            "agency_id": self.agency.pk, "location_id": "loc_a", "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(GhlInstall.objects.filter(location_id="loc_a", tenant=self.tenant).exists())

    def test_bind_rejects_unknown_location(self):
        resp = self.client.post(reverse("dashboard:integrations_bind"), {
            "agency_id": self.agency.pk, "location_id": "loc_UNKNOWN", "tenant_id": self.tenant.pk,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(GhlInstall.objects.filter(location_id="loc_UNKNOWN").exists())

    def test_reconnect_remints(self):
        install = GhlInstall.objects.create(
            location_id="loc_a", agency=self.agency, tenant=self.tenant,
            access_token=encrypt_token("old"), status=GhlInstall.STATUS_DISCONNECTED,
        )
        mint = {"access_token": "new", "refresh_token": "nr", "expires_in": 86400, "scope": ""}
        with mock.patch("core.ghl_oauth.mint_location_token", return_value=mint):
            resp = self.client.post(reverse("dashboard:integrations_reconnect"),
                                    {"install_id": install.pk})
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.assertEqual(install.status, GhlInstall.STATUS_CONNECTED)

    def test_reconnect_orphan_install_shows_error_not_500(self):
        from django.contrib.messages import get_messages
        orphan = GhlInstall.objects.create(
            location_id="loc_orphan", access_token=encrypt_token("x")
        )
        resp = self.client.post(reverse("dashboard:integrations_reconnect"),
                                {"install_id": orphan.pk})
        self.assertEqual(resp.status_code, 302)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any("Reconnect failed" in m for m in msgs))

    def test_disconnect_marks_disconnected(self):
        install = GhlInstall.objects.create(
            location_id="loc_a", agency=self.agency, tenant=self.tenant,
            access_token=encrypt_token("x"), status=GhlInstall.STATUS_CONNECTED,
        )
        resp = self.client.post(reverse("dashboard:integrations_disconnect"),
                                {"install_id": install.pk})
        self.assertEqual(resp.status_code, 302)
        install.refresh_from_db()
        self.assertEqual(install.status, GhlInstall.STATUS_DISCONNECTED)

    def test_disconnect_clears_tenant_location(self):
        self.tenant.ghl_location_id = "loc_a"
        self.tenant.save(update_fields=["ghl_location_id"])
        install = GhlInstall.objects.create(
            location_id="loc_a", agency=self.agency, tenant=self.tenant,
            access_token=encrypt_token("x"), status=GhlInstall.STATUS_CONNECTED,
        )
        self.client.post(reverse("dashboard:integrations_disconnect"), {"install_id": install.pk})
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.ghl_location_id)

    def test_refresh_locations_updates_list(self):
        new_locs = [{"id": "loc_a", "name": "Acme HQ"}, {"id": "loc_c", "name": "Gamma"}]
        with mock.patch("core.services.ghl_connect.ensure_fresh_agency_token", return_value="tok"), \
             mock.patch("core.ghl_oauth.list_installed_locations", return_value=new_locs):
            resp = self.client.post(reverse("dashboard:integrations_refresh_locations"),
                                    {"agency_id": self.agency.pk})
        self.assertEqual(resp.status_code, 302)
        self.agency.refresh_from_db()
        self.assertEqual(self.agency.available_locations, new_locs)
