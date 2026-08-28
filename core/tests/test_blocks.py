"""Curated block palette — parser, renderer, and migration parity."""
from pathlib import Path

from django.test import TestCase

from core.models import BlockType, Template, Tenant
from core.parser import build_block_schema, build_schema
from core.renderer import merge_with_defaults, render_page_from_blocks, render_site
from core.services import blocks


TESTIMONIAL = (
    '<section data-block="testimonial" data-label="Testimonial" '
    'data-icon="quote" data-category="Social proof">'
    '<blockquote data-edit="testimonial.quote" data-type="richtext" '
    'data-label="Quote">Great food</blockquote>'
    '<p data-edit="testimonial.author" data-type="text">Jane</p>'
    "</section>"
)

SHELL = (
    "<!doctype html><html><head><title>T</title></head><body>"
    '<header data-section="nav" data-group="Header">'
    '<span data-edit="nav.brand" data-type="text">Brand</span></header>'
    '<main data-region="main"></main>'
    '<footer data-section="footer" data-group="Footer">'
    '<span data-edit="footer.name" data-type="text">Co</span></footer>'
    "</body></html>"
)


class BuildBlockSchemaTests(TestCase):
    def test_relative_field_ids_and_metadata(self):
        schema = build_block_schema(TESTIMONIAL)
        self.assertEqual(schema["key"], "testimonial")
        self.assertEqual(schema["label"], "Testimonial")
        self.assertEqual(schema["icon"], "quote")
        self.assertEqual(schema["category"], "Social proof")
        field_ids = [f["id"] for f in schema["fields"]]
        self.assertEqual(field_ids, ["quote", "author"])
        self.assertEqual(schema["defaults"]["author"], "Jane")

    def test_empty_input(self):
        self.assertEqual(build_block_schema("")["fields"], [])
        self.assertEqual(build_block_schema("<div>no block</div>")["fields"], [])

    def test_child_bearing_text_keeps_the_accent_span_in_defaults(self):
        frag = (
            '<section data-section="problem" data-label="The Problem">'
            '<h2 data-edit="problem.title" data-type="text">'
            'Busy isn\'t a strategy. <span class="hl">It\'s a symptom.</span>'
            "</h2></section>"
        )
        schema = build_block_schema(frag)
        title = next(f for f in schema["fields"] if f["id"] == "title")
        self.assertEqual(title["type"], "richtext")
        self.assertIn('class="hl"', title["default"])
        self.assertIn("It's a symptom.", title["default"])


class BlockTypeModelTests(TestCase):
    def test_schema_derived_on_save(self):
        bt = BlockType.objects.create(html_source=TESTIMONIAL)
        self.assertEqual(bt.key, "testimonial")
        self.assertEqual(bt.label, "Testimonial")
        self.assertEqual(bt.schema["defaults"]["author"], "Jane")
        # Re-derived every save, never hand-edited.
        bt.schema = {"tampered": True}
        bt.save()
        bt.refresh_from_db()
        self.assertNotIn("tampered", bt.schema)

    def test_key_placeholder_label_replaced_from_html(self):
        bt = BlockType.objects.create(key="hero", label="hero", html_source=(
            '<section data-section="hero" data-label="Hero" data-group="Home">'
            '<h1 data-edit="hero.title" data-type="text">Hi</h1></section>'
        ))
        self.assertEqual(bt.label, "Hero")
        self.assertEqual(bt.category, "Home")


