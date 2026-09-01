"""GET /api/monitored-hosts, the host list for the off-box uptime checker.

Written after 2026-09-01, when six client sites were unreachable for about a day
and nothing alerted. The endpoint exists so the checker never needs a hardcoded
list: a domain verified today is watched today.
"""
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import CustomDomain, Template, Tenant

TOKEN = "test-monitor-token"
URL = "/api/monitored-hosts"


@override_settings(TENANT_BASE_DOMAIN="sites.katek.app")
class MonitoredHostsTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("owner", password="x")
        tpl = Template.objects.create(name="T", slug="t", html_source="<html></html>")
        self.published = Tenant.objects.create(
            subdomain="acme", name="Acme", template=tpl, owner=owner, is_published=True
        )
        CustomDomain.objects.create(
            tenant=self.published, domain="verified.example", is_verified=True
        )
        CustomDomain.objects.create(
            tenant=self.published, domain="pending.example", is_verified=False
        )

    def get(self, token=TOKEN):
        headers = {"HTTP_X_MONITOR_TOKEN": token} if token is not None else {}
        return self.client.get(URL, **headers)

    @mock.patch.dict(os.environ, {"MONITOR_TOKEN": TOKEN})
    def test_lists_agency_hosts_and_verified_custom_domains(self):
        hosts = self.get().json()["hosts"]
        self.assertIn("https://sites.katek.app/", hosts)
        self.assertIn("https://sites.katek.app/login/", hosts)
        self.assertIn("https://verified.example/", hosts)

    @mock.patch.dict(os.environ, {"MONITOR_TOKEN": TOKEN})
    def test_unverified_domain_is_not_watched(self):
        # An unverified domain has no Traefik router, so watching it would
        # alert forever on a domain that was never meant to serve.
        self.assertNotIn("https://pending.example/", self.get().json()["hosts"])

    @mock.patch.dict(os.environ, {"MONITOR_TOKEN": TOKEN})
    def test_tenant_subdomains_are_excluded(self):
        # They share one wildcard router, and an unknown subdomain returns 200
        # anyway, so a check against one proves nothing.
        self.assertNotIn("https://acme.sites.katek.app/", self.get().json()["hosts"])

    @mock.patch.dict(os.environ, {"MONITOR_TOKEN": TOKEN})
    def test_wrong_token_is_rejected(self):
        self.assertEqual(self.get(token="wrong").status_code, 401)

    @mock.patch.dict(os.environ, {"MONITOR_TOKEN": TOKEN})
    def test_missing_token_is_rejected(self):
        self.assertEqual(self.get(token=None).status_code, 401)

    @mock.patch.dict(os.environ, {"MONITOR_TOKEN": ""})
    def test_unset_server_token_returns_503_not_an_empty_list(self):
        # A misconfigured deploy must read as broken. An empty 200 would leave
        # the checker happily watching nothing.
        self.assertEqual(self.get().status_code, 503)
