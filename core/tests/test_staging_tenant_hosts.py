"""Deployment contracts for TLS-valid staging tenant hosts (KPILOT-346)."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
PRODUCTION_COMPOSE = REPO_ROOT / "docker-compose.yml"


class StagingTenantHostContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.staging = STAGING_COMPOSE.read_text(encoding="utf-8")
        cls.production = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    def test_staging_generates_one_level_suffixed_tenant_hosts(self):
        self.assertIn(
            "TENANT_BASE_DOMAIN: ${TENANT_BASE_DOMAIN:-sites.katek.app}",
            self.staging,
        )
        self.assertIn(
            "TENANT_SUBDOMAIN_SUFFIX: ${TENANT_SUBDOMAIN_SUFFIX:--staging}",
            self.staging,
        )

    def test_staging_https_router_owns_suffixed_hosts(self):
        self.assertIn(
            r"traefik.http.routers.cmsstg-tenants.rule=HostRegexp(`^[a-z0-9-]+-staging\.sites\.katek\.app$`)",
            self.staging,
        )
        self.assertIn(
            "traefik.http.routers.cmsstg-tenants.service=cmsstg-web",
            self.staging,
        )
        self.assertIn(
            "traefik.http.routers.cmsstg-tenants.priority=200",
            self.staging,
        )

    def test_staging_http_router_redirects_suffixed_hosts(self):
        self.assertIn(
            r"traefik.http.routers.cmsstg-tenants-web.rule=HostRegexp(`^[a-z0-9-]+-staging\.sites\.katek\.app$`)",
            self.staging,
        )
        self.assertIn(
            "traefik.http.routers.cmsstg-tenants-web.middlewares=cmsstg-redirect-to-https",
            self.staging,
        )
        self.assertIn(
            "traefik.http.routers.cmsstg-tenants-web.priority=200",
            self.staging,
        )

    def test_production_tenant_host_contract_is_unchanged(self):
        self.assertNotIn("TENANT_SUBDOMAIN_SUFFIX:", self.production)
        self.assertIn(
            r"traefik.http.routers.cms-tenants.rule=HostRegexp(`^[a-z0-9-]+\.sites\.katek\.app$`)",
            self.production,
        )
        self.assertIn("traefik.http.routers.cms-tenants.priority=10", self.production)
        self.assertIn(
            r"traefik.http.routers.cms-tenants-web.rule=HostRegexp(`^[a-z0-9-]+\.sites\.katek\.app$`)",
            self.production,
        )
        self.assertIn("traefik.http.routers.cms-tenants-web.priority=10", self.production)
