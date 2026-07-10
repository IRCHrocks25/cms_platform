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
