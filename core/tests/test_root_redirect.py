from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant, TenantMembership


User = get_user_model()

PUBLIC_SENTINEL = "Client-editable websites that cannot be broken"
DRAFT_SENTINEL = "PRIVATE_UNPUBLISHED_DRAFT_SENTINEL"
PUBLISHED_SENTINEL = "PUBLIC_PUBLISHED_SITE_SENTINEL"


@override_settings(
    TENANT_BASE_DOMAIN="sites.katek.app",
    ALLOWED_HOSTS=["*"],
)
class RootRedirectTests(TestCase):
    def _tenant(self, *, subdomain, owner, sentinel, is_published=False):
        template = Template.objects.create(
            name=f"{subdomain} template",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text' "
                f"data-label='Title'>{sentinel}</h1>"
                "</section>"
            ),
        )
        return Tenant.objects.create(
            name=subdomain.title(),
            subdomain=subdomain,
            template=template,
            owner=owner,
            is_published=is_published,
        )

    def test_anonymous_base_domain_renders_public_homepage(self):
        response = Client().get("/", HTTP_HOST="sites.katek.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Katek CMS")
        self.assertContains(response, PUBLIC_SENTINEL)

    def test_authenticated_base_domain_redirects_to_dashboard(self):
        user = User.objects.create_user("signed-in", password="secret")
        client = Client()
        client.force_login(user)

        response = client.get("/", HTTP_HOST="sites.katek.app")

        self.assertRedirects(
            response,
            reverse("dashboard:root"),
            fetch_redirect_response=False,
        )

    def test_anonymous_unpublished_tenant_redirects_without_draft_content(self):
        owner = User.objects.create_user("draft-owner", password="secret")
        self._tenant(
            subdomain="draft",
            owner=owner,
            sentinel=DRAFT_SENTINEL,
        )

        response = Client().get("/", HTTP_HOST="draft.sites.katek.app")

        self.assertNotIn(DRAFT_SENTINEL.encode(), response.content)
        self.assertRedirects(
            response,
            reverse("login"),
            fetch_redirect_response=False,
        )

    def test_published_tenant_renders_site(self):
        owner = User.objects.create_user("published-owner", password="secret")
        self._tenant(
            subdomain="published",
            owner=owner,
            sentinel=PUBLISHED_SENTINEL,
            is_published=True,
        )

        response = Client().get("/", HTTP_HOST="published.sites.katek.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, PUBLISHED_SENTINEL)
        self.assertNotContains(response, PUBLIC_SENTINEL)

    def test_editor_renders_own_unpublished_tenant(self):
        owner = User.objects.create_user("editor-owner", password="secret")
        tenant = self._tenant(
            subdomain="editor-draft",
            owner=owner,
            sentinel=DRAFT_SENTINEL,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=owner,
            role=TenantMembership.ROLE_OWNER,
        )
        client = Client()
        client.force_login(owner)

        response = client.get("/", HTTP_HOST="editor-draft.sites.katek.app")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DRAFT_SENTINEL)
        self.assertNotContains(response, PUBLIC_SENTINEL)
