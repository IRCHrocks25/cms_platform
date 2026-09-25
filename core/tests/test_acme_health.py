"""CMS-65: the route-syncer heals a failed first ACME issuance.

Traefik only retries a certificate when its config changes, so a domain whose
first order failed kept the default cert until someone removed and re-added
it. ``check_pending_certificates`` probes each verified-but-unconfirmed domain
and bumps its router generation when the origin still can't serve a valid
cert. The TLS probe is the only thing faked.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import CustomDomain, Template, Tenant
from core.services import acme_health, custom_domains

TARGET_IP = "203.0.113.7"


def _tenant():
    owner = get_user_model().objects.create_user("o", password="x")
    tpl = Template.objects.create(
        name="T", html_source="<section data-section='x'></section>"
    )
    return Tenant.objects.create(name="A", subdomain="a", template=tpl, owner=owner)


class CheckPendingCertificatesTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.cd = CustomDomain.objects.create(
            tenant=_tenant(),
            domain="www.acme.com",
            is_verified=True,
            verified_at=self.now - timedelta(minutes=10),
        )

    def _check(self, probe_ok, now=None):
        probe = lambda domain: probe_ok  # noqa: E731
        acme_health.check_pending_certificates(now=now or self.now, probe=probe)
        self.cd.refresh_from_db()

    def test_valid_cert_is_confirmed_without_a_bump(self):
        self._check(True)
        self.assertEqual(self.cd.cert_confirmed_at, self.now)
        self.assertEqual(self.cd.acme_generation, 0)

    def test_failed_cert_bumps_generation(self):
        self._check(False)
        self.assertIsNone(self.cd.cert_confirmed_at)
        self.assertEqual(self.cd.acme_generation, 1)
        self.assertEqual(self.cd.acme_retry_count, 1)
        self.assertEqual(self.cd.acme_retried_at, self.now)

    def test_waits_out_the_grace_period_after_verification(self):
        """The first ACME order runs on the first HTTPS hit; don't judge it
        before it has had a chance."""
        self.cd.verified_at = self.now - timedelta(seconds=30)
        self.cd.save()
        with patch.object(acme_health, "probe_certificate") as probe:
            acme_health.check_pending_certificates(now=self.now, probe=probe)
        probe.assert_not_called()

    def test_retries_at_most_once_per_window(self):
        """Let's Encrypt allows 5 failed validations per hostname per hour."""
        self._check(False)
        self._check(False, now=self.now + timedelta(minutes=5))
        self.assertEqual(self.cd.acme_generation, 1)
        self._check(False, now=self.now + acme_health.RETRY_INTERVAL)
        self.assertEqual(self.cd.acme_generation, 2)

    def test_stops_after_max_automatic_retries(self):
        now = self.now
        for _ in range(acme_health.MAX_AUTO_RETRIES + 2):
            self._check(False, now=now)
            now += acme_health.RETRY_INTERVAL
        self.assertEqual(self.cd.acme_generation, acme_health.MAX_AUTO_RETRIES)

    def test_confirmed_and_unverified_domains_are_not_probed(self):
        self.cd.cert_confirmed_at = self.now
        self.cd.save()
        CustomDomain.objects.create(
            tenant=self.cd.tenant, domain="new.acme.com", is_verified=False
        )
        with patch.object(acme_health, "probe_certificate") as probe:
            acme_health.check_pending_certificates(now=self.now, probe=probe)
        probe.assert_not_called()


@override_settings(CUSTOM_DOMAIN_TARGET_IP=TARGET_IP)
class ReverifyForcesReissueTests(TestCase):
    """CMS-65: re-running verify on a verified domain whose cert never came
    through must trigger a new ACME attempt (it used to do nothing)."""

    def setUp(self):
        self.now = timezone.now()
        self.cd = CustomDomain.objects.create(
            tenant=_tenant(),
            domain="www.acme.com",
            is_verified=True,
            verified_at=self.now - timedelta(hours=2),
            acme_retry_count=acme_health.MAX_AUTO_RETRIES,
        )

    def _verify(self, probe_ok):
        with patch.object(
            custom_domains, "resolve_a_records", return_value=[TARGET_IP]
        ), patch.object(acme_health, "probe_certificate", return_value=probe_ok):
            result = custom_domains.verify_custom_domain(self.cd)
        self.cd.refresh_from_db()
        return result

    def test_reverify_with_bad_cert_bumps_and_resets_the_retry_budget(self):
        self.assertEqual(self._verify(False), (True, [TARGET_IP]))
        self.assertEqual(self.cd.acme_generation, 1)
        self.assertEqual(self.cd.acme_retry_count, 0)

    def test_reverify_with_good_cert_confirms_it(self):
        self._verify(True)
        self.assertIsNotNone(self.cd.cert_confirmed_at)
        self.assertEqual(self.cd.acme_generation, 0)

    def test_repeated_clicks_inside_the_floor_bump_once(self):
        self._verify(False)
        self._verify(False)
        self.assertEqual(self.cd.acme_generation, 1)

    def test_first_verification_does_not_probe(self):
        """A domain that just became verified has no router yet."""
        self.cd.is_verified = False
        self.cd.verified_at = None
        self.cd.save()
        with patch.object(
            custom_domains, "resolve_a_records", return_value=[TARGET_IP]
        ), patch.object(acme_health, "probe_certificate") as probe:
            custom_domains.verify_custom_domain(self.cd)
        probe.assert_not_called()


class ProbeCertificateTests(TestCase):
    def test_unreachable_origin_counts_as_no_valid_cert(self):
        with override_settings(CUSTOM_DOMAIN_TLS_PROBE_HOST="127.0.0.1:1"):
            self.assertFalse(acme_health.probe_certificate("www.acme.com"))


class SyncCommandRunsHealthCheckTests(TestCase):
    """The route-syncer loop is the only place with a Traefik mount, so the
    health check runs there, before the routes are regenerated."""

    def test_command_checks_certificates_then_syncs(self):
        import tempfile

        from django.core.management import call_command

        calls = []
        with tempfile.TemporaryDirectory() as tmp, override_settings(
            TRAEFIK_DYNAMIC_DIR=tmp
        ), patch(
            "core.management.commands.sync_traefik_routes.check_pending_certificates",
            side_effect=lambda: calls.append("check"),
        ), patch(
            "core.management.commands.sync_traefik_routes.sync_custom_domain_routes",
            side_effect=lambda: calls.append("sync") or True,
        ):
            call_command("sync_traefik_routes")
        self.assertEqual(calls, ["check", "sync"])

    def test_health_check_error_does_not_block_route_sync(self):
        import tempfile

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp, override_settings(
            TRAEFIK_DYNAMIC_DIR=tmp
        ), patch(
            "core.management.commands.sync_traefik_routes.check_pending_certificates",
            side_effect=RuntimeError("boom"),
        ), patch(
            "core.management.commands.sync_traefik_routes.sync_custom_domain_routes",
            return_value=True,
        ) as sync:
            call_command("sync_traefik_routes")
        sync.assert_called_once()
