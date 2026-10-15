"""CMS-74: every template font is editable, and text is grouped by role."""
import json

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.models import Template, Tenant, TenantMembership
from core.parser import build_schema
from core.renderer import render_site
from core.tests.test_typography_tokens import PLAIN_TPL, STORAGES, style_text
from core.typography import detect_typography

# IAM's real shape: a third font (--mono), a large "display" statement styled
# with the body font, nav links, buttons and small tags.
SITE_TPL = """<!doctype html><html><head>
<style>
:root{--bg:#000;--disp:'Bebas Neue',sans-serif;--body:'Inter',sans-serif;
  --mono:'JetBrains Mono',monospace}
body{font-family:var(--body);background:var(--bg)}
.h-title{font-family:var(--disp);font-size:clamp(2.2rem,5vw,4.6rem)}
.sec-h{font-family:var(--disp)}
.shift-reveal{font-family:var(--body);font-weight:600;font-size:clamp(1.45rem,3.2vw,2.7rem)}
.n-links a{font-family:var(--body);font-size:.8rem}
.btn{font-family:var(--body)}
.tag{font-family:var(--mono);font-size:.7rem}
.lead{font-family:var(--body);font-size:1.1rem}
</style></head><body>
<nav><div class="n-links"><a href="#a">A</a></div></nav>
<section data-section="hero"><h1 class="h-title" data-edit="hero.title">Hi</h1>
<a class="btn" data-edit="hero.cta" data-type="link" href="#a">Go</a><span class="tag">x</span></section>
<section data-section="shift"><h2 class="sec-h" data-edit="shift.title">Shift</h2>
<p class="shift-reveal" data-edit="shift.statement">In a world</p>
<p class="lead" data-edit="shift.lead">Lead</p></section>
</body></html>"""


def typo(html):
    return detect_typography(BeautifulSoup(html, "lxml"))


class FontSlotTests(SimpleTestCase):
    def test_every_template_font_is_a_slot(self):
        slots = {s["name"]: s for s in typo(SITE_TPL)["slots"]}
        self.assertEqual(list(slots), ["body", "disp", "font-title", "mono"])
        self.assertEqual(
            {n: (s["key"], s["label"], s["group"]) for n, s in slots.items()},
            {
                "body": ("fontFamily", "Body font", "body"),
                "disp": ("headingFamily", "Heading font", "heading"),
                "font-title": ("titleFamily", "Title font", "title"),
                "mono": ("font:mono", "Mono font", "other"),
            },
        )
        self.assertEqual(slots["mono"]["generic"], "monospace")
        self.assertEqual(slots["mono"]["value"].replace('"', "'"), "'JetBrains Mono',monospace")

    def test_plain_template_has_no_tokens_or_slots_but_titles_can_be_customized(self):
        found = typo(PLAIN_TPL)
        self.assertEqual(found["tokens"], [])
        self.assertEqual(found["slots"], [])
        self.assertEqual(found["groups"][0]["selectors"], ["h1"])

    def test_schema_exposes_slots_and_groups_and_brand_skips_them(self):
        html = SITE_TPL.replace("<style>", "<style data-tokens>", 1)
        schema = build_schema(html)
        self.assertEqual([s["name"] for s in schema["font_slots"]], ["body", "disp", "font-title", "mono"])
        self.assertTrue(schema["text_groups"])
        brand = next(s for s in schema["sections"] if s["id"] == "brand")
        self.assertEqual([f["id"] for f in brand["fields"]], ["brand.bg"])


