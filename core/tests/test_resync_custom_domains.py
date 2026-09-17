"""CMS-63: ``resync_custom_domains`` repairs tenants whose legacy
``Tenant.custom_domain`` hint drifted from their verified CustomDomain rows
(MCP-verified domains, or domains verified before the dashboard sync existed).
It touches only that column: no DNS lookups, no Traefik route file.
"""
from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import CustomDomain, Template, Tenant

User = get_user_model()


def _template():
    return Template.objects.create(
        name="Bare",
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


@override_settings(TENANT_BASE_DOMAIN="sites.example.test")
class ResyncCustomDomainsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _template()

    def _tenant(self, subdomain, legacy=""):
        return Tenant.objects.create(
            name=subdomain, subdomain=subdomain, template=self.template,
            owner=self.staff, custom_domain=legacy,
        )

    def _run(self):
        out = StringIO()
        call_command("resync_custom_domains", stdout=out)
        return out.getvalue()

    def test_repairs_drifted_tenants_and_reports_them(self):
        drifted = self._tenant("drifted", legacy="")
        CustomDomain.objects.create(
            tenant=drifted, domain="www.drifted.com", is_verified=True
        )
        stale = self._tenant("stale", legacy="gone.example.com")
        pending = self._tenant("pending", legacy="www.pending.com")
        CustomDomain.objects.create(
            tenant=pending, domain="www.pending.com", is_verified=False
        )
        in_step = self._tenant("instep", legacy="www.instep.com")
        CustomDomain.objects.create(
            tenant=in_step, domain="www.instep.com", is_verified=True
        )
        plain = self._tenant("plain", legacy="")

        output = self._run()

        self.assertEqual(Tenant.objects.get(pk=drifted.pk).custom_domain, "www.drifted.com")
        self.assertEqual(Tenant.objects.get(pk=stale.pk).custom_domain, "")
        self.assertEqual(Tenant.objects.get(pk=pending.pk).custom_domain, "")
        self.assertEqual(Tenant.objects.get(pk=in_step.pk).custom_domain, "www.instep.com")
        self.assertEqual(Tenant.objects.get(pk=plain.pk).custom_domain, "")

        # Reports each changed tenant with old -> new, and a summary count.
        self.assertIn("drifted", output)
        self.assertIn("www.drifted.com", output)
        self.assertIn("gone.example.com", output)
        self.assertIn("www.pending.com", output)
        self.assertNotIn("instep", output)
        self.assertNotIn("plain", output)
        self.assertIn("3 changed", output)

    def test_is_idempotent(self):
        drifted = self._tenant("drifted", legacy="")
        CustomDomain.objects.create(
            tenant=drifted, domain="www.drifted.com", is_verified=True
        )
        first = self._run()
        self.assertIn("1 changed", first)
        second = self._run()
        self.assertIn("0 changed", second)
        self.assertNotIn("drifted", second)
        self.assertEqual(Tenant.objects.get(pk=drifted.pk).custom_domain, "www.drifted.com")

    def test_never_resolves_dns_or_touches_traefik(self):
        drifted = self._tenant("drifted", legacy="")
        CustomDomain.objects.create(
            tenant=drifted, domain="www.drifted.com", is_verified=True
        )
        with patch("core.services.custom_domains.resolve_a_records") as dns, patch(
            "core.services.traefik_routes.sync_custom_domain_routes"
        ) as traefik:
            self._run()
        dns.assert_not_called()
        traefik.assert_not_called()
        # Verification state is data, not something this command decides.
        self.assertTrue(CustomDomain.objects.get(domain="www.drifted.com").is_verified)
