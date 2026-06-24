from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import EmbeddableAssistant, Template, Tenant, TenantMembership


User = get_user_model()


def _make_template(name="Bare", desc=""):
    return Template.objects.create(
        name=name,
        description=desc,
        html_source="<section data-section='hero' data-label='Hero'></section>",
    )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class AgencyAdminAccessTests(TestCase):
    """Step 9 / case 1 — non-staff cannot reach agency dashboard URLs."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.outsider = User.objects.create_user("eve", password="x")
        cls.template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=cls.template, owner=cls.staff,
        )

    def _client(self):
        return Client(HTTP_HOST="localhost")

    def test_anonymous_redirects_to_login(self):
        c = self._client()
        for url in [
            reverse("dashboard:root"),
            reverse("dashboard:tenant_list"),
            reverse("dashboard:tenant_create"),
            reverse("dashboard:user_list"),
            reverse("dashboard:tenant_detail", args=[self.tenant.pk]),
            reverse("dashboard:check_subdomain"),
        ]:
            response = c.get(url)
            self.assertEqual(response.status_code, 302, msg=f"failed for {url}")
            self.assertIn(reverse("login"), response["Location"])

    def test_non_staff_gets_403(self):
        c = self._client()
        c.force_login(self.outsider)
        for url in [
            reverse("dashboard:root"),
            reverse("dashboard:tenant_list"),
            reverse("dashboard:user_list"),
            reverse("dashboard:tenant_detail", args=[self.tenant.pk]),
        ]:
            response = c.get(url)
            self.assertEqual(response.status_code, 403, msg=f"failed for {url}")


@override_settings(TENANT_BASE_DOMAIN="localhost")
class AgencyHomeStatsTests(TestCase):
    """Step 9 / case 2 — staff sees stat cards on home."""

    def test_home_renders_with_stats(self):
        staff = User.objects.create_user("agency", password="x", is_staff=True)
        template = _make_template()
        Tenant.objects.create(name="Acme", subdomain="acme", template=template, owner=staff, is_published=True)
        Tenant.objects.create(name="Beta", subdomain="beta", template=template, owner=staff, is_published=False)

        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        response = c.get(reverse("dashboard:root"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/home.html")
        self.assertContains(response, "Total sites")
        self.assertContains(response, "Templates")
        self.assertContains(response, "Edited this week")
        # 2 sites, 1 published, 1 draft
        stats = response.context["stats"]
        self.assertEqual(stats["total_sites"], 2)
        self.assertEqual(stats["published_sites"], 1)
        self.assertEqual(stats["draft_sites"], 1)
        self.assertEqual(stats["total_templates"], 1)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class NewClientFlowTests(TestCase):
    """Step 9 / case 3 + 4 — new client flow + atomic rollback."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.template = _make_template()

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def test_valid_post_creates_user_tenant_and_membership(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_create"),
            data={
                "name": "Bella's",
                "subdomain": "bellas",
                "template": str(self.template.pk),
                "custom_domain": "",
                "client_username": "alice",
                "client_email": "alice@example.com",
            },
        )
        # Redirect to the post-create site_created page.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/created/", response["Location"])
        self.assertIn("token=", response["Location"])

        # Side-effects landed.
        self.assertTrue(User.objects.filter(username="alice").exists())
        tenant = Tenant.objects.get(subdomain="bellas")
        self.assertEqual(tenant.name, "Bella's")
        self.assertEqual(tenant.owner.username, "alice")
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=tenant,
                user__username="alice",
                role=TenantMembership.ROLE_OWNER,
            ).exists()
        )

    def test_blank_subdomain_is_auto_generated_from_name(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_create"),
            data={
                "name": "Bella's Restaurant",
                "subdomain": "",
                "template": str(self.template.pk),
                "custom_domain": "",
                "client_username": "alice_auto",
                "client_email": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(owner__username="alice_auto")
        self.assertEqual(tenant.subdomain, "bellas-restaurant")

    def test_blank_subdomain_auto_generation_adds_numeric_suffix_on_collision(self):
        Tenant.objects.create(
            name="Existing Bella",
            subdomain="bellas-restaurant",
            template=self.template,
            owner=self.staff,
        )
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_create"),
            data={
                "name": "Bella's Restaurant",
                "subdomain": "",
                "template": str(self.template.pk),
                "custom_domain": "",
                "client_username": "alice_auto_2",
                "client_email": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        tenant = Tenant.objects.get(owner__username="alice_auto_2")
        self.assertEqual(tenant.subdomain, "bellas-restaurant-1")

    def test_credentials_visible_once_then_expired(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_create"),
            data={
                "name": "Bella's", "subdomain": "bellas",
                "template": str(self.template.pk),
                "custom_domain": "",
                "client_username": "alice", "client_email": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        creds_url = response["Location"]

        # First view shows the password.
        first = c.get(creds_url)
        self.assertEqual(first.status_code, 200)
        self.assertTemplateUsed(first, "dashboard/site_created.html")
        self.assertIsNotNone(first.context["payload"])
        password = first.context["payload"]["password"]
        self.assertEqual(len(password), 16)
        self.assertContains(first, password)

        # Confirm the password actually authenticates the new user.
        alice = User.objects.get(username="alice")
        self.assertTrue(alice.check_password(password))

        # Second view: expired/already-viewed.
        second = c.get(creds_url)
        self.assertEqual(second.status_code, 200)
        self.assertIsNone(second.context["payload"])
        self.assertContains(second, "no longer available")

    def test_taken_subdomain_rolls_back_user_creation(self):
        # Pre-existing tenant with subdomain "bellas".
        Tenant.objects.create(
            name="Existing", subdomain="bellas",
            template=self.template, owner=self.staff,
        )
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_create"),
            data={
                "name": "Bella's", "subdomain": "bellas",
                "template": str(self.template.pk),
                "custom_domain": "",
                "client_username": "alice", "client_email": "",
            },
        )
        self.assertEqual(response.status_code, 400)
        # No new user, no second tenant.
        self.assertFalse(User.objects.filter(username="alice").exists())
        self.assertEqual(Tenant.objects.filter(subdomain="bellas").count(), 1)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class CheckSubdomainEndpointTests(TestCase):
    """Step 9 / case 5 — subdomain availability JSON endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        template = _make_template()
        Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.staff,
        )

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def test_taken(self):
        r = self._client().get(reverse("dashboard:check_subdomain") + "?value=acme")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"available": False, "reason": "taken"})

    def test_reserved(self):
        r = self._client().get(reverse("dashboard:check_subdomain") + "?value=admin")
        self.assertEqual(r.json(), {"available": False, "reason": "reserved"})

    def test_invalid(self):
        for bad in ["UPPER", "with space", "-leadingdash", "trailing-", "x_y"]:
            r = self._client().get(reverse("dashboard:check_subdomain") + f"?value={bad}")
            self.assertEqual(
                r.json(),
                {"available": False, "reason": "invalid"},
                msg=f"failed for value={bad!r}",
            )

    def test_available(self):
        r = self._client().get(reverse("dashboard:check_subdomain") + "?value=newco")
        self.assertEqual(r.json(), {"available": True})


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ResetPasswordTests(TestCase):
    """Step 9 / case 6 — reset password sets a new password and shows it once."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.client_user = User.objects.create_user("alice", password="oldpassword123")

    def test_reset_password_replaces_password(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        response = c.post(
            reverse("dashboard:user_reset_password", args=[self.client_user.pk])
        )
        self.assertEqual(response.status_code, 302)
        creds_url = response["Location"]
        self.assertIn("/credentials/", creds_url)

        first = c.get(creds_url)
        self.assertIsNotNone(first.context["payload"])
        new_password = first.context["payload"]["password"]
        self.assertEqual(len(new_password), 16)

        self.client_user.refresh_from_db()
        self.assertTrue(self.client_user.check_password(new_password))
        self.assertFalse(self.client_user.check_password("oldpassword123"))


@override_settings(TENANT_BASE_DOMAIN="localhost")
class MembershipManagementTests(TestCase):
    """Step 9 / case 7 — add and remove members from a site."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.alice = User.objects.create_user("alice", password="x")
        cls.bob = User.objects.create_user("bob", password="x")
        template = _make_template()
        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=cls.staff,
        )

    def _client(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        return c

    def test_add_existing_user_creates_membership(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_member_add", args=[self.tenant.pk]),
            data={"user_id": str(self.alice.pk), "role": "editor"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=self.tenant, user=self.alice, role="editor"
            ).exists()
        )

    def test_remove_member_deletes_membership(self):
        membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.bob, role="editor"
        )
        c = self._client()
        response = c.post(
            reverse(
                "dashboard:tenant_member_remove",
                args=[self.tenant.pk, membership.pk],
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            TenantMembership.objects.filter(pk=membership.pk).exists()
        )


@override_settings(TENANT_BASE_DOMAIN="localhost")
class DeleteSiteTests(TestCase):
    """Step 9 / case 8 — delete site requires typing the subdomain."""

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

    def test_wrong_subdomain_does_not_delete(self):
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_delete", args=[self.tenant.pk]),
            data={"confirm_subdomain": "wrong"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Tenant.objects.filter(pk=self.tenant.pk).exists())

    def test_correct_subdomain_deletes(self):
        tenant_pk = self.tenant.pk
        c = self._client()
        response = c.post(
            reverse("dashboard:tenant_delete", args=[tenant_pk]),
            data={"confirm_subdomain": "acme"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tenant.objects.filter(pk=tenant_pk).exists())


@override_settings(TENANT_BASE_DOMAIN="localhost")
class AssistantDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)
        cls.outsider = User.objects.create_user("eve", password="x")
        cls.assistant = EmbeddableAssistant.objects.create(
            name="Acme Sales Bot",
            slug="acme-sales",
            brand="Acme",
            greeting="Hi, ask me anything about Acme.",
            suggestions="Pricing|Book a demo|Talk to support",
            is_active=True,
        )

    def _agency_client(self, *, login_as_staff=True):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff if login_as_staff else self.outsider)
        return c

    def test_staff_can_open_assistant_list(self):
        response = self._agency_client().get(reverse("dashboard:assistant_list"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/assistant_list.html")
        self.assertContains(response, "AI Assistants")
        self.assertContains(response, self.assistant.name)

    def test_non_staff_cannot_open_assistant_list(self):
        response = self._agency_client(login_as_staff=False).get(
            reverse("dashboard:assistant_list")
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_can_create_assistant(self):
        response = self._agency_client().post(
            reverse("dashboard:assistant_create"),
            data={
                "name": "Support Bot",
                "slug": "support-bot",
                "brand": "Support",
                "brand_full": "Support AI",
                "greeting": "Hello there",
                "suggestions": "Help|Onboarding",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(EmbeddableAssistant.objects.filter(slug="support-bot").exists())

    def test_embed_routes_work_for_active_assistant(self):
        c = Client(HTTP_HOST="localhost")
        frame = c.get(reverse("embed_assistant_frame", args=[self.assistant.slug]))
        self.assertEqual(frame.status_code, 200)
        self.assertContains(frame, "ask me anything")

        chat = c.post(
            reverse("embed_assistant_chat", args=[self.assistant.slug]),
            data='{"message": "How much does it cost?"}',
            content_type="application/json",
        )
        self.assertEqual(chat.status_code, 200)
        self.assertTrue(chat.json()["success"])
        self.assertIn("pricing", chat.json()["reply"].lower())