class TextGroupTests(SimpleTestCase):
    def groups(self):
        return {g["id"]: g for g in typo(SITE_TPL)["groups"]}

    def test_text_is_grouped_into_title_heading_and_body(self):
        groups = self.groups()
        self.assertEqual(list(groups), ["title", "heading", "body"])
        self.assertEqual(groups["title"]["selectors"], [".h-title"])
        self.assertEqual(groups["heading"]["selectors"], [".sec-h", ".shift-reveal"])
        self.assertEqual(
            groups["body"]["selectors"],
            ["body", ".n-links a", ".btn", ".tag", ".lead"],
        )

    def test_group_defaults_to_the_font_the_template_uses(self):
        groups = self.groups()
        self.assertEqual(groups["title"]["default"], "disp")
        self.assertEqual(groups["heading"]["default"], "disp")
        self.assertEqual(groups["body"]["default"], "body")
        self.assertEqual(groups["title"]["label"], "Title")
        self.assertTrue(groups["heading"]["mixed"])

    def test_group_using_several_fonts_is_marked_mixed(self):
        html = SITE_TPL.replace(".lead{font-family:var(--body)", ".lead{font-family:var(--mono)")
        groups = {g["id"]: g for g in typo(html)["groups"]}
        self.assertTrue(groups["body"]["mixed"])


