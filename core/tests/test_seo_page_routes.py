from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from core.models import Page, Template, Tenant


STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    TENANT_BASE_DOMAIN="sites.example.test",
    ALLOWED_HOSTS=["*"],
    STORAGES=STATIC,
)
class CanonicalTenantPageRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = get_user_model().objects.create_user("owner", password="x")
        home = Template.objects.create(name="Home", html_source="<h1>Home</h1>")
        cls.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=home,
            owner=owner,
            is_published=True,
        )
        page_template = Template.objects.create(
            name="About",
            html_source=(
                "<section data-section='hero'><h1 data-edit='hero.title' "
                "data-type='text'>About marker</h1></section>"
            ),
        )
        Page.objects.create(
            tenant=cls.tenant,
            template=page_template,
            title="About",
            slug="about",
            is_published=True,
        )
        draft_template = Template.objects.create(
            name="Draft", html_source="<h1>Draft</h1>"
        )
        Page.objects.create(
            tenant=cls.tenant,
            template=draft_template,
            title="Draft",
            slug="draft",
            is_published=False,
        )

    def setUp(self):
        self.client = Client(HTTP_HOST="acme.sites.example.test")

    def test_extensionless_page_is_canonical_response(self):
        response = self.client.get("/about")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "About marker")

    def test_trailing_slash_redirects_permanently_to_extensionless_page(self):
        response = self.client.get("/about/", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/about")

    def test_html_alias_redirects_permanently_to_extensionless_page(self):
        response = self.client.get("/about.html", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/about")

    def test_home_html_alias_redirects_permanently_to_root(self):
        response = self.client.get("/index.html", follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/")

    def test_redirect_aliases_preserve_query_string(self):
        for path, expected in (
            ("/about/?utm_source=test", "/about?utm_source=test"),
            ("/about.html?utm_source=test", "/about?utm_source=test"),
            ("/index.html?utm_source=test", "/?utm_source=test"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], expected)

    def test_unknown_and_unpublished_aliases_return_404(self):
        for path in (
            "/missing",
            "/missing/",
            "/missing.html",
            "/draft",
            "/draft/",
            "/draft.html",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, follow=False).status_code, 404)

    def test_tenant_legal_page_slash_aliases_redirect_to_canonical_paths(self):
        for slug in ("privacy", "terms"):
            template = Template.objects.create(
                name=slug.title(), html_source=f"<h1>{slug} marker</h1>"
            )
            Page.objects.create(
                tenant=self.tenant,
                template=template,
                title=slug.title(),
                slug=slug,
                is_published=True,
            )

            response = self.client.get(f"/{slug}/", follow=False)

            self.assertEqual(response.status_code, 301)
            self.assertEqual(response["Location"], f"/{slug}")
