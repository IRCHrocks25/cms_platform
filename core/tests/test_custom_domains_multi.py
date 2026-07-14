"""Multiple custom domains per tenant — list management on the per-tenant panel.

The CustomDomain model + route-syncer already handle N domains; these tests pin
the agency dashboard surface so it manages each domain independently (add /
verify / remove) instead of only the newest one.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import CustomDomain, Template, Tenant

User = get_user_model()

TARGET_IP = "203.0.113.7"


def _make_template(name="Bare"):
    return Template.objects.create(
        name=name,
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


@override_settings(TENANT_BASE_DOMAIN="localhost", CUSTOM_DOMAIN_TARGET_IP=TARGET_IP)
class MultiCustomDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=cls.template, owner=cls.staff,
        )
        cls.other = Tenant.objects.create(
            name="Beta", subdomain="beta", template=cls.template, owner=cls.staff,
        )

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def _section(self):
        return self._client().get(
            reverse("dashboard:tenant_custom_domain_section", args=[self.tenant.pk])
        )

    # --- listing ----------------------------------------------------------- #

    def test_section_lists_all_domains_for_tenant(self):
        # Distinct, non-substring hosts so "both present" can't pass spuriously.
        CustomDomain.objects.create(
            tenant=self.tenant, domain="primary-site.com", is_verified=True
        )
        CustomDomain.objects.create(
            tenant=self.tenant, domain="second-site.net", is_verified=False
        )
        body = self._section().content.decode()
        self.assertIn("primary-site.com", body)
        self.assertIn("second-site.net", body)

    # --- verify (per-domain) ---------------------------------------------- #

    def test_verify_targets_only_the_named_domain(self):
        a = CustomDomain.objects.create(
            tenant=self.tenant, domain="primary-site.com", is_verified=False
        )
        b = CustomDomain.objects.create(
            tenant=self.tenant, domain="second-site.net", is_verified=False
        )
        with patch("dashboard.views._resolve_a_records", return_value=[TARGET_IP]):
            resp = self._client().post(
                reverse(
                    "dashboard:tenant_custom_domain_verify",
                    args=[self.tenant.pk, b.pk],
                )
            )
        self.assertEqual(resp.status_code, 200)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertTrue(b.is_verified)
        self.assertFalse(a.is_verified)  # sibling untouched

    def test_verify_rejects_domain_from_another_tenant(self):
        foreign = CustomDomain.objects.create(
            tenant=self.other, domain="foreign-site.com", is_verified=False
        )
        with patch("dashboard.views._resolve_a_records", return_value=[TARGET_IP]):
            resp = self._client().post(
                reverse(
                    "dashboard:tenant_custom_domain_verify",
                    args=[self.tenant.pk, foreign.pk],
                )
            )
        self.assertEqual(resp.status_code, 404)
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_verified)

    # --- delete (per-domain) ---------------------------------------------- #

    def test_delete_removes_only_the_named_domain(self):
        a = CustomDomain.objects.create(
            tenant=self.tenant, domain="primary-site.com", is_verified=True
        )
        b = CustomDomain.objects.create(
            tenant=self.tenant, domain="second-site.net", is_verified=True
        )
        resp = self._client().post(
            reverse(
                "dashboard:tenant_custom_domain_delete",
                args=[self.tenant.pk, a.pk],
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CustomDomain.objects.filter(pk=a.pk).exists())
        self.assertTrue(CustomDomain.objects.filter(pk=b.pk).exists())

    def test_delete_rejects_domain_from_another_tenant(self):
        foreign = CustomDomain.objects.create(
            tenant=self.other, domain="foreign-site.com", is_verified=True
        )
        resp = self._client().post(
            reverse(
                "dashboard:tenant_custom_domain_delete",
                args=[self.tenant.pk, foreign.pk],
            )
        )
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(CustomDomain.objects.filter(pk=foreign.pk).exists())

    # --- add (unchanged rules, now into a list) --------------------------- #

    def test_add_allows_a_second_distinct_domain(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="primary-site.com", is_verified=True
        )
        resp = self._client().post(
            reverse("dashboard:tenant_custom_domain_add", args=[self.tenant.pk]),
            data={"domain": "second-site.net"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.tenant.custom_domains.count(), 2)

    def test_add_still_rejects_a_globally_duplicate_domain(self):
        CustomDomain.objects.create(
            tenant=self.other, domain="taken-site.com", is_verified=True
        )
        resp = self._client().post(
            reverse("dashboard:tenant_custom_domain_add", args=[self.tenant.pk]),
            data={"domain": "taken-site.com"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already in use", resp.content.decode())
        self.assertFalse(
            CustomDomain.objects.filter(tenant=self.tenant, domain="taken-site.com").exists()
        )