class RenderTests(SimpleTestCase):
    def test_no_saved_values_leave_template_fonts_alone(self):
        css = style_text(render_site(SITE_TPL, {}), "data-cms-typography")
        self.assertIsNone(css)

    def test_extra_font_slot_is_overridden_and_loaded(self):
        html = render_site(SITE_TPL, {"_global": {"font:mono": "Roboto Mono"}})
        self.assertIn("--mono: 'Roboto Mono', monospace;", style_text(html, "data-cms-typography"))
        self.assertIn("family=Roboto+Mono", html)

    def test_heading_font_reaches_title_and_heading_groups(self):
        css = style_text(render_site(SITE_TPL, {
            "_global": {"headingFamily": "Oswald"},
        }), "data-cms-typography")
        self.assertIn("--disp: 'Oswald', sans-serif;", css)
        self.assertIn(".h-title{font-family:var(--disp)}", css)
        self.assertIn(".sec-h{font-family:var(--disp)}", css)
        self.assertIn(".shift-reveal{font-family:var(--disp)}", css)
        self.assertNotIn(".btn{font-family", css)
        self.assertNotIn(".tag{font-family", css)

    def test_body_font_reaches_body_group(self):
        css = style_text(render_site(SITE_TPL, {
            "_global": {"fontFamily": "Poppins"},
        }), "data-cms-typography")
        self.assertIn("--body: 'Poppins', sans-serif;", css)
        self.assertIn(".btn{font-family:var(--body)}", css)
        self.assertIn(".tag{font-family:var(--body)}", css)
        self.assertIn(".n-links a{font-family:var(--body)}", css)
        self.assertNotIn(".shift-reveal{font-family", css)

    def test_group_can_switch_to_another_template_font(self):
        html = render_site(SITE_TPL, {"_global": {"group:heading": "body"}})
        css = style_text(html, "data-cms-typography")
        self.assertIn(".sec-h{font-family:var(--body)}", css)
        self.assertIn(".shift-reveal{font-family:var(--body)}", css)

    def test_unknown_slot_or_group_is_ignored(self):
        html = render_site(SITE_TPL, {"_global": {
            "group:heading": "evil}body{display:none", "group:nope": "disp",
            "font:missing": "Lora",
        }})
        css = style_text(html, "data-cms-typography")
        self.assertNotIn("display:none", html)
        self.assertIsNone(css)

    def test_preview_map_lists_slots_and_groups(self):
        html = render_site(SITE_TPL, {}, preview=True)
        data = json.loads(BeautifulSoup(html, "lxml").find("script", id="cms-typography").string)
        self.assertEqual(set(data), {"roles", "slots", "groups"})
        self.assertEqual(data["roles"]["heading-font"], {"name": "disp", "generic": "sans-serif"})
        self.assertEqual(
            data["slots"][3], {"name": "mono", "key": "font:mono", "generic": "monospace"},
        )
        group = next(g for g in data["groups"] if g["id"] == "heading")
        self.assertEqual(group["selectors"], [".sec-h", ".shift-reveal"])
        self.assertEqual(group["rules"][0]["context"], [])

    def test_title_font_changes_titles_without_changing_heading_or_body_font(self):
        html = render_site(SITE_TPL, {"_global": {
            "titleFamily": "Lora", "headingFamily": "Oswald", "fontFamily": "Poppins",
        }})
        css = style_text(html, "data-cms-typography")
        self.assertIn("--font-title: 'Lora', sans-serif;", css)
        self.assertIn(".h-title{font-family:var(--font-title)}", css)
        self.assertIn(".sec-h{font-family:var(--disp)}", css)
        self.assertIn(".btn{font-family:var(--body)}", css)
        self.assertIn("family=Lora", html)

    def test_legacy_class_titles_can_change_independently_of_heading_font(self):
        legacy = PLAIN_TPL.replace('</style>', '.hero-title{font-family:Georgia}</style>')
        legacy = legacy.replace('<h1 ', '<h1 class="hero-title" ')
        html = render_site(legacy, {"_global": {"titleFamily": "Lora", "headingFamily": "Oswald"}})
        self.assertIn(".hero-title{font-family:var(--font-title)}", style_text(html, "data-cms-typography"))
        self.assertEqual(style_text(html, "data-cms-global"), "h2,h3,h4,h5,h6{font-family: Oswald;}")

    def test_title_mapping_takes_precedence_over_title_font_picker(self):
        css = style_text(render_site(SITE_TPL, {"_global": {
            "titleFamily": "Lora", "group:title": "mono",
        }}), "data-cms-typography")
        self.assertIn(".h-title{font-family:var(--mono)}", css)

    def test_all_groups_can_share_title_preset_without_alias_cycles(self):
        css = style_text(render_site(SITE_TPL, {"_global": {
            "titleFamily": "Lora", "fontFamily": "Poppins", "headingFamily": "Oswald",
            "group:title": "body", "group:heading": "font-title", "group:body": "disp",
        }}), "data-cms-typography")
        self.assertIn("--font-title: 'Lora', sans-serif;", css)
        self.assertNotIn("--font-title: var(--body)", css)
        self.assertIn(".h-title{font-family:var(--body)}", css)
        self.assertIn(".sec-h{font-family:var(--font-title)}", css)
        self.assertIn("--cms-font-body: var(--disp);", css)
        self.assertIn("--cms-font-heading: var(--font-title);", css)
        self.assertIn("--cms-font-title: var(--body);", css)

    def test_unset_title_preset_can_be_shared_with_heading(self):
        css = style_text(render_site(SITE_TPL, {"_global": {
            "group:heading": "font-title",
        }}), "data-cms-typography")
        self.assertIn("--font-title: var(--disp);", css)
        self.assertIn(".sec-h{font-family:var(--font-title)}", css)

    def test_functional_selector_commas_remain_in_one_rule(self):
        html = SITE_TPL.replace('</style>', ':where(h1.h-title,h1.other):not(.small){font-family:var(--body)}</style>')
        css = style_text(render_site(html, {"_global": {"titleFamily": "Lora"}}), "data-cms-typography")
        self.assertIn(':where(h1.h-title,h1.other):not(.small){font-family:var(--font-title)}', css)

    def test_nested_conditions_and_important_are_preserved(self):
        html = SITE_TPL.replace("</style>", """
@media(max-width:600px){@supports(display:grid){
  .mobile-title{font-family:var(--body)!important;font-size:4rem}
}}
</style>""")
        css = style_text(render_site(html, {"_global": {"titleFamily": "Lora"}}), "data-cms-typography")
        scoped = "@media (max-width:600px){@supports (display:grid){.mobile-title{font-family:var(--font-title)!important}}}"
        self.assertIn(scoped, css)
        self.assertNotIn("}.mobile-title{", css)

    def test_literal_and_inherited_titles_are_mapped(self):
        html = SITE_TPL.replace(".h-title{font-family:var(--disp);", ".h-title{font-family:Arial;")
        html = html.replace('</body>', '<h1>Another title</h1></body>')
        css = style_text(render_site(html, {"_global": {"titleFamily": "Lora"}}), "data-cms-typography")
        self.assertIn(".h-title{font-family:var(--font-title)}", css)
        self.assertIn("h1{font-family:var(--font-title)}", css)

    def test_framework_font_variables_and_keyframes_are_not_exposed(self):
        html = SITE_TPL.replace("--bg:#000;", "--bg:#000;--tw-font-sans:Arial;--font-mono:monospace;")
        html = html.replace('</style>', '.noise{font-family:var(--tw-font-sans)} @keyframes spin{from{font-family:var(--font-mono)}} </style>')
        found = typo(html)
        self.assertNotIn("tw-font-sans", [s["name"] for s in found["slots"]])
        self.assertNotIn("font-mono", [s["name"] for s in found["slots"]])
        self.assertFalse(any(".noise" in g["selectors"] for g in found["groups"]))


