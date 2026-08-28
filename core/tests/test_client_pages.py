"""Phase 2 — client page management on a block shell (create/rename/nav/delete)."""
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import BlockType, Page, Template, Tenant, TenantMembership

SHELL = (
    "<!doctype html><html><head><title>T</title></head><body>"
    '<header data-section="nav" data-group="Header">'
    '<span data-edit="nav.brand" data-type="text">Brand</span></header>'
    '<main data-region="main"></main>'
    '<footer data-section="footer" data-group="Footer">'
    '<span data-edit="footer.name" data-type="text">Co</span></footer>'
    "</body></html>"
)
HERO = (
    '<section data-block="hero" data-label="Hero" data-category="Headers">'
    '<h1 data-edit="hero.title" data-type="text">Welcome</h1></section>'
)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ClientPageManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.member = User.objects.create_user("alice", password="x")
        cls.staff = User.objects.create_user("agency", password="x", is_staff=True)

        cls.shell = Template.objects.create(
            name="Shell", html_source=SHELL, editing_mode=Template.EDITING_EDITABLE
        )
        cls.hero = BlockType.objects.create(html_source=HERO)
        cls.shell.allowed_block_types.add(cls.hero)

        cls.classic = Template.objects.create(
            name="Classic",
            html_source="<section data-section='hero'><h1 data-edit='hero.title' data-type='text'>Hi</h1></section>",
            editing_mode=Template.EDITING_EDITABLE,
        )

        cls.tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=cls.shell, owner=cls.staff,
            content={"regions": {"main": [
                {"id": "blk_home", "type": "hero", "fields": {"title": "Home hero"}},
            ]}},
        )
        cls.classic_tenant = Tenant.objects.create(
            name="Beta", subdomain="beta", template=cls.classic, owner=cls.staff,
        )
        TenantMembership.objects.create(tenant=cls.tenant, user=cls.member)

    def _client(self, host="acme.localhost"):
        c = Client(HTTP_HOST=host)
        c.force_login(self.member)
        return c

    def test_client_can_create_blank_page_on_shell(self):
        c = self._client()
        resp = c.post(reverse("dashboard:page_create_self"),
                      {"title": "About", "slug": "about", "start_from": "blank"})
        self.assertEqual(resp.status_code, 302)
        page = self.tenant.pages.get(slug="about")
        # shares the shell + starts with an empty region.
        self.assertEqual(page.template_id, self.shell.pk)
        self.assertEqual(page.content["regions"]["main"], [])

    def test_copy_home_keeps_nested_titles_with_fresh_ids(self):
        # C2: a row → column → headline must survive copy-home, not just the
        # top-level instance (the old shallow dict dropped ``children``).
        row = BlockType.objects.create(
            html_source=(
                '<div data-block="row-2" data-label="2 Column" data-category="Rows">'
                '<div data-region="col1"></div><div data-region="col2"></div></div>'
            )
        )
        self.shell.allowed_block_types.add(row)
        self.tenant.content = {"regions": {"main": [{
            "id": "blk_rowhome",
            "type": "row-2",
            "fields": {},
            "children": {
                "col1": [{"id": "blk_nested", "type": "hero",
                          "fields": {"title": "Nested kept"}}],
                "col2": [],
            },
        }]}}
        self.tenant.save(update_fields=["content"])
        c = self._client()
        c.post(reverse("dashboard:page_create_self"),
               {"title": "Deep", "slug": "deep", "start_from": "copy_home"})
        page = self.tenant.pages.get(slug="deep")
        row_inst = page.content["regions"]["main"][0]
        self.assertEqual(row_inst["type"], "row-2")
        self.assertNotEqual(row_inst["id"], "blk_rowhome")
        child = row_inst["children"]["col1"][0]
        self.assertEqual(child["fields"]["title"], "Nested kept")
        self.assertNotEqual(child["id"], "blk_nested")

    def test_copy_home_clones_blocks_with_fresh_ids(self):
        c = self._client()
        c.post(reverse("dashboard:page_create_self"),
               {"title": "Copy", "slug": "copy", "start_from": "copy_home"})
        page = self.tenant.pages.get(slug="copy")
        main = page.content["regions"]["main"]
        self.assertEqual([b["type"] for b in main], ["hero"])
        self.assertEqual(main[0]["fields"]["title"], "Home hero")
        self.assertNotEqual(main[0]["id"], "blk_home")  # fresh id

    def test_copy_home_copies_header_region(self):
        self.tenant.content = {
            "regions": {
                "header": [{"id": "blk_nav1", "type": "hero", "fields": {"title": "About"}}],
                "main": [{"id": "blk_home", "type": "hero", "fields": {"title": "Home"}}],
            }
        }
        self.tenant.save(update_fields=["content"])
        c = self._client()
        c.post(reverse("dashboard:page_create_self"),
               {"title": "With nav", "slug": "with-nav", "start_from": "copy_home"})
        page = self.tenant.pages.get(slug="with-nav")
        header = page.content["regions"]["header"]
        self.assertEqual(header[0]["fields"]["title"], "About")
        self.assertNotEqual(header[0]["id"], "blk_nav1")

    def test_copy_from_another_page_cross_page_reuse(self):
        src = Page.objects.create(
            tenant=self.tenant, template=self.shell, title="Src", slug="src",
            content={"regions": {"main": [
                {"id": "blk_src", "type": "hero", "fields": {"title": "Src hero"}},
            ]}},
        )
        c = self._client()
        c.post(reverse("dashboard:page_create_self"),
               {"title": "Dest", "slug": "dest", "start_from": f"copy_page:{src.pk}"})
        dest = self.tenant.pages.get(slug="dest")
        main = dest.content["regions"]["main"]
        self.assertEqual(main[0]["fields"]["title"], "Src hero")
        self.assertNotEqual(main[0]["id"], "blk_src")  # fresh id

    def test_client_on_classic_template_is_upgraded_and_can_create(self):
        c = Client(HTTP_HOST="beta.localhost")
        beta_member = get_user_model().objects.create_user("bob", password="x")
        TenantMembership.objects.create(tenant=self.classic_tenant, user=beta_member)
        c.force_login(beta_member)
        resp = c.post(reverse("dashboard:page_create_self"),
                      {"title": "X", "slug": "x"})
        self.assertEqual(resp.status_code, 302)
        self.classic_tenant.refresh_from_db()
        self.assertTrue(self.classic_tenant.template.is_block_shell)
        self.assertTrue(self.classic_tenant.pages.filter(slug="x").exists())

    def test_client_cannot_paste_html(self):
        # A non-staff client's html_source is ignored — they get a shared-shell
        # page, never a pasted-HTML template.
        c = self._client()
        c.post(reverse("dashboard:page_create_self"),
               {"title": "Sneaky", "slug": "sneaky",
                "html_source": "<section data-section='x'></section>"})
        page = self.tenant.pages.get(slug="sneaky")
        self.assertEqual(page.template_id, self.shell.pk)

    def test_agency_can_create_block_page_on_shell(self):
        # Agency surface (host = base domain) also composes from the palette on a
        # block shell — no HTML paste required.
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        resp = c.post(
            reverse("dashboard:page_create", args=[self.tenant.pk]),
            {"title": "About", "slug": "about-agency", "start_from": "blank"},
        )
        self.assertEqual(resp.status_code, 302)
        page = self.tenant.pages.get(slug="about-agency")
        self.assertEqual(page.template_id, self.shell.pk)
        self.assertEqual(page.content["regions"]["main"], [])

    def test_agency_copy_home_on_shell(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        c.post(
            reverse("dashboard:page_create", args=[self.tenant.pk]),
            {"title": "Copy2", "slug": "copy2", "start_from": "copy_home"},
        )
        main = self.tenant.pages.get(slug="copy2").content["regions"]["main"]
        self.assertEqual([b["type"] for b in main], ["hero"])
        self.assertNotEqual(main[0]["id"], "blk_home")  # fresh id

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_agency_page_list_renders_block_card_on_shell(self):
        c = Client(HTTP_HOST="localhost")
        c.force_login(self.staff)
        resp = c.get(reverse("dashboard:page_list", args=[self.tenant.pk]))
        self.assertEqual(resp.status_code, 200)
        # Both paths are offered on a shell: the block "Start from" chooser is
        # primary, and the advanced HTML-paste editor stays available.
        self.assertContains(resp, "Start from")
        self.assertContains(resp, "HTML source")
        self.assertContains(resp, "New page from HTML")

    def test_rename_page(self):
        page = Page.objects.create(
            tenant=self.tenant, template=self.shell, title="Old", slug="old",
            content={"regions": {"main": []}},
        )
        c = self._client()
        c.post(reverse("dashboard:page_rename_self", args=[page.pk]),
               {"title": "New name", "slug": "new-name"})
        page.refresh_from_db()
        self.assertEqual(page.title, "New name")
        self.assertEqual(page.slug, "new-name")

    def test_rename_rejects_duplicate_slug(self):
        Page.objects.create(tenant=self.tenant, template=self.shell, title="A", slug="a")
        page = Page.objects.create(tenant=self.tenant, template=self.shell, title="B", slug="b")
        c = self._client()
        c.post(reverse("dashboard:page_rename_self", args=[page.pk]),
               {"title": "B", "slug": "a"})
        page.refresh_from_db()
        self.assertEqual(page.slug, "b")  # unchanged

    def test_nav_reorder_persists_order_and_visibility(self):
        p1 = Page.objects.create(tenant=self.tenant, template=self.shell, title="P1", slug="p1", nav_order=0)
        p2 = Page.objects.create(tenant=self.tenant, template=self.shell, title="P2", slug="p2", nav_order=1)
        c = self._client()
        resp = c.post(
            reverse("dashboard:page_nav_reorder_self"),
            data=json.dumps({"order": [
                {"id": p2.pk, "show_in_nav": False},
                {"id": p1.pk, "show_in_nav": True},
            ]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        p1.refresh_from_db(); p2.refresh_from_db()
        self.assertEqual(p2.nav_order, 0)
        self.assertFalse(p2.show_in_nav)
        self.assertEqual(p1.nav_order, 1)

    def test_page_cap_enforced(self):
        from core.services import blocks
        for i in range(blocks.MAX_PAGES_PER_TENANT):
            Page.objects.create(tenant=self.tenant, template=self.shell,
                                title=f"P{i}", slug=f"p{i}")
        c = self._client()
        c.post(reverse("dashboard:page_create_self"),
               {"title": "Over", "slug": "over", "start_from": "blank"})
        self.assertFalse(self.tenant.pages.filter(slug="over").exists())

    def test_page_versions_are_scoped_per_page(self):
        page = Page.objects.create(tenant=self.tenant, template=self.shell,
                                   title="Ver", slug="ver", content={"regions": {"main": []}})
        c = self._client()
        c.post(
            reverse("dashboard:page_save_self", args=[page.pk]),
            data=json.dumps({"content": {"regions": {"main": [
                {"id": "blk_x", "type": "hero", "fields": {"title": "Hi"}},
            ]}}}),
            content_type="application/json",
        )
        resp = c.get(reverse("dashboard:page_versions_self", args=[page.pk]))
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["versions"]), 1)

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_blank_page_editor_uses_canvas_first_chrome(self):
        page = Page.objects.create(
            tenant=self.tenant, template=self.shell, title="Blank", slug="blank",
            content={"regions": {"main": []}},
        )
        resp = self._client().get(reverse("dashboard:page_editor_self", args=[page.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "is-block-mode")
        self.assertContains(resp, "Start creating this page")
        self.assertContains(resp, "Select an element to see and edit its properties here.")
        self.assertContains(resp, "Quick Add")
        self.assertContains(resp, "Layers")
        self.assertContains(resp, "cms-shell-regions")
        self.assertContains(resp, "Background")
        self.assertContains(resp, "Image URL")
        self.assertContains(resp, "data-layout-image-option")
        self.assertContains(resp, "data-layout-align")
        self.assertContains(resp, "data-layout-target")
        self.assertContains(resp, "Blur image")
        self.assertContains(resp, "data-block-layout")
        self.assertContains(resp, "data-replace-image")
        self.assertContains(resp, "Replace from gallery")
        self.assertContains(resp, "data-drawer-tab=\"general\"")
        self.assertContains(resp, "data-drawer-tab=\"styles\"")
        self.assertContains(resp, "palette-nav")
        self.assertContains(resp, 'data-jump="nav"')
        self.assertContains(resp, 'data-jump="footer"')
        self.assertContains(resp, 'data-chrome="1"')
        self.assertContains(resp, "data-gallery-upload")

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_classic_site_editor_upgrades_to_block_chrome(self):
        c = Client(HTTP_HOST="beta.localhost")
        c.force_login(self.staff)
        resp = c.get(reverse("dashboard:root"))
        self.assertEqual(resp.status_code, 200)
        self.classic_tenant.refresh_from_db()
        self.assertTrue(self.classic_tenant.template.is_block_shell)
        self.assertContains(resp, "cms-header-panel")
        self.assertContains(resp, "is-block-mode")
        self.assertContains(resp, "editor-tools-menu")
        self.assertContains(resp, "cms-header-panel")
        self.assertContains(resp, "data-header-layout")
        self.assertContains(resp, "data-header-logo-gallery")
        self.assertContains(resp, "data-header-logo-upload")
        self.assertContains(resp, "data-header-logo-size")
        self.assertContains(resp, "data-header-show-name")

    @override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    )
    def test_global_group_nav_chrome_is_in_the_editor(self):
        shell = Template.objects.create(
            name="Global chrome",
            html_source=(
                "<!doctype html><html><body>"
                "<header data-section='nav' data-group='Global' data-label='Navigation'>"
                "<a data-edit='nav.link1' data-type='text' data-label='Services' "
                "href='#about'>Services</a></header>"
                "<main data-region='main'></main>"
                "<footer data-section='footer' data-group='Global' data-label='Footer'>"
                "<a data-edit='footer.go' data-type='link' href='#'>Hi</a>"
                "</footer></body></html>"
            ),
            editing_mode=Template.EDITING_EDITABLE,
        )
        page = Page.objects.create(
            tenant=self.tenant, template=shell, title="Chrome", slug="chrome",
            content={"regions": {"main": []}},
        )
        resp = self._client().get(reverse("dashboard:page_editor_self", args=[page.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Navigation")
        self.assertContains(resp, "Services URL")
        self.assertContains(resp, 'data-jump="nav"')
        self.assertContains(resp, 'data-jump="footer"')
