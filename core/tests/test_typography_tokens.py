"""CMS-74: semantic typography tokens (discovery, save, render, blog)."""
import json

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BlogPost, Template, Tenant, TenantMembership
from core.parser import build_schema
from core.renderer import render_site
from core.typography import detect_typography_tokens

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Shaped like the IAM Marketable template: fonts live in custom properties that
# class rules consume, next to framework noise and a color palette.
IAM_TPL = """<!doctype html><html><head>
<style data-tokens>:root{--primary:#FF2E88;--text:#FFFFFF}</style>
<style>
:root{--bg:#000;--tw-ring:0 0 #0000;--pk:#FF2E88;
  --disp:'Bebas Neue',sans-serif;--body:'Inter',sans-serif;--mono:'Inter',sans-serif;}
body{font-family:var(--body);background:var(--bg);color:var(--text)}
.h-title{font-family:var(--disp);font-size:clamp(2.2rem,5vw,4.6rem)}
.sec-h{font-family:var(--disp)}
.btn{font-family:var(--body);background:var(--pk)}
.tag{font-family:var(--mono)}
</style></head><body>
<section data-section="hero"><h1 class="h-title" data-edit="hero.title">Hi</h1>
<a class="btn" data-edit="hero.cta" data-type="link" href="#about">Go</a><span class="tag">x</span></section>
<section data-section="about" id="about"><h2 class="sec-h" data-edit="about.title">About</h2>
<p data-edit="about.body">Body copy</p></section>
</body></html>"""

CONTRACT_TPL = """<!doctype html><html><head>
<style data-tokens>:root{--primary:#b91c1c;--font-body:'Lato',sans-serif;
  --font-heading:"Playfair Display",serif;--text-body:17px;--text-heading:48px}</style>
<style>body{font-family:var(--font-body);font-size:var(--text-body)}
.title{font-family:var(--font-heading);font-size:var(--text-heading)}
.btn{background:var(--primary)}</style>
</head><body>
<section data-section="hero"><h1 class="title" data-edit="hero.title">Hi</h1></section>
</body></html>"""

PLAIN_TPL = """<!doctype html><html><head><style>body{font-family:Arial}</style></head><body>
<section data-section="hero"><h1 data-edit="hero.title">Hi</h1></section>
</body></html>"""


def tokens(html):
    return {t["role"]: t for t in detect_typography_tokens(BeautifulSoup(html, "lxml"))}


def style_text(html, attr):
    tag = BeautifulSoup(html, "lxml").find("style", attrs={attr: True})
    return tag.string if tag is not None else None


class DiscoveryTests(SimpleTestCase):
    def test_contract_names_fill_every_role(self):
        found = tokens(CONTRACT_TPL)
        self.assertEqual(
            {role: t["name"] for role, t in found.items()},
            {
                "body-font": "font-body", "heading-font": "font-heading",
                "body-size": "text-body", "heading-size": "text-heading",
            },
        )
        self.assertEqual(found["heading-font"]["label"], "Heading font")
        self.assertEqual(found["body-size"]["value"], "17px")

    def test_infers_roles_from_usage(self):
        found = tokens(IAM_TPL)
        self.assertEqual(found["body-font"]["name"], "body")
        self.assertEqual(found["heading-font"]["name"], "disp")
        self.assertEqual(found["heading-font"]["value"].replace('"', "'"), "'Bebas Neue',sans-serif")
        self.assertNotIn("body-size", found)
        self.assertNotIn("heading-size", found)

    def test_ignores_framework_and_unused_variables(self):
        names = {t["name"] for t in tokens(IAM_TPL).values()}
        self.assertNotIn("mono", names)
        self.assertNotIn("tw-ring", names)
        self.assertNotIn("bg", names)

    def test_rejects_unsafe_or_non_font_values(self):
        html = """<html><head><style>
:root{--font-body:var(--x);--font-heading:url(evil.css);--text-body:calc(1rem + 2px);
  --text-heading:red}
</style></head><body><h1>x</h1></body></html>"""
        self.assertEqual(tokens(html), {})

    def test_plain_template_has_none(self):
        self.assertEqual(tokens(PLAIN_TPL), {})
        self.assertEqual(build_schema(PLAIN_TPL)["typography_tokens"], [])

    def test_schema_lists_tokens_and_brand_drops_them(self):
        schema = build_schema(CONTRACT_TPL)
        roles = [t["role"] for t in schema["typography_tokens"]]
        self.assertEqual(roles, ["body-font", "heading-font", "body-size", "heading-size"])
        brand = next(s for s in schema["sections"] if s["id"] == "brand")
        self.assertEqual([f["id"] for f in brand["fields"]], ["brand.primary"])

    def test_color_tokens_unchanged(self):
        names = {t["name"] for t in build_schema(IAM_TPL)["theme_tokens"]}
        self.assertIn("pk", names)
        self.assertIn("bg", names)


