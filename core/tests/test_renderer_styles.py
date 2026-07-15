"""Tests for per-element and global editable styles."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.renderer import (
    _apply_element_styles,
    _apply_global_styles,
    _apply_styles,
    render_site,
)


def _el(html):
    return BeautifulSoup(html, "lxml").find(attrs={"data-edit": True})


class ApplyElementStylesTests(SimpleTestCase):
    def test_maps_each_property_to_css(self):
        el = _el('<h1 data-edit="hero.title">Hi</h1>')
        _apply_element_styles(el, {
            "color": "#b91c1c", "fontSize": "56px", "fontFamily": "Poppins",
            "fontWeight": "700", "italic": True, "align": "center",
        })
        style = el.get("style", "")
        self.assertIn("color: #b91c1c;", style)
        self.assertIn("font-size: 56px;", style)
        self.assertIn("font-family: Poppins;", style)
        self.assertIn("font-weight: 700;", style)
        self.assertIn("font-style: italic;", style)
        self.assertIn("text-align: center;", style)

    def test_italic_false_omits_font_style(self):
        el = _el('<p data-edit="a.b">x</p>')
        _apply_element_styles(el, {"italic": False, "color": "#000000"})
        self.assertNotIn("font-style", el.get("style", ""))

    def test_empty_values_skipped(self):
        el = _el('<p data-edit="a.b">x</p>')
        _apply_element_styles(el, {"color": "", "fontSize": None, "align": "left"})
        style = el.get("style", "")
        self.assertNotIn("color", style)
        self.assertNotIn("font-size", style)
        self.assertIn("text-align: left;", style)

    def test_reapply_replaces_not_appends(self):
        el = _el('<p data-edit="a.b" style="color: red;">x</p>')
        _apply_element_styles(el, {"color": "#111111"})
        self.assertEqual(el.get("style", "").count("color"), 1)
        self.assertIn("color: #111111;", el.get("style", ""))

    def test_apply_styles_targets_by_data_edit(self):
        soup = BeautifulSoup(
            '<body><h1 data-edit="hero.title">Hi</h1>'
            '<p data-edit="hero.body">B</p></body>', "lxml")
        _apply_styles(soup, {"hero.title": {"color": "#abcabc"}})
        self.assertIn("color: #abcabc;", soup.find(attrs={"data-edit": "hero.title"}).get("style", ""))
        self.assertEqual(soup.find(attrs={"data-edit": "hero.body"}).get("style", ""), "")


class ApplyGlobalStylesTests(SimpleTestCase):
    def _render(self, global_styles):
        soup = BeautifulSoup("<html><head></head><body><h1>H</h1></body></html>", "lxml")
        _apply_global_styles(soup, global_styles)
        return soup

    def test_injects_body_and_heading_rules(self):
        soup = self._render({
            "fontFamily": "Inter", "baseSize": "16px",
            "headingFamily": "Poppins", "textColor": "#1f2937",
        })
        block = soup.find("style", attrs={"data-cms-global": True})
        self.assertIsNotNone(block)
        css = block.string
        self.assertIn("font-family: Inter", css)
        self.assertIn("font-size: 16px", css)
        self.assertIn("color: #1f2937", css)
        self.assertIn("Poppins", css)
        self.assertIn("h1", css)

    def test_empty_global_injects_nothing(self):
        soup = self._render({})
        self.assertIsNone(soup.find("style", attrs={"data-cms-global": True}))

    def test_partial_global_only_sets_provided(self):
        soup = self._render({"textColor": "#123456"})
        css = soup.find("style", attrs={"data-cms-global": True}).string
        self.assertIn("color: #123456", css)
        self.assertNotIn("font-size", css)

    def test_page_background_sets_body_background(self):
        soup = self._render({"pageBg": "#fef3c7"})
        css = soup.find("style", attrs={"data-cms-global": True}).string
        self.assertIn("background-color: #fef3c7", css)


_TEMPLATE = (
    "<html><head></head><body>"
    '<section data-section="hero"><h1 data-edit="hero.title" data-type="text">Hi</h1></section>'
    "</body></html>"
)


class RenderSiteStylesTests(SimpleTestCase):
    def test_round_trip_applies_inline_and_global_and_font(self):
        content = {
            "hero": {"title": "Welcome"},
            "_styles": {"hero.title": {"color": "#b91c1c", "fontSize": "56px",
                                       "fontFamily": "Poppins"}},
            "_global": {"fontFamily": "Inter", "textColor": "#1f2937"},
        }
        html = render_site(_TEMPLATE, content)
        soup = BeautifulSoup(html, "lxml")
        h1 = soup.find(attrs={"data-edit": "hero.title"})
        self.assertIn("color: #b91c1c;", h1.get("style", ""))
        self.assertIn("font-size: 56px;", h1.get("style", ""))
        self.assertEqual(h1.get_text(), "Welcome")
        self.assertIsNotNone(soup.find("style", attrs={"data-cms-global": True}))
        self.assertTrue(soup.find_all("link", href=lambda h: h and "fonts.googleapis.com" in h))

    def test_no_style_namespaces_is_noop(self):
        html = render_site(_TEMPLATE, {"hero": {"title": "Hi"}})
        soup = BeautifulSoup(html, "lxml")
        self.assertIsNone(soup.find("style", attrs={"data-cms-global": True}))
        self.assertFalse(soup.find_all("link", href=lambda h: h and "fonts.googleapis.com" in h))


class PreviewBridgeStyleTests(SimpleTestCase):
    def test_bridge_has_style_handlers(self):
        html = render_site(_TEMPLATE, {"hero": {"title": "Hi"}}, preview=True)
        self.assertIn("apply-styles", html)
        self.assertIn("apply-global", html)
        self.assertIn("cmsEnsureFont", html)
