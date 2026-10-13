"""CMS-41: unknown hosts get a branded 404, never platform surfaces."""
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings

from core.middleware import TenantResolverMiddleware
from core.models import CustomDomain, Template, Tenant

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class HostKindBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("owner", password="x")
        self.staff = User.objects.create_user("op", password="x", is_staff=True)
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", owner=self.owner, is_published=True,
            template=Template.objects.create(
                name="T", html_source="<section data-section='x'><p>Acme home</p></section>",
            ),
        )
        CustomDomain.objects.create(
            tenant=self.tenant, domain="acme.example.com", is_verified=True,
        )
        CustomDomain.objects.create(
            tenant=self.tenant, domain="pending.example.com", is_verified=False,
        )

    def kind(self, host):
        request = RequestFactory().get("/", HTTP_HOST=host)
        TenantResolverMiddleware(lambda r: None)(request)
        return request.host_kind, request.tenant


@override_settings(TENANT_BASE_DOMAIN="sites.example.app", TENANT_SUBDOMAIN_SUFFIX="")
class HostKindTests(HostKindBase):
    def test_classifies_hosts(self):
        cases = {
            "acme.sites.example.app": ("tenant", self.tenant),
            "acme.example.com": ("tenant", self.tenant),
            "ghost.sites.example.app": ("unknown", None),
            "pending.example.com": ("unknown", None),
            "deep.ghost.sites.example.app": ("unknown", None),
            "sites.example.app": ("agency", None),
            "www.sites.example.app": ("agency", None),
            "app.sites.example.app": ("agency", None),
            "127.0.0.1": ("agency", None),
            "localhost": ("agency", None),
            "testserver": ("agency", None),
            "never-heard-of.example.org": ("agency", None),
        }
        for host, expected in cases.items():
            with self.subTest(host=host):
                self.assertEqual(self.kind(host), expected)


@override_settings(
    TENANT_BASE_DOMAIN="sites.example.app", TENANT_SUBDOMAIN_SUFFIX="-staging",
)
class StagingHostKindTests(HostKindBase):
    def test_staging_agency_host_and_reserved_labels_stay_agency(self):
        self.assertEqual(self.kind("staging.sites.example.app"), ("agency", None))
        self.assertEqual(self.kind("www.sites.example.app"), ("agency", None))
        self.assertEqual(self.kind("acme-staging.sites.example.app"), ("tenant", self.tenant))
        self.assertEqual(self.kind("ghost-staging.sites.example.app"), ("unknown", None))


@override_settings(
    TENANT_BASE_DOMAIN="sites.example.app", TENANT_SUBDOMAIN_SUFFIX="",
    MONITOR_BASE_HOST="", STORAGES=STORAGES,
)
class UnknownHostPageTests(HostKindBase):
    PATHS = ("/", "/login/", "/privacy/", "/terms/", "/dashboard/", "/some/page/", "/mcp")

    def assert_branded_404(self, response, host):
        self.assertEqual(response.status_code, 404)
        body = response.content.decode()
        self.assertIn("There's no website here yet", body)
        self.assertIn(host, body)
        self.assertIn('href="http://sites.example.app/"', body)
        self.assertIn('name="robots" content="noindex"', body)
        self.assertNotIn('name="password"', body)
        self.assertNotIn("Privacy Policy", body)

    def test_every_path_on_unknown_subdomain_is_branded_404(self):
        host = "ghost.sites.example.app"
        for client in (Client(), self.staff_client()):
            for path in self.PATHS:
                with self.subTest(path=path, staff=client is not None):
                    self.assert_branded_404(client.get(path, HTTP_HOST=host), host)

    def test_unverified_custom_domain_shows_pending_copy(self):
        response = Client().get("/", HTTP_HOST="pending.example.com")
        self.assert_branded_404(response, "pending.example.com")
        self.assertIn("still being verified", response.content.decode())

    def test_unknown_subdomain_does_not_claim_pending_verification(self):
        response = Client().get("/", HTTP_HOST="ghost.sites.example.app")
        self.assertNotIn("still being verified", response.content.decode())

    def test_agency_reserved_and_tenant_hosts_are_unchanged(self):
        login = Client().get("/login/", HTTP_HOST="sites.example.app")
        self.assertEqual(login.status_code, 200)
        self.assertContains(login, 'name="password"')
        www = Client().get("/login/", HTTP_HOST="www.sites.example.app")
        self.assertContains(www, 'name="password"')
        tenant = Client().get("/", HTTP_HOST="acme.sites.example.app")
        self.assertEqual(tenant.status_code, 200)
        self.assertContains(tenant, "Acme home")
        self.assertNotContains(tenant, "There's no website here yet")

    def test_cta_prefers_monitor_base_host(self):
        with self.settings(MONITOR_BASE_HOST="staging.sites.example.app"):
            response = Client().get("/", HTTP_HOST="ghost.sites.example.app", secure=True)
        self.assertContains(
            response, 'href="https://staging.sites.example.app/"', status_code=404,
        )

    def test_cta_keeps_request_port(self):
        response = Client().get("/", HTTP_HOST="ghost.sites.example.app:8000")
        self.assertContains(
            response, 'href="http://sites.example.app:8000/"', status_code=404,
        )

    def staff_client(self):
        client = Client()
        client.force_login(self.staff)
        return client
