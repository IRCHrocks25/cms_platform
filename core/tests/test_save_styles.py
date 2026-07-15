"""Tests for _styles / _global normalization on save."""
from django.test import SimpleTestCase

from dashboard.views import _normalize_styles


class NormalizeStylesTests(SimpleTestCase):
    def test_keeps_allowed_style_keys_and_drops_others(self):
        content = {"_styles": {"hero.title": {
            "color": "#b91c1c", "fontSize": "56px", "fontFamily": "Poppins",
            "fontWeight": "700", "italic": True, "align": "center",
            "evil": "x", "onclick": "alert(1)",
        }}}
        _normalize_styles(content)
        style = content["_styles"]["hero.title"]
        self.assertEqual(set(style), {
            "color", "fontSize", "fontFamily", "fontWeight", "italic", "align"})
        self.assertTrue(style["italic"])

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
