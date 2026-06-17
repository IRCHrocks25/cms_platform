"""Tests for the GHL /embed/ auto-login view.

This view is hit when a user clicks the "KATEK-AI CMS" Custom Menu Link inside
a GHL sub-account. GHL substitutes ?location_id={{location.id}}&email={{user.email}}
into the URL; the view trusts those params (Phase 1), finds the matching
Tenant + User, logs them in, and redirects to the editor."""
import importlib
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core.models import Template, Tenant, TenantMembership


User = get_user_model()


def _enable_auto_login():
    return override_settings(GHL_AUTO_LOGIN=True)


class GhlEmbedViewTests(TestCase):
    def setUp(self):
        self.tpl = Template.objects.create(
            name="t", html_source="<section data-section='a'><h1 data-edit='a.x' data-type='text'>hi</h1></section>"
        )
        self.owner = User.objects.create_user(
            username="acme-owner", email="owner@acme.com", password="x"
        )
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=self.tpl,
            owner=self.owner,
            ghl_location_id="LOC123",
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.owner, role="owner")
        self.client = Client()

    def test_returns_404_when_auto_login_disabled(self):
        with override_settings(GHL_AUTO_LOGIN=False):
            r = self.client.get("/embed/?location_id=LOC123&email=owner@acme.com")
        self.assertEqual(r.status_code, 404)

    @_enable_auto_login()
    def test_redirects_to_login_when_params_missing(self):
        r = self.client.get("/embed/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    @_enable_auto_login()
    def test_returns_404_when_location_id_unknown(self):
        r = self.client.get("/embed/?location_id=NOPE&email=owner@acme.com")
        self.assertEqual(r.status_code, 404)

    @_enable_auto_login()
    def test_tenant_member_redirects_to_tenant_host_dashboard(self):
        # Non-staff tenant member: goes to <sub>.<TENANT_BASE_DOMAIN>/dashboard/
        # so the host-based dashboard router lands them in the tenant editor.
        with override_settings(TENANT_BASE_DOMAIN="sites.katek.app"):
            r = self.client.get("/embed/?location_id=LOC123&email=owner@acme.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)
        self.assertEqual(r["Location"], "https://acme.sites.katek.app/dashboard/")

    @_enable_auto_login()
    def test_local_dev_stays_on_agency_host(self):
        # No per-subdomain TLS on localhost, so don't try to redirect there.
        with override_settings(TENANT_BASE_DOMAIN="localhost"):
            r = self.client.get("/embed/?location_id=LOC123&email=owner@acme.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/dashboard/")

    @_enable_auto_login()
    def test_falls_back_to_tenant_owner_when_email_does_not_match(self):
        # Common case: the GHL user's email doesn't map to a CMS user
        # (or maps to one without access). Embed should still log the
        # session in as the tenant owner so the iframe lands in the editor.
        User.objects.create_user(username="outsider", email="outsider@x.com", password="x")
        with override_settings(TENANT_BASE_DOMAIN="sites.katek.app"):
            r = self.client.get("/embed/?location_id=LOC123&email=outsider@x.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)
        self.assertEqual(r["Location"], "https://acme.sites.katek.app/dashboard/")

    @_enable_auto_login()
    def test_falls_back_to_owner_when_email_omitted(self):
        r = self.client.get("/embed/?location_id=LOC123")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)

    @_enable_auto_login()
    def test_staff_user_lands_on_agency_editor(self):
        User.objects.create_user(
            username="staff", email="staff@agency.com", password="x", is_staff=True
        )
        with override_settings(TENANT_BASE_DOMAIN="sites.katek.app"):
            r = self.client.get("/embed/?location_id=LOC123&email=staff@agency.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], f"/dashboard/sites/{self.tenant.pk}/edit/")

    @_enable_auto_login()
    def test_email_match_is_case_insensitive(self):
        r = self.client.get("/embed/?location_id=LOC123&email=OWNER@ACME.COM")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.owner.pk)


class TenantSettingsGhlFieldTests(TestCase):
    """Agency dashboard settings form can set ghl_location_id."""

    def setUp(self):
        self.tpl = Template.objects.create(
            name="t",
            html_source="<section data-section='a'><h1 data-edit='a.x' data-type='text'>hi</h1></section>",
        )
        self.staff = User.objects.create_user(
            username="staff", email="staff@x.com", password="x", is_staff=True
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.tpl, owner=self.staff,
        )
        self.client = Client()
        self.client.force_login(self.staff)

    def _post(self, **fields):
        data = {"name": self.tenant.name, "subdomain": self.tenant.subdomain, **fields}
        return self.client.post(f"/dashboard/sites/{self.tenant.pk}/settings/", data)

    def test_saving_ghl_location_id_sets_field(self):
        self._post(ghl_location_id="LOC_NEW")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.ghl_location_id, "LOC_NEW")

    def test_blank_value_clears_field(self):
        self.tenant.ghl_location_id = "OLD"
        self.tenant.save()
        self._post(ghl_location_id="")
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.ghl_location_id)

    def test_duplicate_location_id_rejected(self):
        other = Tenant.objects.create(
            name="Other", subdomain="other", template=self.tpl, owner=self.staff,
            ghl_location_id="TAKEN",
        )
        self._post(ghl_location_id="TAKEN")
        self.tenant.refresh_from_db()
        self.assertNotEqual(self.tenant.ghl_location_id, "TAKEN")
        # Other tenant unaffected
        other.refresh_from_db()
        self.assertEqual(other.ghl_location_id, "TAKEN")


class GhlSettingsEnvTests(TestCase):
    """The env vars must be wired so Dokploy values reach Python."""

    def tearDown(self):
        import cms_platform.settings as s
        importlib.reload(s)

    def test_env_var_flips_setting(self):
        with mock.patch.dict(os.environ, {"GHL_AUTO_LOGIN": "1"}):
            import cms_platform.settings as s
            importlib.reload(s)
            self.assertTrue(s.GHL_AUTO_LOGIN)

    def test_default_is_off(self):
        with mock.patch.dict(os.environ, {"GHL_AUTO_LOGIN": "0"}):
            import cms_platform.settings as s
            importlib.reload(s)
            self.assertFalse(s.GHL_AUTO_LOGIN)
