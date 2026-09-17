"""Slashless application routes must redirect, not fall into the page catch-all.

PR #55 made tenant pages canonical at `/<slug>` (no trailing slash). Since
then `/login`, `/dashboard`, `/blog` and friends without a slash resolved to
`page_render(slug=...)`, found no page, and answered 404; Django's
APPEND_SLASH redirect never ran because the path was "valid". Shared links
like `sites.katek.app/login` broke for 11 days (CMS-64, 2026-09-18).
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from core.models import Page, Template, Tenant


BASE = "sites.katek.app"
HERO = (
    "<section data-section='hero'>"
    "<h1 data-edit='hero.title' data-type='text'>{}</h1></section>"
)


@override_settings(TENANT_BASE_DOMAIN=BASE, DEBUG=False, ALLOWED_HOSTS=["*"])
class SlashlessApplicationRouteTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("op", is_staff=True)
        tpl = Template.objects.create(name="Home", html_source=HERO.format("Home"))
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=tpl, owner=self.owner, is_published=True
        )
        Page.objects.create(
            tenant=self.tenant, template=tpl, title="About", slug="about", is_published=True
        )
        self.host = f"acme.{BASE}"
        self.client = Client()

    def test_login_without_slash_redirects_to_the_login_route(self):
        r = self.client.get("/login", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "/login/")

    def test_query_string_survives_the_redirect(self):
        r = self.client.get("/login?next=/dashboard/", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "/login/?next=/dashboard/")

    def test_dashboard_and_blog_without_slash_redirect_too(self):
        for path in ("/dashboard", "/blog"):
            r = self.client.get(path, HTTP_HOST=self.host)
            self.assertEqual(r.status_code, 301, path)
            self.assertEqual(r["Location"], path + "/", path)

    def test_agency_host_login_without_slash_redirects(self):
        r = self.client.get("/login", HTTP_HOST=BASE)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "/login/")

    def test_real_tenant_page_still_renders_canonically_without_slash(self):
        r = self.client.get("/about", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 200)

    def test_unknown_slug_stays_a_real_404_not_a_redirect(self):
        r = self.client.get("/no-such-page", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 404)

    def test_tenant_page_named_like_an_app_route_still_yields_to_the_app_route(self):
        # `/login/` is an application route; a page slug "login" cannot shadow
        # it, so the slashless form must go to the app route as well.
        Page.objects.create(
            tenant=self.tenant, template=self.tenant.template, title="Login", slug="login", is_published=True
        )
        r = self.client.get("/login", HTTP_HOST=self.host)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r["Location"], "/login/")
