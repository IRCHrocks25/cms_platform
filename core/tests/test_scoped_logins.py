from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant, TenantMembership


User = get_user_model()


def _make_template(name="Bare"):
    return Template.objects.create(
        name=name,
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class AgencyCreateLoginTests(TestCase):
    """Agency operator mints an extra login for an existing site."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.staff,
        )

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def test_create_login_makes_scoped_nonstaff_user(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_member_create", args=[self.tenant.pk]),
            data={"username": "carol", "email": "carol@example.com", "role": "editor"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("token=", response["Location"])

        carol = User.objects.get(username="carol")
        self.assertFalse(carol.is_staff)
        self.assertFalse(carol.is_superuser)
        self.assertTrue(carol.is_active)
        # Membership only on this one tenant.
        memberships = TenantMembership.objects.filter(user=carol)
        self.assertEqual(memberships.count(), 1)
        self.assertEqual(memberships.first().tenant_id, self.tenant.pk)

    def test_duplicate_username_rejected_no_user_created(self):
        User.objects.create_user("carol", password="x")
        c = self._client()
        before = User.objects.count()
        response = c.post(
            reverse("dashboard:tenant_member_create", args=[self.tenant.pk]),
            data={"username": "carol", "role": "editor"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("#members", response["Location"])
        self.assertEqual(User.objects.count(), before)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class UserPageCreateLoginTests(TestCase):
    """Agency creates an extra login from a client's user-detail page."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.client_user = User.objects.create_user("alice", password="x")
        template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.client_user,
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.client_user)
        # A site the client does NOT belong to.
        cls.other = Tenant.objects.create(
            name="Beta", subdomain="beta", template=template, owner=cls.staff,
        )

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def test_creates_login_on_clients_site(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:user_create_login", args=[self.client_user.pk]),
            data={"tenant_id": str(self.tenant.pk), "username": "ivy", "role": "editor"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("token=", response["Location"])
        ivy = User.objects.get(username="ivy")
        self.assertFalse(ivy.is_staff)
        m = TenantMembership.objects.filter(user=ivy)
        self.assertEqual(m.count(), 1)
        self.assertEqual(m.first().tenant_id, self.tenant.pk)

    def test_rejects_site_client_does_not_belong_to(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:user_create_login", args=[self.client_user.pk]),
            data={"tenant_id": str(self.other.pk), "username": "mallory", "role": "editor"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/users/{self.client_user.pk}/", response["Location"])
        self.assertFalse(User.objects.filter(username="mallory").exists())


@override_settings(TENANT_BASE_DOMAIN="localhost")
class AgencyHostClientLoginRoutingTests(TestCase):
    """A non-staff client logging in on the main/agency host is routed to
    their own site instead of being refused."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="secret", is_staff=True)
        cls.client_user = User.objects.create_user("alice", password="secret")
        cls.orphan = User.objects.create_user("eve", password="secret")
        template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.client_user,
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.client_user)

    def _post_login(self, username):
        c = Client(HTTP_HOST="localhost")
        return c.post(
            reverse("login"), data={"username": username, "password": "secret"}
        )

    def test_client_redirected_to_own_site_login_when_no_shared_cookie(self):
        # No SESSION_COOKIE_DOMAIN in tests → bounce to the tenant host login.
        r = self._post_login("alice")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "http://acme.localhost/login/")

    @override_settings(SESSION_COOKIE_DOMAIN=".localhost")
    def test_client_logged_in_and_sent_to_editor_when_cookie_spans(self):
        r = self._post_login("alice")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "http://acme.localhost/dashboard/")

    def test_staff_still_lands_on_agency_dashboard(self):
        r = self._post_login("agency")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], reverse("dashboard:root"))

    def test_client_with_no_site_is_refused(self):
        r = self._post_login("eve")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], reverse("login"))


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ClientTeamTests(TestCase):
    """Client (tenant host) self-serve Team management."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("alice", password="x")
        cls.editor = User.objects.create_user("dave", password="x")
        cls.outsider = User.objects.create_user("eve", password="x")
        template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.owner,
        )
        cls.owner_m = TenantMembership.objects.create(
            tenant=cls.tenant, user=cls.owner, role=TenantMembership.ROLE_OWNER,
        )
        cls.editor_m = TenantMembership.objects.create(
            tenant=cls.tenant, user=cls.editor, role=TenantMembership.ROLE_EDITOR,
        )

    def _host(self, user=None):
        c = Client(HTTP_HOST="acme.localhost")
        if user:
            c.force_login(user)
        return c

    def test_member_can_open_team_page(self):
        r = self._host(self.editor).get(reverse("dashboard:team_self"))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "dashboard/team.html")

    def test_non_member_blocked_from_team(self):
        r = self._host(self.outsider).get(reverse("dashboard:team_self"))
        self.assertEqual(r.status_code, 403)

    def test_member_creates_scoped_nonstaff_login(self):
        c = self._host(self.editor)
        response = c.post(
            reverse("dashboard:team_member_create_self"),
            data={"username": "frank", "role": "owner"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("token=", response["Location"])

        frank = User.objects.get(username="frank")
        self.assertFalse(frank.is_staff)
        self.assertFalse(frank.is_superuser)
        memberships = TenantMembership.objects.filter(user=frank)
        self.assertEqual(memberships.count(), 1)
        self.assertEqual(memberships.first().tenant_id, self.tenant.pk)
        self.assertEqual(memberships.first().role, TenantMembership.ROLE_OWNER)

    def test_cannot_remove_self(self):
        c = self._host(self.editor)
        response = c.post(
            reverse("dashboard:team_member_remove_self", args=[self.editor_m.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantMembership.objects.filter(pk=self.editor_m.pk).exists())

    def test_cannot_remove_owner(self):
        c = self._host(self.editor)
        response = c.post(
            reverse("dashboard:team_member_remove_self", args=[self.owner_m.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantMembership.objects.filter(pk=self.owner_m.pk).exists())

    def test_can_remove_other_non_owner(self):
        extra = User.objects.create_user("grace", password="x")
        m = TenantMembership.objects.create(
            tenant=self.tenant, user=extra, role=TenantMembership.ROLE_EDITOR,
        )
        c = self._host(self.editor)
        response = c.post(
            reverse("dashboard:team_member_remove_self", args=[m.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantMembership.objects.filter(pk=m.pk).exists())

    def test_cannot_remove_membership_from_another_tenant(self):
        other = Tenant.objects.create(
            name="Beta", subdomain="beta", template=self.tenant.template, owner=self.owner,
        )
        other_user = User.objects.create_user("heidi", password="x")
        other_m = TenantMembership.objects.create(tenant=other, user=other_user)
        c = self._host(self.editor)
        response = c.post(
            reverse("dashboard:team_member_remove_self", args=[other_m.pk])
        )
        # Scoped lookup → 404, membership untouched.
        self.assertEqual(response.status_code, 404)
        self.assertTrue(TenantMembership.objects.filter(pk=other_m.pk).exists())
