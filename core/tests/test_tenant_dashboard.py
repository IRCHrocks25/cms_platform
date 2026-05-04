from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant, TenantMembership


def _make_template(name="Bare"):
    return Template.objects.create(
        name=name,
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class TenantDashboardAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.User = User

        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.member = User.objects.create_user("alice", password="x")
        cls.outsider = User.objects.create_user("eve", password="x")
        cls.tenant_b_member = User.objects.create_user("bob", password="x")

        template = _make_template()
        cls.tenant_a = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=template,
            owner=cls.staff,
        )
        cls.tenant_b = Tenant.objects.create(
            name="Beta",
            subdomain="beta",
            template=template,
            owner=cls.staff,
        )

        TenantMembership.objects.create(tenant=cls.tenant_a, user=cls.member)
        TenantMembership.objects.create(tenant=cls.tenant_b, user=cls.tenant_b_member)

    def _client(self, host):
        return Client(HTTP_HOST=host)

    # ---------- tenant host ---------- #

    def test_anonymous_on_tenant_host_redirects_to_login(self):
        c = self._client("acme.localhost")
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_non_member_on_tenant_host_gets_403(self):
        c = self._client("acme.localhost")
        c.force_login(self.outsider)
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 403)

    def test_member_on_tenant_host_sees_editor(self):
        c = self._client("acme.localhost")
        c.force_login(self.member)
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acme")
        self.assertTemplateUsed(response, "dashboard/editor.html")

    def test_staff_on_tenant_host_sees_editor(self):
        c = self._client("acme.localhost")
        c.force_login(self.staff)
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/editor.html")

    def test_member_of_a_cannot_access_b(self):
        # alice is a member of tenant A, not tenant B.
        c = self._client("beta.localhost")
        c.force_login(self.member)
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 403)

    # ---------- agency host ---------- #

    def test_anonymous_on_agency_host_redirects_to_login(self):
        c = self._client("localhost")
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_non_staff_on_agency_host_gets_403(self):
        # outsider has no memberships and isn't staff.
        c = self._client("localhost")
        c.force_login(self.outsider)
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 403)

    def test_staff_on_agency_host_sees_agency_home(self):
        c = self._client("localhost")
        c.force_login(self.staff)
        response = c.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/home.html")

    # ---------- other tenant-scoped URLs ---------- #

    def test_tenant_save_self_requires_member(self):
        c = self._client("acme.localhost")
        c.force_login(self.outsider)
        response = c.post(
            reverse("dashboard:tenant_save_self"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_tenant_save_self_works_for_member(self):
        c = self._client("acme.localhost")
        c.force_login(self.member)
        response = c.post(
            reverse("dashboard:tenant_save_self"),
            data='{"content": {"hero.title": "Hello"}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.tenant_a.refresh_from_db()
        self.assertEqual(self.tenant_a.content, {"hero.title": "Hello"})
