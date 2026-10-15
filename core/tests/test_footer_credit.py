"""CMS-86: one tenant may hide its whole footer credit without changing others."""

import json

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import Page, Template, Tenant, TenantMembership
from core.renderer import render_page_from_blocks, render_site
from core.services.blog_render import wrap_in_site_chrome


FOOTER = (
    "<html><head></head><body><footer data-section='footer' data-group='Footer'>"
    "<span class='copyright'>Copyright</span><span class='separator'> | </span>"
    "<span class='credit'>Built by <a data-edit='footer.builtBy' "
    "data-label='Built-by link' data-type='link' href='https://example.test'>"
    "KATEK</a></span></footer></body></html>"
)


class FooterCreditRenderTests(SimpleTestCase):
    def test_default_and_explicit_true_are_normalized_identical(self):
        baseline = BeautifulSoup(render_site(FOOTER, {}), "lxml")
        enabled = BeautifulSoup(
            render_site(FOOTER, {}, site_settings={"show_footer_credit": True}),
            "lxml",
        )
        self.assertEqual(str(baseline), str(enabled))
        self.assertEqual(baseline.select_one(".credit").get_text(" ", strip=True), "Built by KATEK")

    def test_false_removes_whole_wrapped_credit_and_separator(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                soup = BeautifulSoup(
                    render_site(
                        FOOTER, {}, preview=preview,
                        site_settings={"show_footer_credit": False},
                    ),
                    "lxml",
                )
                self.assertIsNone(soup.select_one(".credit"))
                self.assertIsNone(soup.select_one(".separator"))
                self.assertNotIn("Built by", soup.footer.get_text(" ", strip=True))
                self.assertNotIn("KATEK", soup.footer.get_text(" ", strip=True))
                self.assertEqual(soup.select_one(".copyright").get_text(strip=True), "Copyright")

    def test_false_handles_credit_without_dedicated_wrapper(self):
        html = (
            "<footer data-section='footer'><span>Copyright</span> | Built by "
            "<a data-edit='footer.builtBy' data-type='link' href='/'>KATEK</a>"
            "</footer>"
        )
        soup = BeautifulSoup(
            render_site(html, {}, site_settings={"show_footer_credit": False}),
            "lxml",
        )
        self.assertEqual(soup.footer.get_text(" ", strip=True), "Copyright")
        self.assertIsNone(soup.select_one('[data-edit="footer.builtBy"]'))

    def test_false_removes_split_credit_separator_but_keeps_other_item_separator(self):
        for separator in ("|", "·", "-"):
            for preview in (False, True):
                with self.subTest(separator=separator, preview=preview):
                    html = (
                        "<footer data-section='footer'><span>Copyright</span>\n"
                        f"<span class='credit-separator'> {separator} </span>\n"
                        "<span>Built by </span><a data-edit='footer.builtBy' "
                        "data-type='link' href='/'>KATEK</a>"
                        "<span class='privacy-separator'> | </span>"
                        "<a href='/privacy'>Privacy</a></footer>"
                    )
                    soup = BeautifulSoup(
                        render_site(
                            html, {}, preview=preview,
                            site_settings={"show_footer_credit": False},
                        ),
                        "lxml",
                    )
                    self.assertIsNone(soup.select_one(".credit-separator"))
                    self.assertIsNone(soup.select_one('[data-edit="footer.builtBy"]'))
                    self.assertNotIn("Built by", soup.footer.get_text(" ", strip=True))
                    self.assertIsNotNone(soup.select_one(".privacy-separator"))
                    self.assertEqual(soup.footer.get_text(" ", strip=True), "Copyright | Privacy")

    def test_false_removes_split_credit_text_node_separator(self):
        html = (
            "<footer data-section='footer'><span>Copyright</span> | "
            "<span>Built by </span><a data-edit='footer.builtBy' "
            "data-type='link' href='/'>KATEK</a></footer>"
        )
        soup = BeautifulSoup(
            render_site(html, {}, site_settings={"show_footer_credit": False}),
            "lxml",
        )
        self.assertEqual(soup.footer.get_text(" ", strip=True), "Copyright")

    def test_false_removes_separator_with_template_whitespace(self):
        html = FOOTER.replace(
            "</span><span class='separator'> | </span><span class='credit'>",
            "</span>\n  <span class='separator'> | </span>\n  <span class='credit'>",
        )
        soup = BeautifulSoup(
            render_site(html, {}, site_settings={"show_footer_credit": False}),
            "lxml",
        )
        self.assertIsNone(soup.select_one(".separator"))
        self.assertEqual(soup.footer.get_text(" ", strip=True), "Copyright")

    def test_unrelated_footer_link_is_untouched(self):
        html = FOOTER.replace(
            "</footer>",
            "<a data-edit='footer.privacy' data-type='link' href='/privacy'>Privacy</a></footer>",
        )
        soup = BeautifulSoup(
            render_site(html, {}, site_settings={"show_footer_credit": False}),
            "lxml",
        )
        self.assertEqual(soup.select_one('[data-edit="footer.privacy"]').get_text(), "Privacy")

    def test_block_shell_uses_same_site_setting(self):
        shell = FOOTER.replace("<footer", "<main data-region='main'></main><footer", 1)
        html = render_page_from_blocks(
            shell, {"regions": {"main": []}}, {},
            site_settings={"show_footer_credit": False},
        )
        soup = BeautifulSoup(html, "lxml")
        self.assertIsNone(soup.select_one(".credit"))
        self.assertEqual(soup.footer.get_text(" ", strip=True), "Copyright")


@override_settings(
    TENANT_BASE_DOMAIN="localhost",
    ALLOWED_HOSTS=["*"],
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class FooterCreditTenantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("owner", password="x")
        cls.other_owner = User.objects.create_user("other-owner", password="x")
        cls.template = Template.objects.create(
            name="Credit", html_source=FOOTER,
            editing_mode=Template.EDITING_EDITABLE,
        )
        cls.tenant = Tenant.objects.create(
            name="One", subdomain="one", template=cls.template,
            owner=cls.owner, is_published=True,
        )
        cls.other = Tenant.objects.create(
            name="Two", subdomain="two", template=cls.template,
            owner=cls.other_owner, is_published=True,
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.owner)

    def test_editor_exposes_default_checked_site_control(self):
        client = Client(HTTP_HOST="one.localhost")
        client.force_login(self.owner)
        response = client.get(reverse("dashboard:tenant_home"))
        self.assertContains(response, 'data-footer-credit-toggle')
        self.assertContains(response, "Show 'Built by' credit")
        toggle = BeautifulSoup(response.content, "lxml").select_one("#show-footer-credit")
        self.assertEqual(toggle.get("type"), "checkbox")
        self.assertTrue(toggle.has_attr("checked"))
        self.assertFalse(toggle.has_attr("disabled"))
        self.assertEqual(toggle.parent.get("for"), "show-footer-credit")

    def test_patch_persists_per_tenant_and_preserves_other_settings(self):
        self.tenant.site_settings = {"page_title": "One"}
        self.tenant.save(update_fields=["site_settings"])
        client = Client(HTTP_HOST="one.localhost")
        client.force_login(self.owner)
        url = reverse("dashboard:tenant_site_settings_self")
        response = client.patch(
            url, data=json.dumps({"show_footer_credit": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.other.refresh_from_db()
        self.assertIs(self.tenant.site_settings["show_footer_credit"], False)
        self.assertEqual(self.tenant.site_settings["page_title"], "One")
        self.assertEqual(self.other.site_settings, {})
        self.assertNotIn("Built by", Client(HTTP_HOST="one.localhost").get("/").content.decode())
        self.assertIn("Built by", Client(HTTP_HOST="two.localhost").get("/").content.decode())
        self.assertNotIn("Built by", client.get(reverse("dashboard:tenant_preview_self")).content.decode())
        editor = client.get(reverse("dashboard:tenant_home"))
        toggle = BeautifulSoup(editor.content, "lxml").select_one("#show-footer-credit")
        self.assertIsNotNone(toggle)
        self.assertFalse(toggle.has_attr("checked"))

    def test_patch_rejects_non_boolean_and_other_keys(self):
        client = Client(HTTP_HOST="one.localhost")
        client.force_login(self.owner)
        url = reverse("dashboard:tenant_site_settings_self")
        for payload in ({"show_footer_credit": "false"}, {"page_title": "changed"}):
            with self.subTest(payload=payload):
                response = client.patch(url, data=json.dumps(payload), content_type="application/json")
                self.assertEqual(response.status_code, 400)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.site_settings, {})

    def test_setting_covers_inner_page_and_blog_chrome(self):
        self.tenant.site_settings = {"show_footer_credit": False}
        self.tenant.save(update_fields=["site_settings"])
        Page.objects.create(
            tenant=self.tenant, template=self.template,
            title="About", slug="about", is_published=True,
        )
        page_response = Client(HTTP_HOST="one.localhost").get("/about")
        self.assertEqual(page_response.status_code, 200)
        self.assertNotIn("Built by", page_response.content.decode())
        blog_html = wrap_in_site_chrome(self.tenant, "<main>Article</main>")
        self.assertNotIn("Built by", blog_html)
        self.assertIn("Copyright", blog_html)

    def test_hidden_credit_is_preserved_in_block_shell_blog_chrome(self):
        self.template.html_source = FOOTER.replace(
            "<footer", "<main data-region='main'></main><footer", 1,
        )
        self.template.save()
        self.tenant.content = {"regions": {"main": []}}
        self.tenant.site_settings = {"show_footer_credit": False}
        self.tenant.save(update_fields=["content", "site_settings"])

        blog_html = wrap_in_site_chrome(self.tenant, "<main>Article</main>")
        soup = BeautifulSoup(blog_html, "lxml")
        self.assertIsNone(soup.select_one(".credit"))
        self.assertIsNone(soup.select_one(".separator"))
        self.assertEqual(soup.footer.get_text(" ", strip=True), "Copyright")

    def test_regular_settings_save_does_not_reset_hidden_credit(self):
        self.tenant.site_settings = {"show_footer_credit": False}
        self.tenant.save(update_fields=["site_settings"])
        client = Client(HTTP_HOST="one.localhost")
        client.force_login(self.owner)
        response = client.post(
            reverse("dashboard:tenant_site_settings_self"),
            data=json.dumps({"page_title": "Updated"}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertIs(self.tenant.site_settings["show_footer_credit"], False)

    def test_other_tenant_cannot_patch_this_tenants_setting(self):
        client = Client(HTTP_HOST="one.localhost")
        client.force_login(self.other_owner)
        response = client.patch(
            reverse("dashboard:tenant_site_settings_self"),
            data=json.dumps({"show_footer_credit": False}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)


# CMS-90: templates whose credit link is annotated as footer.built_by_link
# (with its footer.built_by_link_label words field) get the same site control.
LINK_FOOTER = (
    "<html><head></head><body><footer data-section='footer' data-group='Footer'>"
    "<p class='copyright'>Copyright</p><nav class='legal'><a href='/terms'>Terms</a></nav>"
    "<span class='credit'>Built by <a class='built' data-edit='footer.built_by_link' "
    "data-label='Built-by link' data-type='link' href='https://katek.co'>KATEK</a></span>"
    "</footer></body></html>"
)


class BuiltByLinkCreditRenderTests(SimpleTestCase):
    def _footer_text(self, html):
        return BeautifulSoup(html, "lxml").footer.get_text(" ", strip=True)

    def test_default_keeps_credit(self):
        self.assertIn("Built by KATEK", self._footer_text(render_site(LINK_FOOTER, {})))

    def test_site_setting_removes_whole_credit(self):
        for preview in (False, True):
            with self.subTest(preview=preview):
                html = render_site(LINK_FOOTER, {}, preview=preview,
                                   site_settings={"show_footer_credit": False})
                text = self._footer_text(html)
                self.assertNotIn("Built by", text)
                self.assertNotIn("KATEK", text)
                self.assertIn("Copyright", text)
                self.assertIn("Terms", text)

    def test_legacy_per_field_hide_removes_whole_credit_publicly(self):
        for hidden in (["footer.built_by_link"], ["footer.built_by_link_label"]):
            with self.subTest(hidden=hidden):
                html = render_site(LINK_FOOTER, {"_hidden": hidden})
                text = self._footer_text(html)
                self.assertNotIn("Built by", text)
                self.assertNotIn("KATEK", text)
                self.assertIn("Copyright", text)

    def test_legacy_per_field_hide_still_editable_in_preview(self):
        html = render_site(LINK_FOOTER, {"_hidden": ["footer.built_by_link"]}, preview=True)
        link = BeautifulSoup(html, "lxml").select_one('[data-edit="footer.built_by_link"]')
        self.assertIsNotNone(link)
        self.assertIn("cms-hidden", link.get("class"))

    def test_unrelated_hidden_field_keeps_credit(self):
        html = render_site(LINK_FOOTER, {"_hidden": ["footer.copyright"]})
        self.assertIn("Built by KATEK", self._footer_text(html))


class BuiltByLinkCreditEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user("link-owner", password="x")
        template = Template.objects.create(
            name="Link credit", html_source=LINK_FOOTER,
            editing_mode=Template.EDITING_EDITABLE,
        )
        cls.tenant = Tenant.objects.create(
            name="Link", subdomain="linkcredit", template=template,
            owner=cls.owner, is_published=True,
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.owner)

    def _editor(self):
        client = Client(HTTP_HOST="linkcredit.localhost")
        client.force_login(self.owner)
        return client.get(reverse("dashboard:tenant_home"))

    def test_editor_shows_site_credit_switch_once(self):
        soup = BeautifulSoup(self._editor().content, "lxml")
        toggles = soup.select("#show-footer-credit")
        self.assertEqual(len(toggles), 1)
        self.assertTrue(toggles[0].has_attr("checked"))
        field = toggles[0].find_parent(class_="field").find_previous_sibling(attrs={"data-field-id": True})
        self.assertIsNotNone(field)

    def test_switch_off_hides_whole_credit_on_public_page(self):
        self.tenant.site_settings = {"show_footer_credit": False}
        self.tenant.save(update_fields=["site_settings"])
        html = Client(HTTP_HOST="linkcredit.localhost").get("/").content.decode()
        text = BeautifulSoup(html, "lxml").footer.get_text(" ", strip=True)
        self.assertNotIn("Built by", text)
        self.assertNotIn("KATEK", text)
