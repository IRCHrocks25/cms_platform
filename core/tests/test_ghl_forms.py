from datetime import timedelta
from unittest import mock

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core import ghl_oauth
from core.ghl_crypto import encrypt_token
from core.models import GhlInstall, Template, Tenant


User = get_user_model()
KEY = Fernet.generate_key().decode()


@override_settings(
    GHL_TOKEN_ENCRYPTION_KEY=KEY,
    GHL_CLIENT_ID="app-version",
    GHL_CLIENT_SECRET="secret",
)
class TenantGhlFormsServiceTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user("owner", password="pw")
        template = Template.objects.create(name="Template", html_source="<div></div>")
        self.tenant_a = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=template,
            owner=owner,
            ghl_location_id="loc_alpha",
        )
        self.tenant_b = Tenant.objects.create(
            name="Beta",
            subdomain="beta",
            template=template,
            owner=owner,
            ghl_location_id="loc_beta",
        )
        self.install_a = self._install(
            self.tenant_a, "loc_alpha", "token-alpha"
        )
        self.install_b = self._install(
            self.tenant_b, "loc_beta", "token-beta"
        )

    def _install(self, tenant, location_id, token):
        return GhlInstall.objects.create(
            tenant=tenant,
            location_id=location_id,
            access_token=encrypt_token(token),
            refresh_token=encrypt_token(f"refresh-{location_id}"),
            expires_at=timezone.now() + timedelta(hours=1),
            scopes=["locations.readonly", "forms.readonly"],
            status=GhlInstall.STATUS_CONNECTED,
        )

    def _service(self):
        from core.services import ghl_forms

        return ghl_forms

    def test_lists_only_authorized_tenants_location_forms(self):
        service = self._service()
        with mock.patch(
            "core.ghl_oauth.list_forms",
            return_value=[{"id": "form_alpha", "name": "Alpha contact"}],
        ) as list_forms:
            forms = service.list_forms_for_tenant(self.tenant_a)

        self.assertEqual(forms, [{"id": "form_alpha", "name": "Alpha contact"}])
        list_forms.assert_called_once_with(
            access_token="token-alpha", location_id="loc_alpha"
        )

    def test_mismatched_location_binding_never_falls_back_to_another_install(self):
        service = self._service()
        # The model's unique constraint prevents two tenants sharing one
        # location ID. A stale binding with no tenant-owned install must still
        # fail closed rather than selecting any other connected row.
        self.tenant_a.ghl_location_id = "loc_stale"
        self.tenant_a.save(update_fields=["ghl_location_id"])

        with mock.patch("core.ghl_oauth.list_forms") as list_forms:
            with self.assertRaises(service.GhlFormsUnavailable) as raised:
                service.list_forms_for_tenant(self.tenant_a)

        list_forms.assert_not_called()
        self.assertEqual(raised.exception.code, "not_connected")

    def test_missing_connection_has_operator_facing_error(self):
        service = self._service()
        self.install_a.delete()

        with self.assertRaises(service.GhlFormsUnavailable) as raised:
            service.list_forms_for_tenant(self.tenant_a)

        self.assertEqual(raised.exception.code, "not_connected")
        self.assertIn("connect", raised.exception.public_message.lower())

    def test_disconnected_install_requires_reconnect(self):
        service = self._service()
        self.install_a.status = GhlInstall.STATUS_DISCONNECTED
        self.install_a.save(update_fields=["status"])

        with self.assertRaises(service.GhlFormsUnavailable) as raised:
            service.list_forms_for_tenant(self.tenant_a)

        self.assertEqual(raised.exception.code, "reconnect_required")
        self.assertIn("reconnect", raised.exception.public_message.lower())

    def test_missing_forms_scope_requires_reconsent_without_api_call(self):
        service = self._service()
        self.install_a.scopes = ["locations.readonly"]
        self.install_a.save(update_fields=["scopes"])

        with mock.patch("core.ghl_oauth.list_forms") as list_forms:
            with self.assertRaises(service.GhlFormsUnavailable) as raised:
                service.list_forms_for_tenant(self.tenant_a)

        list_forms.assert_not_called()
        self.assertEqual(raised.exception.code, "reconsent_required")
        self.assertIn("permission", raised.exception.public_message.lower())

    def test_revoked_api_access_marks_install_expired(self):
        service = self._service()
        with mock.patch(
            "core.ghl_oauth.list_forms",
            side_effect=ghl_oauth.GhlFormsAuthorizationFailed(
                "GHL form authorization failed."
            ),
        ):
            with self.assertRaises(service.GhlFormsUnavailable) as raised:
                service.list_forms_for_tenant(self.tenant_a)

        self.install_a.refresh_from_db()
        self.assertEqual(self.install_a.status, GhlInstall.STATUS_EXPIRED)
        self.assertEqual(raised.exception.code, "reconnect_required")

    def test_network_failure_degrades_without_exposing_details(self):
        service = self._service()
        with mock.patch(
            "core.ghl_oauth.list_forms",
            side_effect=ghl_oauth.GhlFormsRequestFailed("secret network details"),
        ):
            with self.assertRaises(service.GhlFormsUnavailable) as raised:
                service.list_forms_for_tenant(self.tenant_a)

        self.assertEqual(raised.exception.code, "temporarily_unavailable")
        self.assertNotIn("secret", raised.exception.public_message)
