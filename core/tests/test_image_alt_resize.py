"""Annotated images: alt text companion and bounded display width.

Clients resize a content image from the canvas or a numeric field and set alt
text without editing HTML. Width is a container percentage (10–100) stored in
``_styles``. Unedited images, header logos, and decorative chrome stay as the
template drew them.
"""
from types import SimpleNamespace

from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from core.models import BlockType, Template
from core.parser import build_block_schema, build_schema, clean_image_alt
from core.renderer import (
    bound_image_display_width,
    bound_image_style_widths,
    image_display_style_ids,
    render_page_from_blocks,
    render_site,
)
from dashboard.views import (
    _bound_image_widths_for_save,
    _normalize_regions,
    _normalize_styles,
    _sanitize_content_field_values,
)


PHOTO_HTML = """
<section data-section="hero" data-label="Hero">
  <img class="feature-icon" src="icon.svg" alt="" width="24" height="24">
  <img data-edit="hero.photo" data-type="image" data-label="Photo"
       src="hero.jpg" alt="Kitchen" width="1200" height="800"
       srcset="hero.jpg 1x">
</section>
"""

FEATURE_BLOCK = """
<section data-block="feature" data-label="Feature">
  <img class="feature-icon" src="icon.svg" alt="" width="24" height="24">
  <img data-edit="feature.photo" data-type="image" data-label="Photo"
       src="hero.jpg" alt="Kitchen" width="1200" height="800">
</section>
"""

SHELL_HTML = """
<html><body>
  <img class="site-header-logo" data-edit="nav.logo" data-type="image"
       data-label="Logo" src="logo.png" alt="Logo" width="200" height="40">
  <main data-region="main"></main>
</body></html>
"""


