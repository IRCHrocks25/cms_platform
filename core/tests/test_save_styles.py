"""Tests for _styles / _global normalization on save."""
from django.test import SimpleTestCase

from dashboard.views import _normalize_styles


class NormalizeStylesTests(SimpleTestCase):
    def test_keeps_allowed_style_keys_and_drops_others(self):
        content = {"_styles": {"hero.title": {
            "color": "#b91c1c", "bgColor": "#ffffff", "fontSize": "56px",
            "fontFamily": "Poppins", "fontWeight": "700", "italic": True,
            "align": "center", "evil": "x", "onclick": "alert(1)",
        }}}
        _normalize_styles(content)
        style = content["_styles"]["hero.title"]
        self.assertEqual(set(style), {
            "color", "bgColor", "fontSize", "fontFamily", "fontWeight", "italic", "align"})
        self.assertTrue(style["italic"])

    def test_keeps_typography_style_keys(self):
        content = {"_styles": {"hero.title": {
            "lineHeight": "1.5", "letterSpacing": "0.05em",
            "textTransform": "uppercase",
        }}}
        _normalize_styles(content)
        self.assertEqual(
            set(content["_styles"]["hero.title"]),
            {"lineHeight", "letterSpacing", "textTransform"},
        )

    def test_drops_non_dotted_and_non_dict_entries(self):
        content = {"_styles": {"nodot": {"color": "#000000"}, "a.b": "notadict"}}
        _normalize_styles(content)
        self.assertEqual(content["_styles"], {})

    def test_truncates_long_values(self):
        content = {"_styles": {"a.b": {"fontFamily": "x" * 300}}}
        _normalize_styles(content)
        self.assertEqual(len(content["_styles"]["a.b"]["fontFamily"]), 120)

    def test_normalizes_global(self):
        content = {"_global": {
            "fontFamily": "Inter", "baseSize": "16px",
            "headingFamily": "Poppins", "textColor": "#1f2937",
            "pageBg": "#ffffff", "junk": "no"}}
        _normalize_styles(content)
        self.assertEqual(set(content["_global"]), {
            "fontFamily", "baseSize", "headingFamily", "textColor", "pageBg"})

    def test_missing_namespaces_are_untouched(self):
        content = {"hero": {"title": "x"}}
        _normalize_styles(content)
        self.assertEqual(content, {"hero": {"title": "x"}})

    def test_global_textcolor_breakout_is_dropped(self):
        content = {"_global": {"textColor": "red;} body{display:none !important"}}
        _normalize_styles(content)
        self.assertNotIn("textColor", content["_global"])

    def test_style_fontsize_injection_is_dropped(self):
        content = {"_styles": {"hero.title": {"fontSize": "16px;position:fixed"}}}
        _normalize_styles(content)
        self.assertEqual(content["_styles"], {})

    def test_keeps_layout_and_background_keys(self):
        content = {"_styles": {"blk_abcd1234.__block": {
            "padding": "32px", "maxWidth": "960px", "minHeight": "280px",
            "borderRadius": "16px", "bgMode": "image",
            "bgImage": "https://cdn.example.com/hero.jpg",
            "bgGradient": "180deg,#111111,#2563eb",
            "bgOverlay": "40", "bgSize": "cover",
            "bgOpacity": "60", "bgBlur": "8",
        }}}
        _normalize_styles(content)
        style = content["_styles"]["blk_abcd1234.__block"]
        self.assertEqual(style["bgImage"], "https://cdn.example.com/hero.jpg")
        self.assertEqual(style["bgGradient"], "180deg,#111111,#2563eb")
        self.assertEqual(style["padding"], "32px")
        self.assertEqual(style["bgMode"], "image")
        self.assertEqual(style["bgOpacity"], "60")
        self.assertEqual(style["bgBlur"], "8")

    def test_keeps_region_container_background(self):
        content = {"_styles": {"blk_abcd1234.__region.col1": {
            "bgMode": "image",
            "bgImage": "https://cdn.example.com/col.jpg",
        }}}
        _normalize_styles(content)
        style = content["_styles"]["blk_abcd1234.__region.col1"]
        self.assertEqual(style["bgImage"], "https://cdn.example.com/col.jpg")
        self.assertEqual(style["bgMode"], "image")

    def test_drops_invalid_region_style_target(self):
        content = {"_styles": {"blk_abcd1234.__region.<script>": {
            "bgColor": "#111111",
        }}}
        _normalize_styles(content)
        self.assertEqual(content["_styles"], {})

    def test_drops_invalid_background_opacity_and_blur(self):
        content = {"_styles": {"hero.box": {
            "bgOpacity": "999", "bgBlur": "-1",
        }}}
        _normalize_styles(content)
        style = content["_styles"].get("hero.box", {})
        self.assertNotIn("bgOpacity", style)
        self.assertNotIn("bgBlur", style)

    def test_drops_javascript_background_image(self):
        content = {"_styles": {"hero.box": {
            "bgImage": "javascript:alert(1)", "bgMode": "image",
        }}}
        _normalize_styles(content)
        style = content["_styles"].get("hero.box", {})
        self.assertNotIn("bgImage", style)

    def test_normalizes_header_place(self):
        content = {"_header": {
            "place": {"brand": "center", "nav": "nope", "actions": "right", "evil": "x"},
            "onclick": "alert(1)",
        }}
        _normalize_styles(content)
        self.assertEqual(content["_header"]["layout"], "centered")
        self.assertEqual(content["_header"]["menu"], [])
        self.assertEqual(content["_header"]["button"], {
            "on": False, "label": "Get Started", "href": "#",
        })
        self.assertEqual(content["_header"]["logo"], "")
        self.assertEqual(content["_header"]["logo_size"], 40)
        self.assertTrue(content["_header"]["show_name"])
        self.assertNotIn("onclick", content["_header"])
        self.assertNotIn("place", content["_header"])
