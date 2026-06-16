from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import CustomDomain, Template, Tenant
from core.services.traefik_routes import _build_config, _is_protected


def _tenant():
    User = get_user_model()
    owner = User.objects.create_user("o", password="x")
    tpl = Template.objects.create(
        name="T", html_source="<section data-section='x'></section>"
    )
    return Tenant.objects.create(name="A", subdomain="a", template=tpl, owner=owner)


class BuildConfigTests(TestCase):
    def test_emits_host_rule_with_letsencrypt_resolver(self):
        """Direct-to-origin model: Host(`<domain>`) + certResolver=letsencrypt,
        so Traefik ACME-issues a real public cert per client domain."""
        t = _tenant()
        cd = CustomDomain.objects.create(
            tenant=t, domain="www.acme.com", is_verified=True
        )
        router = _build_config([cd])["http"]["routers"][f"cms-cd-{cd.pk}"]
        self.assertEqual(router["rule"], "Host(`www.acme.com`)")
        self.assertEqual(router["tls"], {"certResolver": "letsencrypt"})
        self.assertEqual(router["service"], "cms-web@docker")
        self.assertEqual(router["entryPoints"], ["websecure"])

    def test_emits_http_to_https_redirect_router(self):
        """Each domain also gets a :80 router that 308s to HTTPS (custom
        domains hit the origin directly — no CF edge to upgrade http://)."""
        t = _tenant()
        cd = CustomDomain.objects.create(
            tenant=t, domain="www.acme.com", is_verified=True
        )
        cfg = _build_config([cd])
        web = cfg["http"]["routers"][f"cms-cd-{cd.pk}-web"]
        self.assertEqual(web["rule"], "Host(`www.acme.com`)")
        self.assertEqual(web["entryPoints"], ["web"])
        self.assertEqual(web["middlewares"], ["cms-redirect-to-https"])
        mw = cfg["http"]["middlewares"]["cms-redirect-to-https"]
        self.assertEqual(mw["redirectScheme"]["scheme"], "https")
        self.assertTrue(mw["redirectScheme"]["permanent"])

    def test_empty_set_is_valid_empty_config(self):
        self.assertEqual(_build_config([]), {"http": {"routers": {}}})

    @override_settings(TENANT_BASE_DOMAIN="sites.katek.app")
    def test_protected_hosts_emit_no_router(self):
        """Our own infra + the tenant base domain (and its subdomains) must
        never get a custom-domain router — that's the catch-all safety net."""
        for host in (
            "katek.app",
            "sites.katek.app",
            "dokploy.katek.app",
            "acme.sites.katek.app",
        ):
            self.assertTrue(_is_protected(host), host)

        t = _tenant()
        row = CustomDomain(tenant=t, domain="sites.katek.app", is_verified=True)
        row.pk = 999  # unsaved; _build_config only reads .pk/.domain
        self.assertEqual(_build_config([row])["http"]["routers"], {})

    def test_non_protected_client_domain_is_allowed(self):
        self.assertFalse(_is_protected("training.acme.com"))
