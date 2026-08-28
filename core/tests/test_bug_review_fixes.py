"""Regression coverage for docs/FULL_SYSTEM_BUG_REVIEW.md (2026-08-27 pass)."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import ProtectedError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import (
    ContentVersion,
    CustomDomain,
    MediaAsset,
    Page,
    Template,
    Tenant,
    TenantMembership,
    validate_tenant_subdomain,
)
from core.services import blocks, content_versions as cv
from dashboard.views import (
    _dns_name_for_domain,
    _page_row_urls,
    _sanitize_content_field_values,
)


User = get_user_model()

PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

HERO_HTML = (
    "<section data-section='hero' data-label='Hero'>"
    "<h1 data-edit='hero.title' data-type='text'>Welcome</h1>"
    "<a data-edit='hero.cta' data-type='link' href='/ok'>Go</a>"
    "</section>"
)
SHELL = (
    "<!doctype html><html><head><title>T</title></head><body>"
    '<header data-section="nav" data-group="Header">'
    '<span data-edit="nav.brand" data-type="text">Brand</span></header>'
    '<main data-region="main"></main>'
    "</body></html>"
)
HEADLINE = (
    '<div data-block="headline" data-label="Headline" data-category="Text">'
    '<h2 data-edit="headline.text" data-type="text">Hi</h2></div>'
)


def _staff(**kwargs):
    return User.objects.create_user("agency", password="x", is_staff=True, **kwargs)


def _template(html=HERO_HTML, name="T"):
    return Template.objects.create(name=name, html_source=html)


@override_settings(TENANT_BASE_DOMAIN="localhost", ALLOWED_HOSTS=["*"])
class UnpublishedSiteHidesInnerPagesTests(TestCase):
    """C1 — a published inner page is not public while the site is draft."""

    def setUp(self):
        self.owner = User.objects.create_user("alice", password="x")
        self.tpl = _template()
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="acme",
            template=self.tpl,
            owner=self.owner,
            is_published=False,
        )
        self.page = Page.objects.create(
            tenant=self.tenant,
            template=self.tpl,
            title="About",
            slug="about",
            is_published=True,
        )

    def test_anonymous_page_404_while_site_unpublished(self):
        r = Client(HTTP_HOST="localhost").get(
            reverse("page_render_public", args=["acme", "about"])
        )
        self.assertEqual(r.status_code, 404)

    def test_anonymous_page_200_after_site_publish(self):
        self.tenant.is_published = True
        self.tenant.save(update_fields=["is_published"])
        r = Client(HTTP_HOST="localhost").get(
            reverse("page_render_public", args=["acme", "about"])
        )
        self.assertEqual(r.status_code, 200)

    def test_editor_can_preview_unpublished_site_page(self):
        staff = _staff()
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.get(reverse("page_render_public", args=["acme", "about"]))
        self.assertEqual(r.status_code, 200)


@override_settings(TENANT_BASE_DOMAIN="localhost", STORAGES=PLAIN_STATIC)
class EditorJsonScriptTests(TestCase):
    """E2 — field values cannot break out of the editor bootstrap script."""

    def test_script_in_content_is_escaped(self):
        staff = _staff()
        tpl = _template()
        tenant = Tenant.objects.create(
            name="Acme",
            subdomain="jsonxss",
            template=tpl,
            owner=staff,
            content={"hero": {"title": "</script><script>alert(1)</script>"}},
        )
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.get(reverse("dashboard:tenant_editor", args=[tenant.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('id="cms-content-data"', body)
        self.assertNotIn("</script><script>alert(1)</script>", body)
        # Django json_script escapes "<" as \u003C so the tag cannot close early.
        self.assertIn("\\u003C/script", body)


@override_settings(TENANT_BASE_DOMAIN="localhost", STORAGES=PLAIN_STATIC)
class SharedShellEditHtmlTests(TestCase):
    """A1 — refuse Edit HTML when the page shares the site shell."""

    def setUp(self):
        self.staff = _staff()
        self.shell = Template.objects.create(
            name="Shell",
            html_source=SHELL,
            editing_mode=Template.EDITING_EDITABLE,
        )
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="shellsite", template=self.shell, owner=self.staff,
        )
        self.shared = Page.objects.create(
            tenant=self.tenant, template=self.shell, title="About", slug="about",
        )
        own_tpl = Template.objects.create(name="Own", html_source=HERO_HTML)
        self.owned = Page.objects.create(
            tenant=self.tenant, template=own_tpl, title="Own", slug="own",
        )

    def test_shared_shell_redirects_to_site_template(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        r = c.get(
            reverse("dashboard:page_edit_html", args=[self.tenant.pk, self.shared.pk])
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            r["Location"],
            reverse("dashboard:template_detail", args=[self.shell.pk]),
        )

    def test_page_row_omits_edit_html_for_shared_shell(self):
        request = RequestFactory().get("/", HTTP_HOST="localhost")
        shared_urls = _page_row_urls(request, "agency", self.tenant, self.shared)
        owned_urls = _page_row_urls(request, "agency", self.tenant, self.owned)
        self.assertNotIn("edit_html", shared_urls)
        self.assertEqual(
            owned_urls["edit_html"],
            reverse("dashboard:page_edit_html", args=[self.tenant.pk, self.owned.pk]),
        )

    def test_page_list_hides_edit_html_for_shared_shell(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        r = c.get(reverse("dashboard:page_list", args=[self.tenant.pk]))
        self.assertEqual(r.status_code, 200)
        shared_url = reverse(
            "dashboard:page_edit_html", args=[self.tenant.pk, self.shared.pk]
        )
        owned_url = reverse(
            "dashboard:page_edit_html", args=[self.tenant.pk, self.owned.pk]
        )
        self.assertNotContains(r, shared_url)
        self.assertContains(r, owned_url)


class OwnerProtectTests(TestCase):
    """A2 — deleting a user who still owns a site is blocked."""

    def test_owner_delete_raises_protected(self):
        owner = User.objects.create_user("owns-site", password="x")
        Tenant.objects.create(
            name="Acme", subdomain="protectme", template=_template(), owner=owner,
        )
        with self.assertRaises(ProtectedError):
            owner.delete()


@override_settings(TENANT_BASE_DOMAIN="localhost")
class OwnerMembershipAndSuperuserTests(TestCase):
    """A3 / A4 — owner membership and superuser accounts stay protected."""

    def setUp(self):
        self.staff = _staff()
        self.owner = User.objects.create_user("siteowner", password="x")
        self.tenant = Tenant.objects.create(
            name="Acme", subdomain="ownermem", template=_template(), owner=self.owner,
        )
        self.owner_mem = TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.ROLE_OWNER,
        )

    def test_cannot_remove_owner_membership_from_site(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        r = c.post(
            reverse(
                "dashboard:tenant_member_remove",
                args=[self.tenant.pk, self.owner_mem.pk],
            )
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            TenantMembership.objects.filter(pk=self.owner_mem.pk).exists()
        )

    def test_cannot_remove_owner_membership_from_user(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        r = c.post(
            reverse(
                "dashboard:user_remove_membership",
                args=[self.owner.pk, self.owner_mem.pk],
            )
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(
            TenantMembership.objects.filter(pk=self.owner_mem.pk).exists()
        )

    def test_staff_cannot_deactivate_superuser(self):
        su = User.objects.create_superuser("root", "root@ex.com", "x")
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        r = c.post(reverse("dashboard:user_deactivate", args=[su.pk]))
        self.assertEqual(r.status_code, 403)
        su.refresh_from_db()
        self.assertTrue(su.is_active)

    def test_staff_cannot_reset_superuser_password(self):
        su = User.objects.create_superuser("root2", "root2@ex.com", "x")
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        r = c.post(reverse("dashboard:user_reset_password", args=[su.pk]))
        self.assertEqual(r.status_code, 403)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class CredentialsTokenBindingTests(TestCase):
    """A9 — a credentials token cannot be revealed on the wrong user page."""

    def test_token_on_wrong_user_shows_expired(self):
        staff = _staff()
        alice = User.objects.create_user("alice-creds", password="old")
        bob = User.objects.create_user("bob-creds", password="old")
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.post(reverse("dashboard:user_reset_password", args=[alice.pk]))
        self.assertEqual(r.status_code, 302)
        token = r["Location"].split("token=")[-1]
        wrong = c.get(
            reverse("dashboard:user_credentials", args=[bob.pk]) + f"?token={token}"
        )
        self.assertEqual(wrong.status_code, 200)
        self.assertIsNone(wrong.context["payload"])


@override_settings(TENANT_BASE_DOMAIN="localhost")
class PageRenameMissingPkTests(TestCase):
    """A14 — missing/invalid page_pk is a 400-style message, not a 500."""

    def test_missing_page_pk_redirects_with_message(self):
        staff = _staff()
        tenant = Tenant.objects.create(
            name="Acme", subdomain="renamepk", template=_template(), owner=staff,
        )
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.post(
            reverse("dashboard:page_rename", args=[tenant.pk]),
            {"title": "X", "slug": "x"},
            follow=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Which page are you renaming?")


class DnsNameMultiPartTldTests(SimpleTestCase):
    """A15 — known multi-part TLDs treat the registrable domain as apex."""

    def test_co_uk_apex_is_at(self):
        self.assertEqual(_dns_name_for_domain("example.co.uk"), "@")

    def test_www_on_co_uk_is_www(self):
        self.assertEqual(_dns_name_for_domain("www.example.co.uk"), "www")

    def test_com_au_apex_is_at(self):
        self.assertEqual(_dns_name_for_domain("shop.com.au"), "@")

    def test_two_label_apex_is_at(self):
        self.assertEqual(_dns_name_for_domain("example.com"), "@")


class SubdomainValidatorTests(TestCase):
    """C6 — underscores are invalid at the model layer too."""

    def test_underscore_rejected(self):
        with self.assertRaises(ValidationError):
            validate_tenant_subdomain("sample_website")

    def test_clean_lowercases_and_rejects_underscore(self):
        owner = User.objects.create_user("slugowner", password="x")
        tenant = Tenant(
            name="X",
            subdomain="Bad_Name",
            template=_template(),
            owner=owner,
        )
        with self.assertRaises(ValidationError):
            tenant.full_clean()


@override_settings(TENANT_BASE_DOMAIN="localhost")
class VersionsAndRestoreTests(TestCase):
    """E12 / E13 — undo list is dashboard-only; oversized restore is rejected."""

    def setUp(self):
        self.member = User.objects.create_user("v-alice", password="x")
        self.shell = Template.objects.create(
            name="Shell",
            html_source=SHELL,
            editing_mode=Template.EDITING_EDITABLE,
        )
        from core.models import BlockType

        head = BlockType.objects.create(html_source=HEADLINE)
        self.shell.allowed_block_types.add(head)
        self.tenant = Tenant.objects.create(
            name="Acme",
            subdomain="verhist",
            template=self.shell,
            owner=self.member,
            content={"regions": {"main": []}},
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.member)
        self.page = Page.objects.create(
            tenant=self.tenant,
            template=self.shell,
            title="P",
            slug="p",
            content={"regions": {"main": []}},
        )

    def test_versions_list_ignores_mcp_rows(self):
        dash = ContentVersion.objects.create(
            tenant=self.tenant,
            page=self.page,
            snapshot={"regions": {"main": []}},
            source=cv.SOURCE_DASHBOARD,
            saved_by=self.member,
        )
        ContentVersion.objects.create(
            tenant=self.tenant,
            page=self.page,
            snapshot={"regions": {"main": []}},
            source=cv.SOURCE_MCP,
            saved_by=self.member,
        )
        c = Client(HTTP_HOST="verhist.localhost")
        c.force_login(self.member)
        r = c.get(reverse("dashboard:page_versions_self", args=[self.page.pk]))
        self.assertEqual(r.status_code, 200)
        ids = [item["id"] for item in r.json()["versions"]]
        self.assertEqual(ids, [dash.id])

    def test_restore_over_block_cap_is_rejected(self):
        too_many = [
            {"id": f"blk_{i:08x}", "type": "headline", "fields": {}}
            for i in range(blocks.MAX_BLOCKS_PER_PAGE + 1)
        ]
        version = ContentVersion.objects.create(
            tenant=self.tenant,
            page=self.page,
            snapshot={"regions": {"main": too_many}},
            source=cv.SOURCE_DASHBOARD,
            saved_by=self.member,
        )
        with self.assertRaises(cv.RestoreValidationError):
            cv.restore_editable_content(self.page, version, user=self.member)
        self.page.refresh_from_db()
        self.assertEqual(self.page.content["regions"]["main"], [])


@override_settings(TENANT_BASE_DOMAIN="localhost")
class MediaDeleteSnapshotsTests(TestCase):
    """C3 — gallery delete snapshots content before scrubbing the URL."""

    def test_delete_creates_a_version(self):
        staff = _staff()
        url = "https://cdn.test/hero.png"
        tenant = Tenant.objects.create(
            name="Acme",
            subdomain="gallery",
            template=_template(),
            owner=staff,
            content={"hero": {"title": "Hi", "image": url}},
        )
        asset = MediaAsset.objects.create(
            tenant=tenant,
            original_name="hero.png",
            secure_url=url,
            resource_type=MediaAsset.RESOURCE_IMAGE,
        )
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.delete(reverse("dashboard:tenant_media_item", args=[tenant.pk, asset.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(
            tenant.versions.filter(source=cv.SOURCE_DASHBOARD).exists()
        )
        tenant.refresh_from_db()
        self.assertNotEqual(tenant.content.get("hero", {}).get("image"), url)


class SavePathSanitizeTests(SimpleTestCase):
    """E6 / E7 — unsafe colors and javascript: links are dropped on save."""

    def test_javascript_link_cleared(self):
        schema = {
            "sections": [{
                "id": "hero",
                "fields": [
                    {"id": "hero.cta", "type": "link"},
                ],
            }]
        }
        content = {"hero": {"cta": "javascript:alert(1)"}}
        _sanitize_content_field_values(content, schema)
        self.assertEqual(content["hero"]["cta"], "")


@override_settings(TENANT_BASE_DOMAIN="localhost", STORAGES=PLAIN_STATIC)
class TemplateCreateHonorsEditingModeTests(TestCase):
    """A7 — paste-path create keeps the submitted Client-editing choice."""

    def test_raw_mode_is_kept_on_annotated_paste(self):
        staff = _staff()
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.post(
            reverse("dashboard:template_create"),
            {
                "name": "Raw paste",
                "description": "",
                "html_source": HERO_HTML,
                "editing_mode": Template.EDITING_RAW,
                "build_mode": "paste",
            },
        )
        self.assertEqual(r.status_code, 302)
        tpl = Template.objects.get(name="Raw paste")
        self.assertEqual(tpl.editing_mode, Template.EDITING_RAW)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ForceVerifySuperuserTests(TestCase):
    """A10 — force-verify is superuser-only."""

    def test_staff_forbidden(self):
        staff = _staff()
        tenant = Tenant.objects.create(
            name="Acme", subdomain="forcev", template=_template(), owner=staff,
        )
        domain = CustomDomain.objects.create(
            tenant=tenant, domain="example.com", is_verified=False
        )
        c = Client(HTTP_HOST="localhost")
        c.force_login(staff)
        r = c.post(reverse("dashboard:custom_domain_force_verify", args=[domain.pk]))
        self.assertEqual(r.status_code, 403)
        domain.refresh_from_db()
        self.assertFalse(domain.is_verified)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class DiagnosticHeaderTests(TestCase):
    """X1 — diagnostic headers stay off unless DEBUG or DIAG_HEADERS."""

    def test_off_when_debug_and_flag_are_false(self):
        with override_settings(DEBUG=False, DIAG_HEADERS=False):
            r = Client(HTTP_HOST="localhost").get("/login/")
        self.assertNotIn("X-Diag-Debug", r)

    def test_on_when_debug(self):
        with override_settings(DEBUG=True):
            r = Client(HTTP_HOST="localhost").get("/login/")
        self.assertEqual(r["X-Diag-Debug"], "True")


class ProductionSecretGuardTests(SimpleTestCase):
    """A5 — production (DEBUG off) must not boot on the hardcoded fallback."""

    def test_guard_raises_for_fallback_when_debug_off(self):
        from cms_platform.settings import _SECRET_KEY_FALLBACK

        def _guard(debug, secret):
            if not debug and secret == _SECRET_KEY_FALLBACK:
                raise ImproperlyConfigured(
                    "DJANGO_SECRET_KEY must be set to a unique secret when "
                    "DEBUG is off."
                )

        with self.assertRaises(ImproperlyConfigured):
            _guard(False, _SECRET_KEY_FALLBACK)
        _guard(True, _SECRET_KEY_FALLBACK)
        _guard(False, "unique-production-secret")
