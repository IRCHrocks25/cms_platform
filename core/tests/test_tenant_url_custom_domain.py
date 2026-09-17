"""CMS-63: tenant URL helpers must derive the public host from the verified
``CustomDomain`` rows (what routing actually serves), not from the legacy
``Tenant.custom_domain`` display hint, which drifted for tenants whose domain
was verified through the MCP tool or before the dashboard sync existed.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import CustomDomain, Template, Tenant
from core.urls_helpers import (
    build_tenant_url_bundle,
    tenant_canonical_public_url,
    tenant_editor_url,
    tenant_login_url,
    tenant_primary_custom_domain,
    tenant_public_url,
)

User = get_user_model()


def _template(name="Bare"):
    return Template.objects.create(
        name=name,
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


def _backdate(row, days):
    CustomDomain.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )


@override_settings(TENANT_BASE_DOMAIN="sites.example.test", TENANT_DEV_BASE_DOMAIN="lvh.me")
class TenantUrlCustomDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _template()

    def setUp(self):
        # Legacy display field deliberately left empty: the production drift.
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=self.template,
            owner=self.staff, custom_domain="",
        )
        self.request = RequestFactory(HTTP_HOST="sites.example.test").get("/dashboard/")

    # --- verified row wins even when the legacy field is empty ------------- #

    def test_public_url_uses_verified_custom_domain_when_legacy_field_empty(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        self.assertEqual(self.tenant.custom_domain, "")
        self.assertEqual(
            tenant_public_url(self.request, self.tenant), "https://www.acme.com/"
        )

    def test_canonical_public_url_uses_verified_custom_domain_when_legacy_field_empty(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        self.assertEqual(
            tenant_canonical_public_url(self.tenant), "https://www.acme.com/"
        )
        self.assertEqual(
            tenant_canonical_public_url(self.tenant, page_slug="about"),
            "https://www.acme.com/about",
        )

    def test_login_and_editor_urls_follow_verified_custom_domain(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        self.assertEqual(
            tenant_login_url(self.request, self.tenant), "https://www.acme.com/login/"
        )
        self.assertEqual(
            tenant_editor_url(self.request, self.tenant),
            "https://www.acme.com/dashboard/",
        )

    def test_url_bundle_flags_custom_domain_from_verified_row(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        bundle = build_tenant_url_bundle(self.request, self.tenant)
        self.assertTrue(bundle["has_custom_domain"])
        self.assertEqual(bundle["public_url"], "https://www.acme.com/")
        self.assertEqual(bundle["login_url"], "https://www.acme.com/login/")
        self.assertEqual(bundle["editor_url"], "https://www.acme.com/dashboard/")

    # --- unverified rows and the stale legacy field are ignored ------------ #

    def test_unverified_rows_are_ignored_even_if_legacy_field_names_them(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.pending.com", is_verified=False
        )
        self.tenant.custom_domain = "www.pending.com"
        self.tenant.save(update_fields=["custom_domain"])
        self.assertEqual(
            tenant_public_url(self.request, self.tenant),
            "https://acme.sites.example.test/",
        )
        self.assertEqual(
            tenant_canonical_public_url(self.tenant),
            "https://acme.sites.example.test/",
        )
        bundle = build_tenant_url_bundle(self.request, self.tenant)
        self.assertFalse(bundle["has_custom_domain"])

    # --- ordering ---------------------------------------------------------- #

    def test_earliest_verified_domain_wins_when_several_exist(self):
        newer = CustomDomain.objects.create(
            tenant=self.tenant, domain="newer.example.com", is_verified=True
        )
        older = CustomDomain.objects.create(
            tenant=self.tenant, domain="older.example.com", is_verified=True
        )
        pending_oldest = CustomDomain.objects.create(
            tenant=self.tenant, domain="oldest-pending.example.com", is_verified=False
        )
        _backdate(newer, 1)
        _backdate(older, 5)
        _backdate(pending_oldest, 10)
        self.assertEqual(
            tenant_primary_custom_domain(self.tenant), "older.example.com"
        )
        self.assertEqual(
            tenant_public_url(self.request, self.tenant), "https://older.example.com/"
        )
        self.assertEqual(
            tenant_canonical_public_url(self.tenant), "https://older.example.com/"
        )

    # --- fallbacks --------------------------------------------------------- #

    def test_no_rows_falls_back_to_base_domain(self):
        self.assertEqual(tenant_primary_custom_domain(self.tenant), "")
        self.assertEqual(
            tenant_public_url(self.request, self.tenant),
            "https://acme.sites.example.test/",
        )
        self.assertEqual(
            tenant_canonical_public_url(self.tenant),
            "https://acme.sites.example.test/",
        )
        self.assertEqual(
            tenant_login_url(self.request, self.tenant),
            "https://acme.sites.example.test/login/",
        )

    def test_local_dev_request_path_is_unchanged_by_verified_domain(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        local = RequestFactory(HTTP_HOST="lvh.me:8000").get("/dashboard/")
        self.assertEqual(
            tenant_public_url(local, self.tenant), "http://acme.lvh.me:8000/"
        )
        localhost = RequestFactory(HTTP_HOST="localhost:8000").get("/dashboard/")
        self.assertEqual(
            tenant_public_url(localhost, self.tenant), "http://acme.localhost:8000/"
        )

    # --- query cost -------------------------------------------------------- #

    def test_uses_prefetched_custom_domains_without_extra_query(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.pending.com", is_verified=False
        )
        tenant = Tenant.objects.prefetch_related(
            Prefetch(
                "custom_domains",
                queryset=CustomDomain.objects.order_by("created_at", "pk"),
            )
        ).get(pk=self.tenant.pk)
        with self.assertNumQueries(0):
            self.assertEqual(
                tenant_public_url(self.request, tenant), "https://www.acme.com/"
            )
            self.assertEqual(
                tenant_canonical_public_url(tenant), "https://www.acme.com/"
            )

    def test_unprefetched_lookup_costs_one_query_per_call(self):
        CustomDomain.objects.create(
            tenant=self.tenant, domain="www.acme.com", is_verified=True
        )
        with self.assertNumQueries(1):
            self.assertEqual(
                tenant_public_url(self.request, self.tenant), "https://www.acme.com/"
            )


@override_settings(TENANT_BASE_DOMAIN="sites.example.test")
class TenantListQueryCostTests(TestCase):
    """The agency sites list calls ``tenant_public_url`` per row; the
    custom-domain lookup must come from a prefetch, not one query per tenant."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _template()

    def _make_tenants(self, n):
        start = Tenant.objects.count()
        for i in range(start, start + n):
            tenant = Tenant.objects.create(
                name=f"T{i}", subdomain=f"t{i}", template=self.template,
                owner=self.staff,
            )
            CustomDomain.objects.create(
                tenant=tenant, domain=f"t{i}.example.com", is_verified=True
            )

    def _query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        c = Client(HTTP_HOST="sites.example.test")
        c.force_login(self.staff)
        with CaptureQueriesContext(connection) as ctx:
            resp = c.get(reverse("dashboard:tenant_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "https://t0.example.com/")
        return len(ctx.captured_queries)

    def test_sites_list_query_count_does_not_grow_with_tenant_count(self):
        self._make_tenants(2)
        with_two = self._query_count()
        self._make_tenants(6)
        with_eight = self._query_count()
        self.assertEqual(with_two, with_eight)


@override_settings(TENANT_BASE_DOMAIN="sites.example.test")
class PageRowUrlQueryCostTests(TestCase):
    """Per-page "live" links on the agency page list and tenant detail share
    one tenant; the custom-domain lookup must not repeat per page row."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=cls.template, owner=cls.staff,
        )
        CustomDomain.objects.create(
            tenant=cls.tenant, domain="www.acme.com", is_verified=True
        )

    def _add_pages(self, n):
        from core.models import Page

        start = Page.objects.count()
        for i in range(start, start + n):
            Page.objects.create(
                tenant=self.tenant, template=_template(f"P{i}"),
                title=f"Page {i}", slug=f"page-{i}", is_published=True,
            )

    def _query_count(self, url_name):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        c = Client(HTTP_HOST="sites.example.test")
        c.force_login(self.staff)
        with CaptureQueriesContext(connection) as ctx:
            resp = c.get(reverse(url_name, args=[self.tenant.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "https://www.acme.com/page-")
        # Only the custom-domain lookups: page_list has unrelated per-page
        # queries of its own, which are not what this test pins.
        return sum(
            1 for q in ctx.captured_queries if "core_customdomain" in q["sql"]
        )

    def test_page_list_query_count_does_not_grow_with_page_count(self):
        self._add_pages(2)
        with_two = self._query_count("dashboard:page_list")
        self._add_pages(6)
        with_eight = self._query_count("dashboard:page_list")
        self.assertEqual(with_two, with_eight)

    def test_tenant_detail_query_count_does_not_grow_with_page_count(self):
        self._add_pages(2)
        with_two = self._query_count("dashboard:tenant_detail")
        self._add_pages(6)
        with_eight = self._query_count("dashboard:tenant_detail")
        self.assertEqual(with_two, with_eight)