class RenderPageFromBlocksTests(TestCase):
    def _catalog(self):
        bt = BlockType.objects.create(html_source=TESTIMONIAL)
        return blocks.build_catalog([bt])

    def test_instance_override_and_defaults(self):
        catalog = self._catalog()
        content = {
            "regions": {
                "main": [
                    {"id": "blk_1", "type": "testimonial",
                     "fields": {"author": "Bob"}},
                ]
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("Bob", html)          # overridden field

    def test_empty_instance_field_keeps_designed_default(self):
        """A stored "" must not wipe the block HTML. The editor used to
        re-apply empty form values on preview-ready and delete body copy."""
        catalog = self._catalog()
        content = {
            "regions": {
                "main": [
                    {"id": "blk_1", "type": "testimonial",
                     "fields": {"quote": "", "author": ""}},
                ]
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("Great food", html)
        self.assertIn("Jane", html)

    def test_instance_wrapper_is_the_section_not_an_extra_box(self):
        from bs4 import BeautifulSoup

        catalog = self._catalog()
        content = {
            "regions": {
                "main": [{"id": "blk_1", "type": "testimonial", "fields": {}}],
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        soup = BeautifulSoup(html, "lxml")
        inst = soup.find(attrs={"data-instance-id": "blk_1"})
        self.assertIsNotNone(inst)
        self.assertEqual(inst.name, "section")
        self.assertEqual(inst.parent.get("data-region"), "main")
        self.assertEqual(inst.get("data-block-type"), "testimonial")

    def test_drop_blank_instance_fields_strips_placeholders(self):
        from core.renderer import drop_blank_instance_fields

        content = {
            "regions": {
                "main": [
                    {
                        "id": "blk_1",
                        "type": "testimonial",
                        "fields": {
                            "quote": "<br>",
                            "author": "Bob",
                            "empty": "",
                        },
                    }
                ]
            }
        }
        drop_blank_instance_fields(content)
        self.assertEqual(
            content["regions"]["main"][0]["fields"],
            {"author": "Bob"},
        )

    def test_contenteditable_br_placeholder_keeps_designed_default(self):
        catalog = self._catalog()
        content = {
            "regions": {
                "main": [
                    {"id": "blk_1", "type": "testimonial",
                     "fields": {"quote": "<br>", "author": "<p><br></p>"}},
                ]
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("Great food", html)
        self.assertIn("Jane", html)
        self.assertIn("Great food", html)   # default field kept
        self.assertIn('data-instance-id="blk_1"', html)
        self.assertIn('data-block-type="testimonial"', html)
        # instance data-edit ids are rewritten to the instance id
        self.assertIn('data-edit="blk_1.quote"', html)

    def test_duplicate_instances_keep_independent_values(self):
        catalog = self._catalog()
        content = {
            "regions": {
                "main": [
                    {"id": "a", "type": "testimonial", "fields": {"author": "One"}},
                    {"id": "b", "type": "testimonial", "fields": {"author": "Two"}},
                ]
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("One", html)
        self.assertIn("Two", html)
        self.assertIn('data-edit="a.author"', html)
        self.assertIn('data-edit="b.author"', html)

    def test_unknown_block_type_is_skipped(self):
        catalog = self._catalog()
        content = {"regions": {"main": [{"id": "x", "type": "nope", "fields": {}}]}}
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("Brand", html)  # chrome still renders
        self.assertNotIn('data-instance-id="x"', html)

    def test_chrome_defaults_and_content(self):
        catalog = self._catalog()
        content = {"nav": {"brand": "Acme"}, "regions": {"main": []}}
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("Acme", html)
        self.assertIn("Co", html)  # footer default

    def test_auto_nav_injection(self):
        shell = SHELL.replace(
            '<main data-region="main"></main>',
            '<nav data-nav-pages></nav><main data-region="main"></main>',
        )
        catalog = self._catalog()
        html = render_page_from_blocks(
            shell, {"regions": {"main": []}}, catalog,
            nav_pages=[{"title": "About", "url": "/about/"}],
        )
        self.assertIn('href="/about/"', html)
        self.assertIn("About", html)

    def test_header_region_renders_nav_links(self):
        from core.services.blocks import shell_region_names, upgrade_shell_chrome_slots

        shell = upgrade_shell_chrome_slots(SHELL)
        self.assertIn("site-header-inner", shell)
        self.assertIn("site-nav-links", shell)
        self.assertIn("site-header-actions", shell)
        self.assertIn('data-region="header-left"', shell)
        self.assertIn('data-region="header-right"', shell)
        self.assertIn('data-region="footer-left"', shell)
        self.assertNotIn("site-header-row", shell)
        names = shell_region_names(shell)
        self.assertIn("header-left", names)
        self.assertIn("header-center", names)
        self.assertIn("header-right", names)
        self.assertIn("main", names)
        self.assertIn("footer-center", names)
        catalog = self._catalog()
        catalog["nav-link"] = {
            "schema": {"fields": [], "defaults": {}},
            "html": (
                '<div data-block="nav-link">'
                '<a data-edit="navlink.text" data-type="text" href="/go">Go</a>'
                "</div>"
            ),
            "label": "Nav link",
        }
        content = {
            "regions": {
                "header-center": [
                    {"id": "blk_hdr1", "type": "nav-link", "fields": {"text": "Services"}}
                ],
                "main": [],
                "footer": [],
            }
        }
        html = render_page_from_blocks(shell, content, catalog)
        self.assertIn("Services", html)
        self.assertIn("data-header-link", html)
        self.assertNotIn('data-instance-id="blk_hdr1"', html)
        self.assertIn('data-header-zone="left"', html)
        self.assertIn('data-chrome-piece="brand"', html)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        left = soup.find(attrs={"data-header-zone": "left"})
        self.assertIsNotNone(left)
        self.assertIsNotNone(left.find(class_="site-header-brand"))
        preview = render_page_from_blocks(shell, content, catalog, preview=True)
        self.assertIn("cms-chrome-add", preview)
        self.assertIn("+ Add link", preview)
        self.assertIn("+ Add button", preview)
        self.assertIn("header-add-link", preview)
        self.assertNotIn('data-cms-dest="header-left"', preview)
        self.assertIn('data-cms-dest="footer-center"', preview)
        self.assertIn("function cmsDropDest", preview)
        self.assertIn("cms-sel-frame--below", preview)
        self.assertIn("cms-chrome-drag", preview)
        with_pages = render_page_from_blocks(
            shell, {"regions": {"main": []}}, catalog,
            nav_pages=[{"title": "About", "url": "/about/"}],
        )
        self.assertIn('href="/about/"', with_pages)
        self.assertIn("About", with_pages)

    def test_logo_image_renders_in_brand(self):
        from core.services.blocks import upgrade_shell_chrome_slots
        from bs4 import BeautifulSoup

        shell = upgrade_shell_chrome_slots(SHELL)
        catalog = self._catalog()
        html = render_page_from_blocks(
            shell,
            {
                "regions": {"main": []},
                "nav": {"brand": "Acme"},
                "_header": {"logo": "/media/tenants/1/logo.png", "logo_size": 64},
            },
            catalog,
        )
        soup = BeautifulSoup(html, "lxml")
        img = soup.find("img", class_="site-header-logo")
        self.assertIsNotNone(img)
        self.assertEqual(img.get("src"), "/media/tenants/1/logo.png")
        self.assertIn("height:64px", (img.get("style") or "").replace(" ", ""))
        parent = img.parent
        self.assertIsNotNone(parent)
        self.assertIn("has-logo", parent.get("class", []))
        self.assertIn("hide-name", parent.get("class", []))
        self.assertIsNotNone(soup.find(attrs={"data-header-name": "off"}))
        shown = render_page_from_blocks(
            shell,
            {
                "regions": {"main": []},
                "nav": {"brand": "Acme"},
                "_header": {
                    "logo": "/media/tenants/1/logo.png",
                    "show_name": True,
                },
            },
            catalog,
        )
        shown_soup = BeautifulSoup(shown, "lxml")
        shown_host = shown_soup.find(class_="site-header-logo").parent
        self.assertNotIn("hide-name", shown_host.get("class", []))
        self.assertIsNone(shown_soup.find(attrs={"data-header-name": "off"}))

    def test_three_column_header_upgrades_to_navbar(self):
        from core.services.blocks import upgrade_shell_chrome_slots

        old = (
            '<header class="site-header"><div class="site-header-row">'
            '<div class="site-header-col">'
            '<a class="site-brand" data-edit="header.brand" data-type="text">Brand</a>'
            '<div data-region="header-left"></div></div>'
            '<div class="site-header-col"><nav data-nav-pages></nav>'
            '<div data-region="header-center"></div></div>'
            '<div class="site-header-col" data-region="header-right"></div>'
            "</div></header><main data-region=\"main\"></main>"
            '<footer><span data-edit="footer.text" data-type="text">c</span></footer>'
        )
        shell = upgrade_shell_chrome_slots(old)
        self.assertIn("site-header-inner", shell)
        self.assertIn("site-header-actions", shell)
        self.assertIn("Brand", shell)
        self.assertNotIn("site-header-row", shell)
        self.assertIn('data-header-zone="left"', shell)
        self.assertIn(".site-header-inner{", shell.replace(" ", ""))

    def test_header_layout_presets(self):
        from core.services.blocks import upgrade_shell_chrome_slots
        from bs4 import BeautifulSoup

        shell = upgrade_shell_chrome_slots(SHELL)
        catalog = self._catalog()
        packed = render_page_from_blocks(
            shell,
            {"regions": {"main": []}, "_header": {"layout": "packed"}},
            catalog,
        )
        soup = BeautifulSoup(packed, "lxml")
        inner = soup.find(class_="site-header-inner")
        self.assertEqual(inner.get("data-header-layout"), "packed")
        left = soup.find(attrs={"data-header-zone": "left"})
        right = soup.find(attrs={"data-header-zone": "right"})
        self.assertIsNotNone(left.find(class_="site-header-brand"))
        self.assertIsNotNone(right.find("nav"))

        centered = render_page_from_blocks(
            shell,
            {"regions": {"main": []}, "_header": {"place": {
                "brand": "center", "nav": "left", "actions": "right",
            }}},
            catalog,
        )
        soup = BeautifulSoup(centered, "lxml")
        inner = soup.find(class_="site-header-inner")
        self.assertEqual(inner.get("data-header-layout"), "centered")
        center = soup.find(attrs={"data-header-zone": "center"})
        self.assertIsNotNone(center.find(class_="site-header-brand"))
        self.assertIsNotNone(center.find("nav"))

    def test_no_nav_marker_leaves_shell_untouched(self):
        catalog = self._catalog()
        html = render_page_from_blocks(
            SHELL, {"regions": {"main": []}}, catalog,
            nav_pages=[{"title": "About", "url": "/about/"}],
        )
        self.assertNotIn("/about/", html)


class NormalizeHeaderTests(TestCase):
    def test_migrates_nav_links_and_button(self):
        header = blocks.normalize_header(
            {},
            regions={
                "header-center": [{
                    "id": "blk_a", "type": "nav-link",
                    "fields": {"text": "Services", "text_href": "/s/"},
                }],
                "header-right": [{
                    "id": "blk_b", "type": "button",
                    "fields": {"label": "Book", "link": "/book/"},
                }],
            },
        )
        self.assertEqual(header["layout"], "classic")
        self.assertEqual(len(header["menu"]), 1)
        self.assertEqual(header["menu"][0]["label"], "Services")
        self.assertEqual(header["menu"][0]["href"], "/s/")
        self.assertTrue(header["menu"][0]["id"].startswith("nav_"))
        self.assertEqual(header["button"], {"on": True, "label": "Book", "href": "/book/"})
        self.assertEqual(header["logo"], "")
        self.assertEqual(header["logo_size"], 40)
        self.assertTrue(header["show_name"])

    def test_strips_javascript_href(self):
        header = blocks.normalize_header({
            "menu": [{"id": "nav_abcd1234", "label": "X", "href": "javascript:alert(1)"}],
        })
        self.assertEqual(header["menu"][0]["href"], "/")
        self.assertEqual(
            blocks.normalize_header({"logo": "javascript:alert(1)"})["logo"],
            "",
        )
        self.assertEqual(blocks.normalize_header({"logo_size": 200})["logo_size"], 80)
        self.assertEqual(blocks.normalize_header({"logo_size": 10})["logo_size"], 24)

    def test_ensure_header_clears_header_regions(self):
        content = {
            "regions": {
                "header-center": [{"id": "blk_a", "type": "nav-link", "fields": {"text": "A"}}],
                "main": [{"id": "blk_b", "type": "hero", "fields": {}}],
            }
        }
        blocks.ensure_header(content)
        self.assertEqual(content["regions"]["header-center"], [])
        self.assertEqual(content["regions"]["main"][0]["id"], "blk_b")
        self.assertEqual(content["_header"]["menu"][0]["label"], "A")
        self.assertEqual(content["_header"]["logo"], "")
        self.assertEqual(content["_header"]["logo_size"], 40)
        self.assertTrue(content["_header"]["show_name"])
        self.assertFalse(blocks.normalize_header({
            "logo": "/media/x.png",
        })["show_name"])
        self.assertTrue(blocks.normalize_header({
            "logo": "/media/x.png", "show_name": True,
        })["show_name"])


class MigrationParityTests(TestCase):
    def test_restaurant_block_render_matches_classic(self):
        sample = (
            Path(__file__).resolve().parent.parent.parent
            / "samples" / "restaurant.html"
        )
        html = sample.read_text(encoding="utf-8")

        classic = render_site(html, merge_with_defaults(build_schema(html), {}))

        shell_html, fragments = blocks.split_shell_and_blocks(html)
        block_keys = [k for k, _ in fragments]
        # hero/about/menu are body; nav/hours/footer are chrome (header/footer).
        self.assertEqual(block_keys, ["hero", "about", "menu"])

        block_types = [
            BlockType.objects.create(html_source=frag) for _, frag in fragments
        ]
        catalog = blocks.build_catalog(block_types)
        content = blocks.convert_content_to_regions({}, block_keys)
        block_html = render_page_from_blocks(shell_html, content, catalog)

        self.assertEqual(
            blocks.normalize_for_diff(block_html),
            blocks.normalize_for_diff(classic),
        )


class TemplateShellTests(TestCase):
    def test_is_block_shell_detection(self):
        shell = Template.objects.create(name="Shell", html_source=SHELL)
        classic = Template.objects.create(
            name="Classic",
            html_source='<body><section data-section="hero"></section></body>',
        )
        self.assertTrue(shell.is_block_shell)
        self.assertFalse(classic.is_block_shell)


class AnnotateFragmentTests(TestCase):
    def test_promotes_section_to_block(self):
        from unittest.mock import patch

        annotated_doc = (
            "<html><body>"
            '<section data-section="feature" data-label="Feature" data-group="Home">'
            '<h2 data-edit="feature.title" data-type="text">Fast</h2>'
            "</section></body></html>"
        )
        with patch("core.services.annotator.annotate_html", return_value=annotated_doc):
            from core.services.annotator import annotate_fragment

            frag = annotate_fragment("<section><h2>Fast</h2></section>")
        # promoted to a data-block wrapper the palette can use
        self.assertIn('data-block="feature"', frag)
        schema = build_block_schema(frag)
        self.assertEqual(schema["key"], "feature")
        self.assertEqual([f["id"] for f in schema["fields"]], ["title"])

    def test_raises_when_no_section(self):
        from unittest.mock import patch

        from core.services.annotator import AnnotatorError, annotate_fragment

        with patch("core.services.annotator.annotate_html",
                   return_value="<html><body><div>nope</div></body></html>"):
            with self.assertRaises(AnnotatorError):
                annotate_fragment("<div>nope</div>")


class MigrateCommandTests(TestCase):
    def _restaurant_template(self):
        from pathlib import Path

        sample = (
            Path(__file__).resolve().parent.parent.parent
            / "samples" / "restaurant.html"
        )
        return Template.objects.create(
            name="Restaurant", html_source=sample.read_text(encoding="utf-8")
        )

    def _tenant_for(self, template):
        from django.contrib.auth.models import User

        owner = User.objects.create_user("owner", password="x")
        return Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=owner,
            content={"hero": {"title": "Custom Welcome"}},
        )

    def test_dry_run_makes_no_changes(self):
        from django.core.management import call_command

        template = self._restaurant_template()
        self._tenant_for(template)
        call_command("migrate_template_to_blocks", str(template.pk))
        template.refresh_from_db()
        self.assertFalse(template.is_block_shell)
        self.assertEqual(BlockType.objects.count(), 0)

    def test_apply_converts_template_and_content(self):
        from django.core.management import call_command

        template = self._restaurant_template()
        tenant = self._tenant_for(template)
        call_command("migrate_template_to_blocks", str(template.pk), "--apply")

        template.refresh_from_db()
        tenant.refresh_from_db()
        self.assertTrue(template.is_block_shell)
        self.assertTrue(
            {"hero", "about", "menu"}.issubset(
                set(template.allowed_block_types.values_list("key", flat=True))
            )
        )
        # content rewritten into ordered instances + dual-write backup kept.
        instances = tenant.content["regions"]["main"]
        self.assertEqual([i["type"] for i in instances], ["hero", "about", "menu"])
        self.assertEqual(tenant.content["_classic"]["hero"]["title"], "Custom Welcome")
        # the overridden field survives the conversion + renders.
        html = blocks.render_content(template, tenant.content)
        self.assertIn("Custom Welcome", html)

    def test_already_shell_is_rejected(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        template = Template.objects.create(name="Shell2", html_source=SHELL)
        with self.assertRaises(CommandError):
            call_command("migrate_template_to_blocks", str(template.pk))


class EnsureBlockEditorTests(TestCase):
    def test_upgrades_classic_and_keeps_copy(self):
        from django.contrib.auth.models import User

        owner = User.objects.create_user("own", password="x")
        template = Template.objects.create(
            name="Classic",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            ),
        )
        tenant = Tenant.objects.create(
            name="Acme", subdomain="acme", template=template, owner=owner,
            content={"hero": {"title": "Kept"}},
        )
        blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        shell = tenant.template
        self.assertTrue(shell.is_block_shell)
        self.assertEqual(tenant.content["regions"]["main"][0]["fields"]["title"], "Kept")
        self.assertEqual(tenant.content["_classic"]["hero"]["title"], "Kept")
        self.assertGreaterEqual(shell.allowed_block_types.count(), 20)
        html = blocks.render_content(shell, tenant.content)
        self.assertIn("Kept", html)

    def test_clones_shared_library_template(self):
        from django.contrib.auth.models import User

        a = User.objects.create_user("a", password="x")
        b = User.objects.create_user("b", password="x")
        library = Template.objects.create(
            name="Shared",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            ),
        )
        t1 = Tenant.objects.create(
            name="One", subdomain="one", template=library, owner=a,
            content={"hero": {"title": "One"}},
        )
        t2 = Tenant.objects.create(
            name="Two", subdomain="two", template=library, owner=b,
            content={"hero": {"title": "Two"}},
        )
        blocks.ensure_block_editor(t1)
        t1.refresh_from_db()
        t2.refresh_from_db()
        library.refresh_from_db()
        self.assertTrue(t1.template.is_block_shell)
        self.assertNotEqual(t1.template_id, library.pk)
        self.assertEqual(t2.template_id, library.pk)
        self.assertFalse(library.is_block_shell)
        self.assertEqual(t2.content, {"hero": {"title": "Two"}})

    def test_skips_convert_when_classic_and_block_renders_differ(self):
        from unittest.mock import patch

        from django.contrib.auth.models import User

        owner = User.objects.create_user("gate", password="x")
        template = Template.objects.create(
            name="ClassicGate",
            html_source=(
                "<section data-section='hero' data-label='Hero'>"
                "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            ),
        )
        tenant = Tenant.objects.create(
            name="Gate", subdomain="gate", template=template, owner=owner,
            content={"hero": {"title": "Hi"}},
        )
        with patch(
            "core.services.blocks.classic_upgrade_is_safe",
            return_value=(False, "at 0: classic=...X... block=...Y..."),
        ):
            blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        self.assertFalse(tenant.template.is_block_shell)
        self.assertNotIn("regions", tenant.content or {})

    def test_problem_fixture_converts_when_parity_matches(self):
        from django.contrib.auth.models import User

        designed = """
        <!DOCTYPE html><html><head><style>
        body > .sec { display: block; }
        </style></head><body>
        <header class="nav" data-section="header" data-group="Header" data-label="Header">
          <a class="brand" href="#top">Burrus</a>
        </header>
        <section class="sec" data-section="problem" data-label="The Problem"
                 data-group="Sections">
          <p class="eyebrow" data-edit="problem.eyebrow" data-type="text">The Problem</p>
          <h2 class="h-sec" data-edit="problem.title" data-type="richtext">
            Busy isn't a strategy. <span class="hl">It's a symptom.</span>
          </h2>
        </section>
        <footer data-section="footer" data-group="Footer" data-label="Footer">
          <a href="mailto:office@burrus.com">office@burrus.com</a>
        </footer>
        </body></html>
        """
        owner = User.objects.create_user("parity", password="x")
        template = Template.objects.create(name="Parity", html_source=designed)
        tenant = Tenant.objects.create(
            name="Parity", subdomain="parity", template=template, owner=owner,
            content={},
        )
        matched, snippet = blocks.preview_classic_upgrade(designed, {})
        self.assertTrue(matched, snippet)
        blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        self.assertTrue(tenant.template.is_block_shell)

    def test_stale_regions_do_not_block_convert(self):
        """A leftover regions blob from a failed convert must not overwrite
        the instances rebuilt from the current annotated sections."""
        html = (
            "<section data-section='hero' data-label='Hero' data-group='Home'>"
            "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            "<section data-section='after' data-label='After' data-group='Home'>"
            "<p data-edit='after.body' data-type='text'>Next</p></section>"
        )
        stale = {
            "regions": {
                "main": [
                    {"id": "hero", "type": "hero", "fields": {}},
                    {"id": "old", "type": "after_course", "fields": {}},
                ]
            },
            "_classic": {},
        }
        converted = blocks.convert_content_to_regions(stale, ["hero", "after"])
        self.assertEqual(
            [item["type"] for item in converted["regions"]["main"]],
            ["hero", "after"],
        )
        matched, snippet = blocks.preview_classic_upgrade(html, stale)
        self.assertTrue(matched, snippet)

    def test_html_comments_between_sections_do_not_fail_parity(self):
        html = (
            "<section data-section='hero' data-label='Hero' data-group='Home'>"
            "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
            "<!-- HEADLINE ALTERNATES -->"
            "<section data-section='about' data-label='About' data-group='Home'>"
            "<p data-edit='about.body' data-type='text'>Copy</p></section>"
        )
        matched, snippet = blocks.preview_classic_upgrade(html, {})
        self.assertTrue(matched, snippet)

    def test_nested_sections_stay_inside_parent_block(self):
        html = (
            "<!doctype html><html><body>"
            "<header data-section='nav' data-group='Header'>Nav</header>"
            "<section data-section='proof' data-label='Proof' data-group='Home'>"
            "<p data-edit='proof.heading' data-type='text'>Who checked</p>"
            "<article data-section='testimonial_1' data-label='T1'>"
            "<p data-edit='testimonial_1.quote' data-type='text'>Great</p>"
            "</article>"
            "</section>"
            "<footer data-section='footer' data-group='Footer'>Foot</footer>"
            "</body></html>"
        )
        shell, fragments = blocks.split_shell_and_blocks(html)
        self.assertEqual([key for key, _ in fragments], ["proof"])
        self.assertIn("testimonial_1", fragments[0][1])
        self.assertIn('data-region="main"', shell)
        self.assertNotIn("HEADLINE", shell)
        matched, snippet = blocks.preview_classic_upgrade(html, {})
        self.assertTrue(matched, snippet)

    def test_overlays_stay_in_shell_and_nested_cards_stay_in_parent(self):
        """Any future client page — not a Burrus special case.

        Header/footer stay designed. Body bands become blocks. A mobile menu
        and video modal stay in the shell even if someone marked them.
        Nested slides stay inside their parent band.
        """
        html = """<!doctype html><html><body>
        <header class="nav" data-section="nav" data-group="Header" data-label="Nav">
          <a href="#top">Brand</a>
        </header>
        <div class="menu" id="menu" data-section="menu" data-label="Menu">
          <a href="#faq">FAQ</a>
        </div>
        <section data-section="hero" data-label="Hero" data-group="Home">
          <h1 data-edit="hero.title" data-type="text">Welcome guests</h1>
        </section>
        <section data-section="proof" data-label="Proof" data-group="Home">
          <p data-edit="proof.heading" data-type="text">Who checked</p>
          <article data-section="quote_1" data-label="Quote">
            <p data-edit="quote_1.body" data-type="text">Loved it</p>
          </article>
        </section>
        <footer data-section="footer" data-group="Footer" data-label="Footer">
          <a href="mailto:hi@example.com">hi@example.com</a>
        </footer>
        <div class="modal" id="videoModal" data-section="intro_video">Watch later</div>
        </body></html>"""
        from django.contrib.auth.models import User

        owner = User.objects.create_user("future", password="x")
        template = Template.objects.create(name="Any Client", html_source=html)
        tenant = Tenant.objects.create(
            name="Future Co", subdomain="futureco", template=template, owner=owner,
        )
        matched, snippet = blocks.preview_classic_upgrade(html, {})
        self.assertTrue(matched, snippet)
        blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        shell = tenant.template
        self.assertTrue(shell.is_block_shell)
        self.assertIn('id="menu"', shell.html_source)
        self.assertIn("videoModal", shell.html_source)
        self.assertIn("hi@example.com", shell.html_source)
        types = [i["type"] for i in tenant.content["regions"]["main"]]
        self.assertEqual(types, ["hero", "proof"])
        self.assertNotIn("menu", types)
        self.assertNotIn("intro_video", types)
        proof = BlockType.objects.get(key="proof")
        self.assertIn("quote_1", proof.html_source)
        rendered = blocks.render_content(shell, tenant.content)
        self.assertIn("Welcome guests", rendered)
        self.assertIn("Loved it", rendered)
        self.assertIn("Watch later", rendered)

    def test_designed_page_keeps_words_after_upgrade(self):
        """Classic annotated pages still render after the block-editor upgrade.

        Body sections become blocks so Add section works. The designed header
        and footer stay in the shell (no site-header chrome rewrite), and
        render must put the extracted sections back into the preview.
        """
        from django.contrib.auth.models import User

        designed = """
        <!DOCTYPE html><html><head><style>
        .rv{opacity:0} .rv.in{opacity:1}
        </style></head><body>
        <header class="nav" data-section="header_navigation" data-group="Header"
                data-label="Header Navigation">
          <a class="brand" href="#top">
            <img alt="Logo" src="https://cdn.example.com/logo.png"/>
          </a>
          <a class="btn nav-cta" href="#start">Start the course</a>
          <button class="burger" type="button"><span></span></button>
        </header>
        <section class="hero rv" data-section="hero" data-label="Hero" data-group="Home">
          <h1 data-edit="hero.title" data-type="text">Welcome to the course</h1>
        </section>
        <section class="sec rv" data-section="instructor" data-label="Instructor">
          <p data-edit="instructor.eyebrow" data-type="text">Daniel Burrus</p>
        </section>
        <section class="sec" data-section="faq" data-label="FAQ">
          <h2 data-edit="faq.heading" data-type="text">Questions we hear a lot</h2>
        </section>
        <footer class="foot" data-section="footer" data-group="Footer" data-label="Footer">
          <a data-edit="footer.email_link" data-type="link"
             href="mailto:office@burrus.com">office@burrus.com</a>
        </footer>
        </body></html>
        """
        owner = User.objects.create_user("dan", password="x")
        template = Template.objects.create(name="Burrus", html_source=designed)
        tenant = Tenant.objects.create(
            name="Course", subdomain="course", template=template, owner=owner,
            content={
                "hero": {"title": "Welcome to the course"},
                "instructor": {"eyebrow": "Daniel Burrus"},
                "faq": {"heading": "Questions we hear a lot"},
            },
        )
        blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        shell = tenant.template
        self.assertTrue(shell.is_block_shell)
        self.assertNotIn("site-header-inner", shell.html_source)
        self.assertIn("Start the course", shell.html_source)
        self.assertIn("office@burrus.com", shell.html_source)
        faq = BlockType.objects.get(key="faq_section")
        self.assertIn("Questions we hear a lot", faq.html_source)

        html = blocks.render_content(shell, tenant.content, preview=True)
        self.assertIn("Welcome to the course", html)
        self.assertIn("Daniel Burrus", html)
        self.assertIn("Questions we hear a lot", html)
        self.assertIn("office@burrus.com", html)
        self.assertIn("cdn.example.com/logo.png", html)
        self.assertIn("data-cms-preview-reveal", html)

    def test_designed_problem_section_stays_designed_after_upgrade(self):
        """Annotation converts a designed band by adding data-* only.

        The block-editor upgrade must put that same band back: two-tone
        headline span, framed portrait, quote + attribution, and the page
        CSS tokens. A hollow region wrapper must not break `body > section`
        style matching (display:contents on the filled main slot).
        """
        from django.contrib.auth.models import User

        designed = """
        <!DOCTYPE html><html><head><style>
        :root { --ocean: #929FA7; --snow: #fff; --text-2: rgba(255,255,255,.72); }
        body > .sec { display: block; }
        .h-sec { color: var(--snow); }
        .hl { color: var(--ocean); }
        .body-t { color: var(--text-2); }
        .quote cite { letter-spacing: .2em; }
        .portrait-in::before { content: ""; inset: 14px; border: 1px solid #ccc; }
        </style></head><body>
        <header class="nav" data-section="header" data-group="Header" data-label="Header">
          <a class="brand" href="#top">Burrus</a>
        </header>
        <section class="sec sec-alt" id="problem" data-section="problem"
                 data-label="The Problem" data-group="Sections">
          <div class="split split-r">
            <div>
              <p class="eyebrow" data-edit="problem.eyebrow" data-type="text">The Problem</p>
              <h2 class="h-sec" data-edit="problem.title" data-type="richtext">
                Busy isn't a strategy. <span class="hl">It's a symptom.</span>
              </h2>
              <p class="body-t" data-edit="problem.paragraph_1" data-type="richtext">
                The top five executives at General Motors were busy.
              </p>
              <blockquote class="quote">
                <p data-edit="problem.quote" data-type="richtext">
                  Being busy didn't help them.
                </p>
                <cite data-edit="problem.quote_author" data-type="text">Daniel Burrus</cite>
              </blockquote>
            </div>
            <figure class="portrait">
              <div class="portrait-in">
                <img alt="" data-edit="problem.portrait" data-type="image"
                     src="https://cdn.example.com/problem.jpg"/>
              </div>
            </figure>
          </div>
        </section>
        <footer data-section="footer" data-group="Footer" data-label="Footer">
          <a href="mailto:office@burrus.com">office@burrus.com</a>
        </footer>
        </body></html>
        """
        owner = User.objects.create_user("design", password="x")
        template = Template.objects.create(name="Designed", html_source=designed)
        tenant = Tenant.objects.create(
            name="Designed", subdomain="designed", template=template, owner=owner,
            content={},
        )
        blocks.ensure_block_editor(tenant)
        tenant.refresh_from_db()
        frag = BlockType.objects.get(key="problem").html_source
        self.assertIn('class="hl"', frag)
        self.assertIn("portrait-in", frag)
        self.assertIn("Daniel Burrus", frag)
        self.assertIn("split-r", frag)

        html = blocks.render_content(tenant.template, tenant.content, preview=True)
        self.assertIn('class="hl"', html)
        self.assertIn("It's a symptom.", html)
        self.assertIn("Daniel Burrus", html)
        self.assertIn("cdn.example.com/problem.jpg", html)
        self.assertIn("portrait-in", html)
        self.assertIn("--ocean: #929FA7", html)
        self.assertIn("display:contents", html.replace(" ", ""))
        self.assertNotIn("site-header-inner", html)

    def test_already_wrapped_shell_does_not_wipe_footer(self):
        """A template already rewritten by the old chrome upgrader still has
        the designed footer sitting inside ``footer-center``. Render must not
        clear that slot when there are no footer block instances."""
        from django.contrib.auth.models import User

        hero_frag = (
            '<section class="hero rv" data-section="hero" data-label="Hero">'
            '<h1 data-edit="hero.title" data-type="text">Welcome to the course</h1>'
            "</section>"
        )
        BlockType.objects.create(key="hero", html_source=hero_frag, label="Hero")
        owner = User.objects.create_user("wrap", password="x")
        wrapped = """
        <html><head><style>.rv{opacity:0}</style></head><body>
        <header class="nav" data-section="header_navigation" data-group="Header">
          <div class="site-header-inner">
            <div class="site-header-brand">
              <div class="site-header-brand-extra" data-region="header-left">
                <a class="brand" href="#top">
                  <img alt="Logo" src="https://cdn.example.com/logo.png"/>
                </a>
                <a class="btn nav-cta" href="#start">Start the course</a>
              </div>
            </div>
            <nav class="site-nav">
              <div class="site-nav-links" data-region="header-center"></div>
            </nav>
            <div class="site-header-actions" data-region="header-right"></div>
          </div>
        </header>
        <div data-region="main"></div>
        <footer class="foot" data-section="footer" data-group="Footer">
          <div class="site-footer-row">
            <div data-region="footer-left"></div>
            <div data-region="footer-center">
              <a href="mailto:office@burrus.com">office@burrus.com</a>
            </div>
            <div data-region="footer-right"></div>
          </div>
        </footer>
        </body></html>
        """
        template = Template.objects.create(name="Wrapped", html_source=wrapped)
        template.allowed_block_types.add(BlockType.objects.get(key="hero"))
        tenant = Tenant.objects.create(
            name="W", subdomain="wrapped", template=template, owner=owner,
            content={"regions": {"main": [
                {"id": "hero", "type": "hero", "fields": {"title": "Welcome to the course"}},
            ]}},
        )
        html = blocks.render_content(template, tenant.content, preview=True)
        self.assertIn("Welcome to the course", html)
        self.assertIn("office@burrus.com", html)
        self.assertIn("cdn.example.com/logo.png", html)
        self.assertIn("Start the course", html)


ROW2 = (
    '<div data-block="row-2" data-label="2 Column" data-category="Rows" '
    'style="display:grid;grid-template-columns:1fr 1fr;">'
    '<div data-region="col1"></div><div data-region="col2"></div></div>'
)

HEADLINE = (
    '<div data-block="headline" data-label="Headline" data-category="Text">'
    '<h2 data-edit="headline.text" data-type="text">Hi</h2></div>'
)


class NestedBlockTests(TestCase):
    def _catalog(self):
        row = BlockType.objects.create(html_source=ROW2)
        head = BlockType.objects.create(html_source=HEADLINE)
        return blocks.build_catalog([row, head])

    def test_block_region_names(self):
        self.assertEqual(blocks.block_region_names(ROW2), ["col1", "col2"])
        self.assertEqual(blocks.block_region_names(HEADLINE), [])

    def test_catalog_exposes_column_slots(self):
        catalog = self._catalog()
        self.assertEqual(catalog["row-2"]["regions"], ["col1", "col2"])
        self.assertEqual(catalog["headline"]["regions"], [])

    def test_seed_instance_seeds_children_for_rows_only(self):
        catalog = self._catalog()
        row = blocks.seed_instance("row-2", catalog)
        self.assertEqual(row["children"], {"col1": [], "col2": []})
        leaf = blocks.seed_instance("headline", catalog)
        self.assertNotIn("children", leaf)

    def test_nested_children_render_into_columns(self):
        catalog = self._catalog()
        content = {
            "regions": {
                "main": [
                    {
                        "id": "row1", "type": "row-2", "fields": {},
                        "children": {
                            "col1": [{"id": "h1", "type": "headline",
                                      "fields": {"text": "Left"}}],
                            "col2": [{"id": "h2", "type": "headline",
                                      "fields": {"text": "Right"}}],
                        },
                    }
                ]
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("Left", html)
        self.assertIn("Right", html)
        # each nested instance is rewritten to its own field id namespace
        self.assertIn('data-edit="h1.text"', html)
        self.assertIn('data-edit="h2.text"', html)
        # the row wrapper is present and holds the column slots
        self.assertIn('data-instance-id="row1"', html)

    def test_empty_columns_get_preview_placeholder_only(self):
        catalog = self._catalog()
        content = {"regions": {"main": [
            {"id": "row1", "type": "row-2", "fields": {},
             "children": {"col1": [], "col2": []}}
        ]}}
        # Preview render marks empty columns so the row is visible in the editor.
        preview_html = render_page_from_blocks(SHELL, content, catalog, preview=True)
        self.assertIn("data-empty-region", preview_html)
        # Each empty column gets a clickable "+" adder carrying its destination
        # (instance id / column name) so the editor can preselect it.
        self.assertIn("data-cms-add-here", preview_html)
        self.assertIn('data-cms-dest="row1/col1"', preview_html)
        self.assertIn('data-cms-dest="row1/col2"', preview_html)
        self.assertIn("function cmsRegionAt", preview_html)
        self.assertIn("function cmsDropDest", preview_html)
        self.assertIn("cms-sel-drag", preview_html)
        self.assertIn("cms-sel-frame--below", preview_html)
        # Public render never emits the placeholder or the adder.
        public_html = render_page_from_blocks(SHELL, content, catalog, preview=False)
        self.assertNotIn("data-empty-region", public_html)
        self.assertNotIn("data-cms-add-here", public_html)

    def test_count_region_blocks_counts_nested(self):
        content = {
            "regions": {
                "main": [
                    {"id": "row1", "type": "row-2", "fields": {},
                     "children": {"col1": [{"id": "h1", "type": "headline", "fields": {}}],
                                  "col2": []}},
                ]
            }
        }
        self.assertEqual(blocks.count_region_blocks(content), 2)

    def test_clone_instance_tree_deep_copies_with_fresh_ids(self):
        # A row with a nested headline (title "Kept") must survive the copy with
        # its child intact and every id regenerated (C2).
        src = {
            "id": "row1", "type": "row-2", "fields": {"gap": "lg"},
            "children": {
                "col1": [{"id": "h1", "type": "headline",
                          "fields": {"text": "Kept"}}],
                "col2": [],
            },
        }
        clone = blocks.clone_instance_tree(src)
        self.assertNotEqual(clone["id"], "row1")
        self.assertEqual(clone["fields"], {"gap": "lg"})
        child = clone["children"]["col1"][0]
        self.assertEqual(child["fields"], {"text": "Kept"})  # nested content kept
        self.assertNotEqual(child["id"], "h1")  # fresh id
        # Deep copy: mutating the clone doesn't touch the source.
        child["fields"]["text"] = "Changed"
        self.assertEqual(src["children"]["col1"][0]["fields"]["text"], "Kept")

    def test_clone_instance_tree_rejects_typeless(self):
        self.assertIsNone(blocks.clone_instance_tree({"id": "x"}))
        self.assertIsNone(blocks.clone_instance_tree("nope"))


class NormalizeRegionsTests(TestCase):
    def _template(self):
        tpl = Template.objects.create(name="Builder", html_source=SHELL)
        row = BlockType.objects.create(html_source=ROW2)
        head = BlockType.objects.create(html_source=HEADLINE)
        tpl.allowed_block_types.add(row, head)
        return tpl

    def test_recurses_and_keeps_valid_children(self):
        from dashboard.views import _normalize_regions

        tpl = self._template()
        content = {
            "regions": {
                "main": [
                    {"id": "row1", "type": "row-2", "fields": {"bogus": "x"},
                     "children": {
                         "col1": [{"id": "h1", "type": "headline",
                                   "fields": {"text": "Hi", "nope": "y"}}],
                         "col2": [],
                     }},
                ]
            }
        }
        _normalize_regions(content, tpl)
        row = content["regions"]["main"][0]
        self.assertEqual(row["fields"], {})  # unknown field dropped
        child = row["children"]["col1"][0]
        self.assertEqual(child["fields"], {"text": "Hi"})  # unknown dropped, known kept

    def test_unknown_nested_type_raises(self):
        from dashboard.views import _BlockValidationError, _normalize_regions

        tpl = self._template()
        content = {
            "regions": {
                "main": [
                    {"id": "row1", "type": "row-2", "fields": {},
                     "children": {"col1": [{"id": "x", "type": "ghost", "fields": {}}],
                                  "col2": []}},
                ]
            }
        }
        with self.assertRaises(_BlockValidationError):
            _normalize_regions(content, tpl)

    def test_children_past_depth_cap_rejected(self):
        # Silently dropping children at MAX_BLOCK_DEPTH is data loss (the client
        # watches nested rows vanish on save), so an incoming payload that nests
        # children past the cap is rejected outright (E11).
        from dashboard.views import _BlockValidationError, _normalize_regions

        tpl = self._template()
        row_at_cap = {"id": "r2", "type": "row-2", "fields": {},
                      "children": {"col1": [{"id": "d", "type": "headline",
                                             "fields": {}}], "col2": []}}
        row_depth1 = {"id": "r1", "type": "row-2", "fields": {},
                      "children": {"col1": [row_at_cap], "col2": []}}
        row_depth0 = {"id": "r0", "type": "row-2", "fields": {},
                      "children": {"col1": [row_depth1], "col2": []}}
        content = {"regions": {"main": [row_depth0]}}
        with self.assertRaises(_BlockValidationError):
            _normalize_regions(content, tpl)

    def test_empty_children_at_depth_cap_accepted(self):
        # A layout block sitting exactly at the cap with no children is fine; it
        # just carries no ``children`` key (nothing to recurse into).
        from dashboard.views import _normalize_regions

        tpl = self._template()
        row_at_cap = {"id": "r2", "type": "row-2", "fields": {},
                      "children": {"col1": [], "col2": []}}
        row_depth1 = {"id": "r1", "type": "row-2", "fields": {},
                      "children": {"col1": [row_at_cap], "col2": []}}
        row_depth0 = {"id": "r0", "type": "row-2", "fields": {},
                      "children": {"col1": [row_depth1], "col2": []}}
        content = {"regions": {"main": [row_depth0]}}
        _normalize_regions(content, tpl)
        r0 = content["regions"]["main"][0]
        r1 = r0["children"]["col1"][0]
        r2 = r1["children"]["col1"][0]  # depth 2 == cap
        self.assertNotIn("children", r2)

    def test_cap_counts_nested_instances(self):
        from dashboard.views import _BlockValidationError, _normalize_regions

        tpl = self._template()
        children = [{"id": f"h{i}", "type": "headline", "fields": {}}
                    for i in range(blocks.MAX_BLOCKS_PER_PAGE + 1)]
        content = {"regions": {"main": [
            {"id": "row1", "type": "row-2", "fields": {},
             "children": {"col1": children, "col2": []}}
        ]}}
        with self.assertRaises(_BlockValidationError):
            _normalize_regions(content, tpl)

    def test_malformed_instance_id_is_regenerated(self):
        # An id carrying markup (or anything not blk_<hex>) is replaced with a
        # fresh safe id, so it can never reach an HTML attribute verbatim (E14).
        from dashboard.views import _INSTANCE_ID_RE, _normalize_regions

        tpl = self._template()
        content = {"regions": {"main": [
            {"id": 'blk_"><script>', "type": "headline", "fields": {}},
            {"id": "not-a-blk-id", "type": "headline", "fields": {}},
        ]}}
        _normalize_regions(content, tpl)
        ids = [b["id"] for b in content["regions"]["main"]]
        for iid in ids:
            self.assertRegex(iid, _INSTANCE_ID_RE)
        self.assertEqual(len(set(ids)), 2)  # both distinct, both regenerated

    def test_valid_instance_id_is_preserved(self):
        from dashboard.views import _normalize_regions

        tpl = self._template()
        content = {"regions": {"main": [
            {"id": "blk_abcdef01", "type": "headline", "fields": {}},
        ]}}
        _normalize_regions(content, tpl)
        self.assertEqual(content["regions"]["main"][0]["id"], "blk_abcdef01")


class SeedBuilderBlocksTests(TestCase):
    def test_seed_creates_primitives_and_attaches_to_shell(self):
        from django.core.management import call_command

        tpl = Template.objects.create(name="ShellSeed", html_source=SHELL)
        self.assertTrue(tpl.is_block_shell)
        call_command("seed_builder_blocks", "--attach", str(tpl.pk))

        keys = set(BlockType.objects.values_list("key", flat=True))
        for expected in ["row-1", "row-6", "headline", "paragraph", "image",
                         "button", "form"]:
            self.assertIn(expected, keys)

        allowed = set(tpl.allowed_block_types.values_list("key", flat=True))
        self.assertIn("row-2", allowed)
        self.assertIn("headline", allowed)

        # Rows expose column slots; leaves do not.
        row = BlockType.objects.get(key="row-3")
        self.assertEqual(blocks.block_region_names(row.html_source),
                         ["col1", "col2", "col3"])
        # The GHL form primitive parses (empty embed slot) without raising.
        form = BlockType.objects.get(key="form")
        self.assertEqual(form.schema["fields"][0]["type"], "ghl-embed")


from django.test import override_settings


@override_settings(
    TENANT_BASE_DOMAIN="localhost",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)
class BuildWithBlocksTemplateCreateTests(TestCase):
    """Agency 'Build with blocks' template creation: makes a client-editable
    block shell and attaches the full block library, with no HTML paste."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.staff = User.objects.create_user("op", password="x", is_staff=True)
        self.client.force_login(self.staff)

    def test_blocks_mode_creates_shell_with_full_library(self):
        resp = self.client.post(
            "/dashboard/templates/new/",
            {"name": "Blank Blocks", "description": "", "build_mode": "blocks"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 302)  # -> detail
        tpl = Template.objects.get(name="Blank Blocks")
        self.assertTrue(tpl.is_block_shell)  # has a data-region slot
        self.assertEqual(tpl.editing_mode, Template.EDITING_EDITABLE)
        # Full primitive library attached so the client can build anything.
        self.assertGreaterEqual(tpl.allowed_block_types.count(), 20)
        self.assertIn(
            "row-2", set(tpl.allowed_block_types.values_list("key", flat=True))
        )

    def test_blocks_mode_ignores_pasted_html(self):
        resp = self.client.post(
            "/dashboard/templates/new/",
            {"name": "Blocks Win", "build_mode": "blocks",
             "html_source": "<div>totally custom</div>"},
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 302)
        tpl = Template.objects.get(name="Blocks Win")
        self.assertNotIn("totally custom", tpl.html_source)
        self.assertIn('data-region="main"', tpl.html_source)

    def test_paste_mode_still_requires_html(self):
        resp = self.client.post(
            "/dashboard/templates/new/",
            {"name": "Pasted", "build_mode": "paste", "html_source": "   "},
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 200)  # re-renders the form
        self.assertFalse(Template.objects.filter(name="Pasted").exists())

    def test_new_client_blocks_mode_builds_shell_site(self):
        """New-client flow with template='__blocks__' spins up a block-shell
        site with the full library and no HTML, so the client can build it."""
        resp = self.client.post(
            "/dashboard/sites/new/",
            {
                "name": "Bella's Salon",
                "subdomain": "bellas",
                "template": "__blocks__",
                "client_username": "bella",
            },
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 302)  # -> site_created (credentials)
        tenant = Tenant.objects.get(subdomain="bellas")
        self.assertTrue(tenant.template.is_block_shell)
        self.assertEqual(tenant.template.editing_mode, Template.EDITING_EDITABLE)
        self.assertGreaterEqual(tenant.template.allowed_block_types.count(), 20)


SELECT_BLOCK = (
    '<section data-block="reviews" data-label="Reviews">'
    '<div class="grid" data-edit="reviews.cols" data-type="select" '
    'data-label="Columns" data-apply="style:--cols" '
    'data-default="repeat(3,1fr)" '
    'data-options="1 col=1fr;2 cols=repeat(2,1fr);3 cols=repeat(3,1fr)">'
    '<p data-edit="reviews.body" data-type="text">Hi</p></div>'
    '<div class="look" data-edit="reviews.look" data-type="select" '
    'data-label="Look" data-apply="class" data-default="look-a" '
    'data-options="A=look-a;B=look-b"></div>'
    "</section>"
)


class SelectFieldTests(TestCase):
    def test_schema_carries_options_apply_and_default(self):
        schema = build_block_schema(SELECT_BLOCK)
        cols = next(f for f in schema["fields"] if f["id"] == "cols")
        self.assertEqual(cols["type"], "select")
        self.assertEqual(cols["apply"], "style:--cols")
        self.assertEqual(cols["default"], "repeat(3,1fr)")
        self.assertEqual(
            [o["value"] for o in cols["options"]],
            ["1fr", "repeat(2,1fr)", "repeat(3,1fr)"],
        )
        look = next(f for f in schema["fields"] if f["id"] == "look")
        self.assertEqual(look["apply"], "class")
        self.assertEqual(look["default"], "look-a")

    def test_default_falls_back_to_first_option(self):
        html = (
            '<div data-edit="x.y" data-type="select" '
            'data-options="One=1;Two=2"></div>'
        )
        from bs4 import BeautifulSoup

        from core.parser import _extract_default

        el = BeautifulSoup(html, "lxml").find(attrs={"data-edit": True})
        self.assertEqual(_extract_default(el, "select"), "1")

    def _apply(self, html, value):
        from bs4 import BeautifulSoup

        from core.renderer import _apply_select

        el = BeautifulSoup(html, "lxml").find(attrs={"data-edit": True})
        _apply_select(el, value)
        return el

    def test_style_apply_sets_declared_value(self):
        el = self._apply(
            '<div data-edit="a.b" data-apply="style:--cols" '
            'data-options="two=repeat(2,1fr)">x</div>',
            "repeat(2,1fr)",
        )
        self.assertIn("--cols: repeat(2,1fr);", el.get("style", ""))

    def test_style_apply_rejects_undeclared_value(self):
        el = self._apply(
            '<div data-edit="a.b" data-apply="style:--cols" '
            'data-options="two=repeat(2,1fr)">x</div>',
            "evil; } body{display:none}",
        )
        self.assertNotIn("display:none", el.get("style", ""))
        self.assertNotIn("evil", el.get("style", ""))

    def test_class_apply_toggles_declared_class(self):
        el = self._apply(
            '<div class="card" data-edit="a.b" data-apply="class" '
            'data-options="A=look-a;B=look-b">x</div>',
            "look-b",
        )
        self.assertEqual(el.get("class"), ["card", "look-b"])

    def test_class_apply_swaps_not_stacks(self):
        from bs4 import BeautifulSoup

        from core.renderer import _apply_select

        el = BeautifulSoup(
            '<div class="card look-a" data-edit="a.b" data-apply="class" '
            'data-options="A=look-a;B=look-b">x</div>',
            "lxml",
        ).find(attrs={"data-edit": True})
        _apply_select(el, "look-b")
        self.assertEqual(el.get("class"), ["card", "look-b"])

    def test_class_apply_ignores_undeclared_value(self):
        el = self._apply(
            '<div class="card" data-edit="a.b" data-apply="class" '
            'data-options="A=look-a;B=look-b">x</div>',
            "evil-class",
        )
        self.assertEqual(el.get("class"), ["card"])

    def test_select_applies_through_block_assembly(self):
        bt = BlockType.objects.create(html_source=SELECT_BLOCK)
        catalog = blocks.build_catalog([bt])
        content = {
            "regions": {
                "main": [
                    {"id": "r1", "type": "reviews",
                     "fields": {"cols": "repeat(2,1fr)", "look": "look-b"}},
                ]
            }
        }
        html = render_page_from_blocks(SHELL, content, catalog)
        # data-edit ids are namespaced to the instance, and the chosen values
        # are applied to the elements (style var + toggled class).
        self.assertIn('data-edit="r1.cols"', html)
        self.assertIn("--cols: repeat(2,1fr);", html)
        self.assertIn("look-b", html)

    def test_select_default_applied_when_unset(self):
        bt = BlockType.objects.create(html_source=SELECT_BLOCK)
        catalog = blocks.build_catalog([bt])
        content = {"regions": {"main": [
            {"id": "r1", "type": "reviews", "fields": {}}]}}
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn("--cols: repeat(3,1fr);", html)
        self.assertIn("look-a", html)


EMBED_CODE_BLOCK = (
    '<section data-block="widget" data-label="Widget">'
    '<iframe data-edit="widget.src" data-type="embed" data-label="Embed URL" '
    'src="https://example.com/a"></iframe>'
    '<div data-edit="widget.html" data-type="code" data-label="Code">'
    '<p>default</p></div>'
    "</section>"
)


class EmbedAndCodeFieldTests(TestCase):
    def test_schema_types(self):
        schema = build_block_schema(EMBED_CODE_BLOCK)
        types = {f["id"]: f["type"] for f in schema["fields"]}
        self.assertEqual(types["src"], "embed")
        self.assertEqual(types["html"], "code")

    def test_embed_default_from_src_and_code_from_contents(self):
        from bs4 import BeautifulSoup

        from core.parser import _extract_default

        soup = BeautifulSoup(EMBED_CODE_BLOCK, "lxml")
        iframe = soup.find("iframe")
        code = soup.find(attrs={"data-edit": "widget.html"})
        self.assertEqual(_extract_default(iframe, "embed"), "https://example.com/a")
        self.assertIn("default", _extract_default(code, "code"))

    def test_render_applies_embed_src_and_raw_code(self):
        bt = BlockType.objects.create(html_source=EMBED_CODE_BLOCK)
        catalog = blocks.build_catalog([bt])
        content = {"regions": {"main": [
            {"id": "w1", "type": "widget", "fields": {
                "src": "https://maps.example/embed?q=1",
                "html": '<b class="x">raw &amp; <i>markup</i></b>',
            }},
        ]}}
        html = render_page_from_blocks(SHELL, content, catalog)
        self.assertIn('src="https://maps.example/embed?q=1"', html)
        # Raw HTML rendered as markup, not escaped text.
        self.assertIn('<b class="x">raw', html)
        self.assertIn("<i>markup</i>", html)


class ExpandedLibraryTests(TestCase):
    def test_all_expanded_blocks_seed_with_fields(self):
        from django.core.management import call_command

        call_command("seed_builder_blocks")
        expected = [
            "divider", "spacer", "icon", "video", "slider", "gallery", "logos",
            "faq", "reviews", "counter", "pricing", "progress", "feature",
            "social", "map", "qr", "code", "countdown",
        ]
        for key in expected:
            bt = BlockType.objects.get(key=key)
            self.assertTrue(bt.label, f"{key} has no label")
            self.assertTrue(
                bt.schema.get("fields"), f"{key} derived no editable fields"
            )

    def test_embed_and_code_blocks_use_new_types(self):
        from django.core.management import call_command

        call_command("seed_builder_blocks")
        self.assertEqual(
            BlockType.objects.get(key="map").schema["fields"][0]["type"], "embed"
        )
        self.assertEqual(
            BlockType.objects.get(key="code").schema["fields"][0]["type"], "code"
        )


class PrimitiveRenderSweepTests(TestCase):
    """Client-readiness guard: every seeded primitive must render in both the
    editor (preview) and public modes without raising, appear on the page, keep
    its field namespacing, and never leak preview-only chrome into the public
    render. Instantiating each block alone isolates failures to one block.
    """

    def setUp(self):
        from django.core.management import call_command

        call_command("seed_builder_blocks")
        self.block_types = list(BlockType.objects.all())
        self.catalog = blocks.build_catalog(self.block_types)
        # Sanity: the full library should be present (32 primitives today).
        self.assertGreaterEqual(len(self.block_types), 20)

    def _render_one(self, key, preview):
        inst = blocks.seed_instance(key, self.catalog)
        content = {"regions": {"main": [inst]}}
        html = render_page_from_blocks(SHELL, content, self.catalog, preview=preview)
        return inst, html

    def test_every_primitive_renders_in_both_modes(self):
        preview_only = ("data-empty-region", "data-cms-add-here", "data-cms-label")
        for bt in self.block_types:
            key = bt.key
            with self.subTest(block=key):
                # Preview render: instance present; chrome still renders.
                inst, preview_html = self._render_one(key, preview=True)
                self.assertIn(
                    f'data-instance-id="{inst["id"]}"', preview_html,
                    f"{key}: instance missing from preview render",
                )
                self.assertIn("Brand", preview_html)  # shell chrome intact

                # Public render: instance present, and NONE of the preview-only
                # editor markers leak to the live site.
                inst2, public_html = self._render_one(key, preview=False)
                self.assertIn(
                    f'data-instance-id="{inst2["id"]}"', public_html,
                    f"{key}: instance missing from public render",
                )
                for marker in preview_only:
                    self.assertNotIn(
                        marker, public_html,
                        f"{key}: preview-only marker '{marker}' leaked to public render",
                    )

    def test_editable_fields_are_namespaced_to_the_instance(self):
        # Non-layout blocks expose at least one editable field; its data-edit id
        # must be rewritten under the instance's own namespace so duplicates stay
        # independent.
        for bt in self.block_types:
            regions = blocks.block_region_names(bt.html_source)
            fields = (bt.schema or {}).get("fields") or []
            if regions or not fields:
                continue  # layout rows / field-less blocks handled elsewhere
            with self.subTest(block=bt.key):
                inst, html = self._render_one(bt.key, preview=True)
                self.assertIn(
                    f'data-edit="{inst["id"]}.', html,
                    f"{bt.key}: no instance-namespaced field id in render",
                )


class RenderParityTests(TestCase):
    """Publish-flow guard: what a client sees in the editor preview is exactly
    what visitors get on the published page. Publishing is a pure visibility
    toggle (`is_published`) over the same canonical content, so the preview and
    public renders must agree on every block instance, every editable field, and
    every content value — the only difference being editor-only chrome, which
    must NOT appear publicly.
    """

    def setUp(self):
        from django.core.management import call_command

        call_command("seed_builder_blocks")
        self.catalog = blocks.build_catalog(list(BlockType.objects.all()))
        # A realistic mixed page: a top-level headline, a 2-column row with a
        # nested headline + paragraph, and a button — covering flat + nested +
        # multiple field types.
        self.content = {"regions": {"main": [
            {"id": "hd1", "type": "headline", "fields": {"text": "PARITY_HEADLINE"}},
            {"id": "row1", "type": "row-2", "fields": {}, "children": {
                "col1": [{"id": "hd2", "type": "headline", "fields": {"text": "PARITY_LEFT"}}],
                "col2": [{"id": "pg1", "type": "paragraph", "fields": {"body": "<p>PARITY_RIGHT</p>"}}],
            }},
            {"id": "btn1", "type": "button", "fields": {"label": "PARITY_BTN", "link": "/contact/"}},
        ]}}

    @staticmethod
    def _ids(attr, html):
        import re
        # Keep only real id tokens; the preview bridge <script> contains JS string
        # literals like '[data-instance-id="' + id + '"]' that would otherwise be
        # captured as bogus ids.
        return {
            m for m in re.findall(attr + r'="([^"]+)"', html)
            if re.fullmatch(r"[A-Za-z0-9_.\-]+", m)
        }

    def test_public_render_matches_preview_content(self):
        preview = render_page_from_blocks(SHELL, self.content, self.catalog, preview=True)
        public = render_page_from_blocks(SHELL, self.content, self.catalog, preview=False)

        # 1. Every client-entered value appears in BOTH renders.
        for value in ("PARITY_HEADLINE", "PARITY_LEFT", "PARITY_RIGHT", "PARITY_BTN", "/contact/"):
            self.assertIn(value, preview, f"{value} missing from preview")
            self.assertIn(value, public, f"{value} missing from public render")

        # 2. Same block instances and same editable fields on both sides.
        self.assertEqual(
            self._ids("data-instance-id", public),
            self._ids("data-instance-id", preview),
            "block instances differ between preview and public",
        )
        self.assertEqual(
            self._ids("data-edit", public),
            self._ids("data-edit", preview),
            "editable field ids differ between preview and public",
        )

        # 3. Editor-only chrome never ships to the public page.
        for marker in ("data-cms-label", "data-empty-region", "data-cms-add-here",
                       "cms-editable", "PREVIEW_BRIDGE", "cms-sel-frame"):
            self.assertNotIn(marker, public, f"preview-only chrome '{marker}' leaked to public render")
