"""CMS-63: every writer of CustomDomain rows keeps the legacy
``Tenant.custom_domain`` display hint in step, via the service layer, so the
MCP tools and the dashboard views can't drift apart.

DNS is always mocked through ``core.services.custom_domains.resolve_a_records``.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import CustomDomain, Template, Tenant
from core.services import custom_domains

User = get_user_model()

TARGET_IP = "203.0.113.7"


def _template():
    return Template.objects.create(
        name="Bare",
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


def _backdate(row, days):
    CustomDomain.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )


@override_settings(CUSTOM_DOMAIN_TARGET_IP=TARGET_IP)
class SyncTenantPrimaryDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _template()

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.template,
            owner=self.staff, custom_domain="",
        )

    def _legacy(self):
        return Tenant.objects.get(pk=self.tenant.pk).custom_domain

    # --- the sync rule ------------------------------------------------------ #

    def test_sync_writes_earliest_verified_domain(self):
        newer = CustomDomain.objects.create(
            tenant=self.tenant, domain="newer.example.com", is_verified=True
        )
        older = CustomDomain.objects.create(
            tenant=self.tenant, domain="older.example.com", is_verified=True
        )
        _backdate(newer, 1)
        _backdate(older, 5)
        changed = custom_domains.sync_tenant_primary_domain(self.tenant)
        self.assertTrue(changed)
        self.assertEqual(self.tenant.custom_domain, "older.example.com")
        self.assertEqual(self._legacy(), "older.example.com")

    def test_sync_ignores_unverified_rows_and_clears_stale_value(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="pending.example.com", is_verified=False
        )
        Tenant.objects.filter(pk=self.tenant.pk).update(custom_domain="gone.example.com")
        self.tenant.refresh_from_db()
        changed = custom_domains.sync_tenant_primary_domain(self.tenant)
        self.assertTrue(changed)
        self.assertEqual(self._legacy(), "")

    def test_sync_is_a_no_op_when_already_in_step(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        self.assertTrue(custom_domains.sync_tenant_primary_domain(self.tenant))
        before = Tenant.objects.get(pk=self.tenant.pk).updated_at
        with CaptureQueriesContext(connection) as ctx:
            self.assertFalse(custom_domains.sync_tenant_primary_domain(self.tenant))
        writes = [q["sql"] for q in ctx.captured_queries if q["sql"].startswith("UPDATE")]
        self.assertEqual(writes, [])
        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).updated_at, before)

    # --- writers ------------------------------------------------------------ #

    def test_verify_custom_domain_syncs_legacy_field_when_it_flips(self):
        row = CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records", return_value=[TARGET_IP]
        ):
            verified, _resolved = custom_domains.verify_custom_domain(row)
        self.assertTrue(verified)
        self.assertEqual(self._legacy(), "www.acme.com")

    def test_verify_custom_domain_leaves_legacy_field_when_dns_does_not_match(self):
        row = CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records", return_value=["10.0.0.1"]
        ):
            verified, _resolved = custom_domains.verify_custom_domain(row)
        self.assertFalse(verified)
        self.assertEqual(self._legacy(), "")

    def test_add_custom_domain_clears_stale_legacy_field(self):
        # A stale hint (row deleted outside the dashboard) must not survive
        # the next write: the new row is unverified, so the hint becomes "".
        Tenant.objects.filter(pk=self.tenant.pk).update(custom_domain="gone.example.com")
        self.tenant.refresh_from_db()
        row, error = custom_domains.add_custom_domain(self.tenant, "www.acme.com")
        self.assertIsNone(error)
        self.assertFalse(row.is_verified)
        self.assertEqual(self._legacy(), "")

    def test_delete_custom_domain_syncs_to_next_verified_or_empty(self):
        first = CustomDomain.objects.create(
            tenant=self.tenant, domain="first.example.com", is_verified=True
        )
        second = CustomDomain.objects.create(
            tenant=self.tenant, domain="second.example.com", is_verified=True
        )
        _backdate(first, 5)
        _backdate(second, 1)
        custom_domains.sync_tenant_primary_domain(self.tenant)
        self.assertEqual(self._legacy(), "first.example.com")

        custom_domains.delete_custom_domain(first)
        self.assertFalse(CustomDomain.objects.filter(pk=first.pk).exists())
        self.assertEqual(self._legacy(), "second.example.com")

        custom_domains.delete_custom_domain(second)
        self.assertEqual(self._legacy(), "")

    # --- review fixes (CMS-63) ---------------------------------------------- #

    def test_sync_compares_against_persisted_value_not_stale_instance(self):
        """Two in-memory copies of one tenant: verify through A, delete through
        B (whose cached custom_domain is still ""). The sync must compare with
        the DB, not B's stale attribute, or the DB keeps naming a dead host."""
        instance_a = Tenant.objects.get(pk=self.tenant.pk)
        instance_b = Tenant.objects.get(pk=self.tenant.pk)
        row = CustomDomain.objects.create(
            tenant=instance_a, domain="www.acme.com", is_verified=False
        )
        with patch(
            "core.services.custom_domains.resolve_a_records", return_value=[TARGET_IP]
        ):
            custom_domains.verify_custom_domain(row)
        self.assertEqual(self._legacy(), "www.acme.com")
        self.assertEqual(instance_b.custom_domain, "")

        row_via_b = CustomDomain.objects.get(pk=row.pk)
        row_via_b.tenant = instance_b
        custom_domains.delete_custom_domain(row_via_b)

        self.assertFalse(CustomDomain.objects.exists())
        self.assertEqual(self._legacy(), "")
        # The passed instance is refreshed to the persisted value too.
        self.assertEqual(instance_b.custom_domain, "")

    def test_sync_refreshes_passed_instance_from_db(self):
        stale = Tenant.objects.get(pk=self.tenant.pk)
        stale.custom_domain = "in-memory-only.example.com"  # never saved
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        self.assertTrue(custom_domains.sync_tenant_primary_domain(stale))
        self.assertEqual(stale.custom_domain, "www.acme.com")
        self.assertEqual(self._legacy(), "www.acme.com")

    def test_reverifying_already_verified_row_repairs_drifted_legacy_field(self):
        row = CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        self.assertEqual(self._legacy(), "")  # drifted (verified before sync existed)
        with patch(
            "core.services.custom_domains.resolve_a_records", return_value=[TARGET_IP]
        ):
            verified, _resolved = custom_domains.verify_custom_domain(row)
        self.assertTrue(verified)
        self.assertEqual(self._legacy(), "www.acme.com")