@override_settings(TENANT_BASE_DOMAIN="localhost", STORAGES=STORAGES)
class SaveTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.member = User.objects.create_user("bob", password="x")
        self.tenant = Tenant.objects.create(
            name="Site", subdomain="site", owner=self.member, is_published=True,
            template=Template.objects.create(
                name="Site", html_source=SITE_TPL, editing_mode=Template.EDITING_EDITABLE,
            ),
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.member)
        self.client = Client(HTTP_HOST="site.localhost")
        self.client.force_login(self.member)

    def test_slot_and_group_keys_are_validated(self):
        response = self.client.post(
            reverse("dashboard:tenant_save_self"),
            data=json.dumps({"content": {"_global": {
                "font:mono": "Roboto Mono<script>",
                "font:bad name!": "Lora",
                "group:heading": "disp",
                "group:labels": "x;}body{",
                "group:unknown": "disp",
            }}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(
            self.tenant.content["_global"],
            {"font:mono": "Roboto Monoscript", "group:heading": "disp"},
        )
        public = Client(HTTP_HOST="site.localhost").get("/").content.decode()
        self.assertIn(".shift-reveal{font-family:var(--disp)}", public)

    def test_block_site_reads_headings_from_block_markup(self):
        from core.models import BlockType

        shell = SITE_TPL.split("<body>")[0] + '<body><main data-region="main"></main></body></html>'
        template = Template.objects.create(
            name="Shell", html_source=shell, editing_mode=Template.EDITING_EDITABLE,
        )
        template.allowed_block_types.add(BlockType.objects.create(html_source=(
            '<section data-block="intro" data-label="Intro">'
            '<h2 class="sec-h" data-edit="intro.title" data-type="text">T</h2></section>'
        )))
        self.tenant.template = template
        self.tenant.content = {"regions": {"main": [
            {"id": "blk_00000001", "type": "intro", "fields": {}},
        ]}}
        self.tenant.save()
        editor = self.client.get("/dashboard/").content.decode()
        self.assertIn('data-global-bind="headingFamily"', editor)
        self.assertNotIn('data-global-bind="font:disp"', editor)
        self.assertIn('data-global-bind="font:mono"', editor)

    def test_editor_lists_extra_fonts_and_groups(self):
        editor = self.client.get("/dashboard/").content.decode()
        self.assertIn('data-global-bind="font:mono"', editor)
        self.assertIn('data-global-bind="group:heading"', editor)
        self.assertIn('data-global-bind="group:title"', editor)
        self.assertIn(">Title<", editor)
        self.assertIn(">Heading<", editor)
        self.assertIn(">Body<", editor)
        self.assertNotIn("Display text", editor)
        self.assertNotIn("Labels & tags", editor)