class AnnotatorPipelineTests(SimpleTestCase):
    def test_restored_styles_keep_typography_out_of_brand(self):
        from core.services.annotator import _restore_blocks, _strip_blocks

        raw = IAM_TPL.replace("<style data-tokens>", "<style>")
        slim, blocks = _strip_blocks(raw)
        self.assertNotIn("Bebas Neue", slim)
        restored = _restore_blocks(slim, blocks)
        self.assertIn("<style data-tokens", restored)

        schema = build_schema(restored)
        found = {t["role"]: t["name"] for t in schema["typography_tokens"]}
        self.assertEqual(found, {"body-font": "body", "heading-font": "disp"})
        brand_ids = {
            f["id"] for s in schema["sections"] if s["id"] == "brand" for f in s["fields"]
        }
        self.assertTrue(brand_ids)
        self.assertFalse(brand_ids & {"brand.body", "brand.disp"})


class RenderTests(SimpleTestCase):
    def test_inferred_tokens_get_overrides_and_contract_aliases(self):
        html = render_site(IAM_TPL, {
            "hero": {"title": "Hi"},
            "_global": {"fontFamily": "Poppins", "headingFamily": "Oswald"},
        })
        css = style_text(html, "data-cms-typography")
        self.assertIn("--body: 'Poppins', sans-serif;", css)
        self.assertIn("--disp: 'Oswald', sans-serif;", css)
        self.assertIn("--font-body: var(--body);", css)
        self.assertIn("--font-heading: var(--disp);", css)
        # The token now reaches every use, so the element-level rules are skipped.
        self.assertIsNone(style_text(html, "data-cms-global"))
        self.assertIn("family=Poppins", html)
        self.assertIn("family=Oswald", html)

    def test_aliases_without_saved_values(self):
        css = style_text(render_site(IAM_TPL, {}), "data-cms-typography")
        self.assertIsNone(css)

    def test_contract_sizes_override_tokens(self):
        html = render_site(CONTRACT_TPL, {
            "_global": {"baseSize": "18px", "headingSize": "40px", "textColor": "#111111"},
        })
        css = style_text(html, "data-cms-typography")
        self.assertEqual(css, ":root{--text-body: 18px;--text-heading: 40px;}")
        self.assertEqual(style_text(html, "data-cms-global"), "body{color: #111111;}")

    def test_unsafe_sizes_never_reach_css(self):
        html = render_site(CONTRACT_TPL, {
            "_global": {"headingSize": "40px;}body{display:none", "baseSize": "huge"},
        })
        self.assertIsNone(style_text(html, "data-cms-typography"))
        self.assertNotIn("display:none", html)

    def test_template_without_tokens_keeps_element_rules(self):
        html = render_site(PLAIN_TPL, {
            "_global": {"fontFamily": "Poppins", "baseSize": "18px", "headingFamily": "Lora"},
        })
        self.assertEqual(
            style_text(html, "data-cms-global"),
            "body{font-family: Poppins; font-size: 18px;}h1,h2,h3,h4,h5,h6{font-family: Lora;}",
        )
        self.assertIsNone(style_text(html, "data-cms-typography"))

    def test_color_token_overrides_still_apply(self):
        html = render_site(IAM_TPL, {"_tokens": {"pk": "#22c55e"}})
        self.assertIn("--pk: #22c55e;", style_text(html, "data-cms-tokens"))

    def test_preview_exposes_role_map_to_bridge(self):
        html = render_site(IAM_TPL, {}, preview=True)
        tag = BeautifulSoup(html, "lxml").find("script", id="cms-typography")
        self.assertEqual(json.loads(tag.string)["roles"], {
            "body-font": {"name": "body", "generic": "sans-serif"},
            "heading-font": {"name": "disp", "generic": "sans-serif"},
        })
        self.assertIsNone(
            BeautifulSoup(render_site(IAM_TPL, {}), "lxml").find("script", id="cms-typography")
        )


