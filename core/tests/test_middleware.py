from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from core.middleware import TenantResolverMiddleware
from core.models import CustomDomain, Template, Tenant


def _make_template():
    return Template.objects.create(
        name="Bare", html_source="<section data-section='x'></section>"
    )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TenantResolverMiddlewareTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("owner", password="x")
        self.template = _make_template()
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=self.template,
            owner=self.owner,
        )
        self.middleware = TenantResolverMiddleware(lambda r: r)
        self.factory = RequestFactory()

    def _get(self, host):
        request = self.factory.get("/", HTTP_HOST=host)
        self.middleware(request)
        return request

    def test_tenant_subdomain_resolves(self):
        request = self._get("acme.localhost:8000")
        self.assertEqual(request.tenant, self.tenant)

    def test_tenant_subdomain_resolves_without_port(self):
        request = self._get("acme.localhost")
        self.assertEqual(request.tenant, self.tenant)

    def test_app_subdomain_is_reserved(self):
        request = self._get("app.localhost:8000")
        self.assertIsNone(request.tenant)

    def test_www_subdomain_is_reserved(self):
        request = self._get("www.localhost:8000")
        self.assertIsNone(request.tenant)

    def test_api_subdomain_is_reserved(self):
        request = self._get("api.localhost:8000")
        self.assertIsNone(request.tenant)

    def test_bare_localhost_resolves_to_none(self):
        request = self._get("localhost:8000")
        self.assertIsNone(request.tenant)

    def test_unknown_subdomain_resolves_to_none(self):
        request = self._get("ghost.localhost:8000")
        self.assertIsNone(request.tenant)

    def test_resolves_unpublished_tenant(self):
        # Members must be able to reach the editor before publish.
        self.assertFalse(self.tenant.is_published)
        request = self._get("acme.localhost:8000")
        self.assertEqual(request.tenant, self.tenant)


@override_settings(TENANT_BASE_DOMAIN="yourdomain.com")
class TenantResolverProductionDomainTests(TestCase):
    def setUp(self):
        User = get_user_model()
        owner = User.objects.create_user("owner2", password="x")
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=_make_template(),
            owner=owner,
        )
        self.middleware = TenantResolverMiddleware(lambda r: r)
        self.factory = RequestFactory()

    def _get(self, host):
        request = self.factory.get("/", HTTP_HOST=host)
        self.middleware(request)
        return request

    def test_production_subdomain_resolves(self):
        request = self._get("acme.yourdomain.com")
        self.assertEqual(request.tenant, self.tenant)

    def test_bare_production_domain_resolves_to_none(self):
        request = self._get("yourdomain.com")
        self.assertIsNone(request.tenant)

    def test_other_domain_resolves_to_none(self):
        request = self._get("acme.someoneelse.com")
        self.assertIsNone(request.tenant)


@override_settings(TENANT_BASE_DOMAIN="katek.app", ALLOWED_HOSTS=["proxy.sites.katek.app", "testserver"])
class TenantResolverCustomDomainForwardedHostTests(TestCase):
    """Host rewritten at edge; original domain in ``X-Forwarded-Host``."""

    def setUp(self):
        User = get_user_model()
        owner = User.objects.create_user("owner3", password="x")
        self.tenant = Tenant.objects.create(
            name="Client",
            subdomain="client",
            template=_make_template(),
            owner=owner,
        )
        CustomDomain.objects.create(
            tenant=self.tenant,
            domain="www.clientbrand.com",
            is_verified=True,
        )
        self.middleware = TenantResolverMiddleware(lambda r: r)
        self.factory = RequestFactory()

    def test_custom_domain_uses_x_forwarded_host_when_host_is_proxy(self):
        request = self.factory.get(
            "/",
            HTTP_HOST="proxy.sites.katek.app",
            HTTP_X_FORWARDED_HOST="www.clientbrand.com",
        )
        self.middleware(request)
        self.assertEqual(request.tenant, self.tenant)