def _style(html: str, edit: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    el = soup.find(attrs={"data-edit": edit})
    return el.get("style") or ""


class ImageAltParserTests(SimpleTestCase):
    def test_image_exposes_alt_companion_and_keeps_src(self):
        schema = build_schema(PHOTO_HTML)
        hero = next(s for s in schema["sections"] if s["id"] == "hero")
        fields = {f["id"]: f for f in hero["fields"]}
        self.assertEqual(fields["hero.photo"]["type"], "image")
        self.assertEqual(fields["hero.photo"]["default"], "hero.jpg")
        self.assertFalse(fields["hero.photo"]["style_editable"])
        alt = fields["hero.photo_alt"]
        self.assertEqual(alt["type"], "text")
        self.assertEqual(alt["default"], "Kitchen")
        self.assertEqual(alt["applies_to"], "hero.photo")
        self.assertFalse(alt["style_editable"])
        self.assertEqual(schema["defaults"]["hero"]["photo_alt"], "Kitchen")

    def test_missing_alt_defaults_to_empty_string(self):
        html = (
            '<section data-section="hero">'
            '<img data-edit="hero.photo" data-type="image" src="a.jpg">'
            "</section>"
        )
        schema = build_schema(html)
        hero = schema["sections"][0]
        alt = next(f for f in hero["fields"] if f["id"] == "hero.photo_alt")
        self.assertEqual(alt["default"], "")

    def test_non_image_element_has_no_alt_companion(self):
        html = (
            '<section data-section="hero">'
            '<div data-edit="hero.photo" data-type="image">x</div>'
            "</section>"
        )
        ids = [f["id"] for f in build_schema(html)["sections"][0]["fields"]]
        self.assertEqual(ids, ["hero.photo"])

    def test_block_schema_uses_a_relative_alt_id(self):
        schema = build_block_schema(FEATURE_BLOCK)
        ids = [f["id"] for f in schema["fields"]]
        self.assertEqual(ids, ["photo", "photo_alt"])
        self.assertEqual(schema["defaults"]["photo_alt"], "Kitchen")
        self.assertEqual(schema["fields"][1]["applies_to"], "photo")

    def test_clean_image_alt_strips_script_and_control_chars(self):
        raw = "<script>alert(1)</script> Dining\nroom" + "x" * 400
        cleaned = clean_image_alt(raw)
        self.assertEqual(cleaned, ("Dining room" + "x" * 400)[:300])
        self.assertNotIn("<", cleaned)
        self.assertNotIn("alert", cleaned)


class ImageAltRenderTests(SimpleTestCase):
    def test_alt_is_applied_and_original_dimensions_stay(self):
        out = render_site(PHOTO_HTML, {"hero": {"photo_alt": "Dining room"}})
        img = BeautifulSoup(out, "lxml").find(attrs={"data-edit": "hero.photo"})
        self.assertEqual(img.get("alt"), "Dining room")
        self.assertEqual(img.get("src"), "hero.jpg")
        self.assertEqual(img.get("width"), "1200")
        self.assertEqual(img.get("height"), "800")
        self.assertIn("srcset", img.attrs)

    def test_unedited_image_keeps_default_markup(self):
        out = render_site(
            PHOTO_HTML,
            {"hero": {"photo": "hero.jpg", "photo_alt": "Kitchen"}},
        )
        img = BeautifulSoup(out, "lxml").find(attrs={"data-edit": "hero.photo"})
        self.assertEqual(img.get("alt"), "Kitchen")
        self.assertIn("srcset", img.attrs)
        self.assertNotIn("height:", img.get("style") or "")
        icon = BeautifulSoup(out, "lxml").find(class_="feature-icon")
        self.assertEqual(icon.get("style"), None)
        self.assertEqual(icon.get("width"), "24")

    def test_bounded_width_preserves_aspect_ratio_and_caps_the_column(self):
        out = render_site(
            PHOTO_HTML,
            {"hero": {"photo_alt": "Kitchen"}, "_styles": {"hero.photo": {"width": "60%"}}},
        )
        style = _style(out, "hero.photo")
        self.assertIn("width: 60%", style)
        self.assertIn("height: auto", style)
        self.assertIn("max-width: 100%", style)
        img = BeautifulSoup(out, "lxml").find(attrs={"data-edit": "hero.photo"})
        self.assertEqual(img.get("width"), "1200")
        self.assertEqual(img.get("height"), "800")

    def test_unsafe_and_unbounded_widths_are_not_applied(self):
        for raw in ("400px", "150%", "9%", "60%;color:red", "60%};position:fixed"):
            out = render_site(
                PHOTO_HTML,
                {"_styles": {"hero.photo": {"width": raw}}},
            )
            style = _style(out, "hero.photo")
            self.assertNotIn("width:", style, raw)
            self.assertNotIn("height:", style, raw)

    def test_header_logo_width_does_not_restyle_chrome(self):
        out = render_site(
            SHELL_HTML,
            {"_styles": {"nav.logo": {"width": "80%"}}},
        )
        logo = BeautifulSoup(out, "lxml").find(class_="site-header-logo")
        self.assertEqual(logo.get("alt"), "Logo")
        self.assertNotIn("width:", logo.get("style") or "")
        self.assertEqual(logo.get("height"), "40")

    def test_preview_ships_the_drag_handle_and_public_render_does_not(self):
        preview = render_site(PHOTO_HTML, {}, preview=True)
        public = render_site(PHOTO_HTML, {}, preview=False)
        self.assertIn("cms-image-resize", preview)
        self.assertIn("image-width", preview)
        self.assertIn("cmsResizeHandle.focus()", preview)
        self.assertIn('role', preview)
        self.assertNotIn("cms-image-resize", public)

    def test_block_shell_carries_alt_and_width_past_chrome(self):
        schema = build_block_schema(FEATURE_BLOCK)
        catalog = {"feature": {"schema": schema, "html": FEATURE_BLOCK, "label": "Feature"}}
        out = render_page_from_blocks(
            SHELL_HTML,
            {
                "regions": {"main": [{
                    "id": "blk_abcd1234",
                    "type": "feature",
                    "fields": {"photo_alt": "Dining room"},
                }]},
                "_styles": {"blk_abcd1234.photo": {"width": "55%"}},
            },
            catalog,
        )
        soup = BeautifulSoup(out, "lxml")
        photo = soup.find(attrs={"data-edit": "blk_abcd1234.photo"})
        self.assertEqual(photo.get("alt"), "Dining room")
        self.assertIn("width: 55%", photo.get("style") or "")
        self.assertIn("height: auto", photo.get("style") or "")
        self.assertIn("max-width: 100%", photo.get("style") or "")
        self.assertEqual(photo.get("width"), "1200")
        icon = soup.find(class_="feature-icon")
        self.assertIsNone(icon.get("style"))
        logo = soup.find(class_="site-header-logo")
        self.assertEqual(logo.get("alt"), "Logo")
        self.assertNotIn("height: auto", logo.get("style") or "")


class ImageWidthSaveTests(SimpleTestCase):
    def test_bounds_percent_and_rejects_the_rest(self):
        self.assertEqual(bound_image_display_width("60%"), "60%")
        self.assertEqual(bound_image_display_width(" 100% "), "100%")
        self.assertIsNone(bound_image_display_width("9%"))
        self.assertIsNone(bound_image_display_width("150%"))
        self.assertIsNone(bound_image_display_width("400px"))
        self.assertIsNone(bound_image_display_width("60%;color:red"))

    def test_save_drops_unsafe_image_widths_and_keeps_text_widths(self):
        content = {"_styles": {
            "hero.photo": {"width": "60%;color:red"},
            "hero.title": {"width": "320px"},
        }}
        _normalize_styles(content)
        self.assertNotIn("hero.photo", content["_styles"])
        self.assertEqual(content["_styles"]["hero.title"]["width"], "320px")

        content = {"_styles": {
            "hero.photo": {"width": "150%"},
            "hero.title": {"width": "80%"},
            "blk_abcd1234.__block": {"width": "100%"},
        }}
        _normalize_styles(content)
        template = SimpleNamespace(html_source=PHOTO_HTML, is_block_shell=False)
        _bound_image_widths_for_save(content, template)
        self.assertNotIn("hero.photo", content["_styles"])
        self.assertEqual(content["_styles"]["hero.title"]["width"], "80%")
        self.assertEqual(content["_styles"]["blk_abcd1234.__block"]["width"], "100%")

    def test_save_keeps_a_bounded_image_width(self):
        content = {"_styles": {"hero.photo": {"width": "40%"}}}
        _normalize_styles(content)
        template = SimpleNamespace(html_source=PHOTO_HTML, is_block_shell=False)
        _bound_image_widths_for_save(content, template)
        self.assertEqual(content["_styles"]["hero.photo"]["width"], "40%")

    def test_block_instance_width_is_bounded_from_the_catalog(self):
        schema = build_block_schema(FEATURE_BLOCK)
        content = {
            "regions": {"main": [{
                "id": "blk_abcd1234",
                "type": "feature",
                "fields": {},
            }]},
            "_styles": {
                "blk_abcd1234.photo": {"width": "400px"},
                "blk_abcd1234.__block": {"width": "100%"},
            },
        }
        catalog = {"feature": {"schema": schema}}
        ids = image_display_style_ids({"sections": []}, content, catalog)
        self.assertIn("blk_abcd1234.photo", ids)
        self.assertNotIn("blk_abcd1234.__block", ids)
        bound_image_style_widths(content, ids)
        self.assertNotIn("blk_abcd1234.photo", content["_styles"])
        self.assertEqual(content["_styles"]["blk_abcd1234.__block"]["width"], "100%")

    def test_alt_companion_is_sanitized_on_save(self):
        schema = build_schema(PHOTO_HTML)
        content = {"hero": {"photo_alt": "<script>alert(1)</script> Dining\nroom"}}
        _sanitize_content_field_values(content, schema)
        self.assertEqual(content["hero"]["photo_alt"], "Dining room")


class ImageAltBlockSaveTests(TestCase):
    def test_block_instance_alt_is_cleaned_and_unknown_keys_drop(self):
        block = BlockType.objects.create(
            key="feature", html_source=FEATURE_BLOCK, label="Feature",
        )
        template = Template.objects.create(name="Shell", html_source=SHELL_HTML)
        template.allowed_block_types.add(block)
        content = {"regions": {"main": [{
            "id": "blk_abcd1234",
            "type": "feature",
            "fields": {
                "photo_alt": "<img src=x onerror=alert(1)> Dining room",
                "not_a_field": "nope",
            },
        }]}}
        _normalize_regions(content, template)
        fields = content["regions"]["main"][0]["fields"]
        self.assertEqual(fields["photo_alt"], "Dining room")
        self.assertNotIn("not_a_field", fields)


class ImageWidthFieldTemplateTests(SimpleTestCase):
    def test_image_field_has_a_numeric_width_control(self):
        html = render_to_string(
            "dashboard/components/field.html",
            {"field": {
                "id": "hero.photo",
                "type": "image",
                "label": "Photo",
                "style_editable": False,
            }},
        )
        self.assertIn('data-bind-image-width="hero.photo"', html)
        self.assertIn('type="number"', html)
        self.assertIn('min="10"', html)
        self.assertIn('max="100"', html)
        self.assertIn("Arrow keys change the width", html)
        self.assertNotIn("data-style-panel", html)