class BlogTemplateTests(SimpleTestCase):
    def test_blog_fonts_read_the_contract_with_current_fallbacks(self):
        from pathlib import Path
        from django.conf import settings

        css = (Path(settings.BASE_DIR) / "templates" / "blog" / "_content.html").read_text("utf-8")
        self.assertIn("--blog-sans:var(--cms-font-body,'Roboto',", css)
        self.assertIn("--blog-serif:var(--cms-font-heading,'Fraunces',", css)


@override_settings(TENANT_BASE_DOMAIN="localhost", STORAGES=STORAGES)
class SaveAndPublicRenderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.member = User.objects.create_user("alice", password="x")
        self.tenant = Tenant.objects.create(
            name="IAM", subdomain="iam", owner=self.member, is_published=True,
            template=Template.objects.create(
                name="IAM", html_source=IAM_TPL, editing_mode=Template.EDITING_EDITABLE,
            ),
        )
        TenantMembership.objects.create(tenant=self.tenant, user=self.member)
        self.client = Client(HTTP_HOST="iam.localhost")
        self.client.force_login(self.member)

    def save(self, global_styles):
        response = self.client.post(
            reverse("dashboard:tenant_save_self"),
            data=json.dumps({"content": {"_global": global_styles}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.tenant.refresh_from_db()

    def test_saved_fonts_match_between_preview_and_public_site(self):
        self.save({"fontFamily": "Poppins", "headingFamily": "Oswald", "headingSize": "big;}"})
        self.assertEqual(
            self.tenant.content["_global"], {"fontFamily": "Poppins", "headingFamily": "Oswald"},
        )
        preview = self.client.get(reverse("dashboard:tenant_preview_self")).content.decode()
        public = Client(HTTP_HOST="iam.localhost").get("/").content.decode()
        for html in (preview, public):
            css = style_text(html, "data-cms-typography")
            self.assertIn("--body: 'Poppins', sans-serif;", css)
            self.assertIn("--disp: 'Oswald', sans-serif;", css)

    def test_blog_article_uses_site_fonts(self):
        self.save({"headingFamily": "Oswald"})
        post = BlogPost.objects.create(
            tenant=self.tenant, title="Why more customers", status=BlogPost.STATUS_PUBLISHED,
            publish_date=timezone.now(), body="<h2>Point</h2><p>Text</p>",
        )
        html = Client(HTTP_HOST="iam.localhost").get(f"/blog/{post.slug}/").content.decode()
        self.assertIn("--disp: 'Oswald', sans-serif;", html)
        self.assertIn("--font-heading: var(--disp);", html)
        self.assertIn("--cms-font-heading: var(--disp);", html)
        self.assertIn("var(--cms-font-heading,'Fraunces',", html)

    def test_editor_shows_heading_size_only_when_template_has_it(self):
        editor = self.client.get("/dashboard/").content.decode()
        self.assertNotIn('data-global-bind="headingSize"', editor)
        # Opening the editor may clone/convert an owned template. Update the
        # tenant's current template rather than the stale setup reference.
        self.tenant.refresh_from_db()
        self.tenant.template.html_source = CONTRACT_TPL
        self.tenant.template.save()
        editor = self.client.get("/dashboard/").content.decode()
        self.assertIn('data-global-bind="headingSize"', editor)
