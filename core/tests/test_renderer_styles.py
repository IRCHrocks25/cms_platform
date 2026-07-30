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
            "color": "#b91c1c", "bgColor": "#0a0a14", "fontSize": "56px",
            "fontFamily": "Poppins", "fontWeight": "700", "italic": True,
            "align": "center",
        })
        style = el.get("style", "")
        self.assertIn("color: #b91c1c;", style)
        self.assertIn("background-color: #0a0a14;", style)
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

    def test_color_reaches_styled_descendants(self):
        template = (
            "<html><head></head><body>"
            '<section data-section="hero">'
            '<h1 data-edit="hero.title" data-type="richtext">Hi <em>there</em></h1>'
            "</section></body></html>"
        )
        content = {"hero": {"title": "Hi <em>there</em>"},
                   "_styles": {"hero.title": {"color": "#b91c1c"}}}
        html = render_site(template, content)
        # element itself gets inline color, AND a scoped rule recolors descendants
        self.assertIn('[data-edit="hero.title"] * { color: #b91c1c !important; }', html)

    def test_unsafe_color_does_not_inject_css(self):
        template = (
            "<html><head></head><body>"
            '<section data-section="hero"><h1 data-edit="hero.title">x</h1></section>'
            "</body></html>"
        )
        content = {"hero": {"title": "x"},
                   "_styles": {"hero.title": {"color": "red;} body{display:none"}}}
        html = render_site(template, content)
        self.assertNotIn("display:none", html)


_RICHTEXT_TEMPLATE = (
    "<html><head></head><body>"
    '<section data-section="hero">'
    '<h1 data-edit="hero.title" data-type="richtext">Welcome</h1>'
    "</section></body></html>"
)


class RichtextSpanStyleTests(SimpleTestCase):
    """Selection-level styling is stored as a styled <span> inside the
    richtext field's own HTML value (not the whole-element _styles map), so a
    client can recolor part of a heading — e.g. a two-colour header. This
    locks the contract that such a span survives sanitize + phrasing-host
    flatten + render with its inline style and text intact."""

    def test_partial_color_span_survives_render(self):
        content = {"hero": {"title":
                            'Anxiety <span style="color: #e11d48">isn\'t</span> who you are'}}
        html = render_site(_RICHTEXT_TEMPLATE, content)
        soup = BeautifulSoup(html, "lxml")
        span = soup.select_one('[data-edit="hero.title"] span')
        self.assertIsNotNone(span, "styled span was stripped from the richtext value")
        self.assertIn("color: #e11d48", span.get("style", ""))
        self.assertEqual(span.get_text(), "isn't")
        # The rest of the heading keeps the host's own color (no forced override).
        self.assertEqual(soup.find(attrs={"data-edit": "hero.title"}).get_text(),
                         "Anxiety isn't who you are")

    def test_partial_color_not_forced_onto_siblings(self):
        # Unlike the whole-element _styles path, a richtext span must NOT emit a
        # scoped `[data-edit] * { color: … !important }` rule that would repaint
        # the untouched text.
        content = {"hero": {"title":
                            'Anxiety <span style="color: #e11d48">isn\'t</span> who you are'}}
        html = render_site(_RICHTEXT_TEMPLATE, content)
        self.assertNotIn("!important", html)

    def test_browser_rgb_color_span_survives_render(self):
        # execCommand('foreColor') emits rgb(), not hex — the value the editor
        # actually stores. It must survive the richtext sanitizer untouched.
        content = {"hero": {"title":
                            'Anxiety <span style="color: rgb(239, 68, 68)">isn\'t</span> who you are'}}
        html = render_site(_RICHTEXT_TEMPLATE, content)
        span = BeautifulSoup(html, "lxml").select_one('[data-edit="hero.title"] span')
        self.assertIsNotNone(span)
        self.assertIn("rgb(239, 68, 68)", span.get("style", ""))
        self.assertEqual(span.get_text(), "isn't")

    def test_font_size_span_survives_render(self):
        content = {"hero": {"title":
                            'Big <span style="font-size: 32px">word</span> here'}}
        html = render_site(_RICHTEXT_TEMPLATE, content)
        span = BeautifulSoup(html, "lxml").select_one('[data-edit="hero.title"] span')
        self.assertIsNotNone(span)
        self.assertIn("font-size: 32px", span.get("style", ""))

    def test_nested_style_spans_survive_render(self):
        # Stacking colour then bold on the same selection nests spans; both must
        # survive (colour + weight) with the text intact.
        content = {"hero": {"title":
                            'Anxiety <span style="color: rgb(225, 29, 72)">'
                            '<span style="font-weight: 700">isn\'t</span></span> who you are'}}
        html = render_site(_RICHTEXT_TEMPLATE, content)
        soup = BeautifulSoup(html, "lxml")
        outer = soup.select_one('[data-edit="hero.title"] span')
        inner = soup.select_one('[data-edit="hero.title"] span span')
        self.assertIsNotNone(inner)
        self.assertIn("rgb(225, 29, 72)", outer.get("style", ""))
        self.assertIn("font-weight: 700", inner.get("style", ""))
        self.assertEqual(inner.get_text(), "isn't")


class PreviewBridgeStyleTests(SimpleTestCase):
    def test_bridge_has_style_handlers(self):
        html = render_site(_TEMPLATE, {"hero": {"title": "Hi"}}, preview=True)
        self.assertIn("apply-styles", html)
        self.assertIn("apply-global", html)
        self.assertIn("cmsEnsureFont", html)
