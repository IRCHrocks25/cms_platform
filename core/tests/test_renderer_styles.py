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

    def test_typography_properties_mapped(self):
        el = _el('<p data-edit="a.b">x</p>')
        _apply_element_styles(el, {
            "lineHeight": "1.5", "letterSpacing": "0.05em",
            "textTransform": "uppercase",
        })
        style = el.get("style", "")
        self.assertIn("line-height: 1.5;", style)
        self.assertIn("letter-spacing: 0.05em;", style)
        self.assertIn("text-transform: uppercase;", style)

    def test_unsafe_typography_value_is_skipped(self):
        el = _el('<p data-edit="a.b">x</p>')
        _apply_element_styles(el, {"lineHeight": "1;} body{display:none"})
        self.assertNotIn("line-height", el.get("style", ""))
        self.assertNotIn("display:none", el.get("style", ""))

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

    def test_layout_properties_mapped(self):
        el = _el('<section data-edit="hero.box">x</section>')
        _apply_element_styles(el, {
            "padding": "32px", "maxWidth": "960px", "minHeight": "280px",
            "borderRadius": "16px",
        })
        style = el.get("style", "")
        self.assertIn("padding: 32px;", style)
        self.assertIn("max-width: 960px;", style)
        self.assertIn("min-height: 280px;", style)
        self.assertIn("border-radius: 16px;", style)
        self.assertIn("box-sizing: border-box;", style)
        self.assertIn("margin-left: auto;", style)

    def test_background_image_and_overlay(self):
        el = _el('<section data-edit="hero.box">x</section>')
        _apply_element_styles(el, {
            "bgMode": "image",
            "bgImage": "https://cdn.example.com/hero.jpg",
            "bgOverlay": "40",
            "bgSize": "cover",
        })
        style = el.get("style", "")
        self.assertIn("background-image:", style)
        self.assertIn("url(\"https://cdn.example.com/hero.jpg\")", style)
        self.assertIn("rgba(0,0,0,0.40)", style)
        self.assertIn("background-size: cover;", style)
        self.assertIn("min-height: 220px;", style)
        self.assertIn("padding: 32px 20px;", style)
        self.assertIsNone(el.find("div", class_="cms-bg-fx"))

    def test_background_image_grows_text_line_into_cell(self):
        el = _el(
            '<div data-edit="counter.n1Value" style="font-size:2.5rem;">200+</div>'
        )
        _apply_element_styles(el, {
            "bgMode": "image",
            "bgImage": "https://cdn.example.com/metric.jpg",
        })
        style = el.get("style", "")
        self.assertIn("min-height: 220px;", style)
        self.assertIn("padding: 32px 20px;", style)
        self.assertIn("width: 100%;", style)
        self.assertIn("background-size: cover;", style)

    def test_background_opacity_uses_fx_layer(self):
        soup = BeautifulSoup('<html><head></head><body><section data-edit="hero.box">Hello</section></body></html>', "lxml")
        el = soup.find(attrs={"data-edit": True})
        _apply_element_styles(el, {
            "bgMode": "image",
            "bgImage": "https://cdn.example.com/hero.jpg",
            "bgOpacity": "60",
            "bgSize": "cover",
        })
        fx = el.find("div", class_="cms-bg-fx")
        self.assertIsNotNone(fx)
        self.assertIn("cms-bg-fx-host", el.get("class") or [])
        self.assertIn('url("https://cdn.example.com/hero.jpg")', fx.get("style", ""))
        self.assertIn("opacity: 0.60", fx.get("style", ""))
        self.assertNotIn("background-image", el.get("style", "") or "")
        self.assertIsNotNone(soup.find("style", attrs={"data-cms-bg-fx": True}))

    def test_background_blur_clips_photo_only(self):
        soup = BeautifulSoup('<html><head></head><body><section data-edit="hero.box">Hello</section></body></html>', "lxml")
        el = soup.find(attrs={"data-edit": True})
        _apply_element_styles(el, {
            "bgMode": "image",
            "bgImage": "https://cdn.example.com/hero.jpg",
            "bgBlur": "8",
        })
        fx = el.find("div", class_="cms-bg-fx")
        self.assertIsNotNone(fx)
        self.assertIn("cms-bg-fx-clip", el.get("class") or [])
        self.assertIn("blur(8px)", fx.get("style", ""))
        self.assertNotIn("filter:", el.get("style", "") or "")

    def test_background_gradient(self):
        el = _el('<section data-edit="hero.box">x</section>')
        _apply_element_styles(el, {
            "bgMode": "gradient",
            "bgGradient": "180deg,#111111,#2563eb",
        })
        self.assertIn("linear-gradient(180deg, #111111, #2563eb)", el.get("style", ""))

    def test_javascript_background_image_is_skipped(self):
        el = _el('<section data-edit="hero.box">x</section>')
        _apply_element_styles(el, {
            "bgMode": "image",
            "bgImage": "javascript:alert(1)",
        })
        self.assertNotIn("background-image", el.get("style", ""))

    def test_block_style_targets_instance_wrapper(self):
        soup = BeautifulSoup(
            '<body><section data-instance-id="blk_abcd1234"><h1 data-edit="blk_abcd1234.title">Hi</h1></section></body>',
            "lxml",
        )
        _apply_styles(soup, {"blk_abcd1234.__block": {"padding": "24px", "bgColor": "#0a0a14"}})
        wrap = soup.find(attrs={"data-instance-id": "blk_abcd1234"})
        self.assertIn("padding: 24px;", wrap.get("style", ""))
        self.assertIn("background-color: #0a0a14;", wrap.get("style", ""))
        self.assertEqual(soup.find(attrs={"data-edit": "blk_abcd1234.title"}).get("style", ""), "")

    def test_region_style_targets_direct_child_container(self):
        soup = BeautifulSoup(
            '<body><section data-instance-id="blk_abcd1234">'
            '<div data-region="col1">A</div>'
            '<div data-region="col2">B</div>'
            "</section></body>",
            "lxml",
        )
        _apply_styles(soup, {
            "blk_abcd1234.__region.col1": {
                "bgMode": "image",
                "bgImage": "https://cdn.example.com/col.jpg",
            }
        })
        col1 = soup.find(attrs={"data-region": "col1"})
        col2 = soup.find(attrs={"data-region": "col2"})
        wrap = soup.find(attrs={"data-instance-id": "blk_abcd1234"})
        self.assertIn('url("https://cdn.example.com/col.jpg")', col1.get("style", ""))
        self.assertNotIn("background-image", col2.get("style", "") or "")
        self.assertNotIn("background-image", wrap.get("style", "") or "")

    def test_region_style_skips_nested_same_name(self):
        soup = BeautifulSoup(
            '<body><div data-instance-id="blk_rowouter">'
            '<div data-region="col1">'
            '<div data-instance-id="blk_rowinner"><div data-region="col1">inner</div></div>'
            "</div></div></body>",
            "lxml",
        )
        _apply_styles(soup, {
            "blk_rowouter.__region.col1": {"bgColor": "#111111"},
        })
        outer = soup.find(attrs={"data-instance-id": "blk_rowouter"}).find(
            attrs={"data-region": "col1"}, recursive=False
        )
        inner = soup.find(attrs={"data-instance-id": "blk_rowinner"}).find(
            attrs={"data-region": "col1"}
        )
        self.assertIn("background-color: #111111;", outer.get("style", ""))
        self.assertNotIn("background-color", inner.get("style", "") or "")


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
        # (except selection-styled spans, which keep their own per-part colour).
        self.assertIn(
            '[data-edit="hero.title"] *:not(.cms-tspan) { color: #b91c1c !important; }',
            html)

    def test_whole_element_color_does_not_override_selection_span(self):
        # The robyn bug: an element has a whole-element _styles colour (emitting
        # the !important descendant rule) AND a per-part <span class="cms-tspan">.
        # The span must be excluded from the rule so its colour still shows.
        template = (
            "<html><head></head><body>"
            '<section data-section="hero">'
            '<h1 data-edit="hero.title" data-type="richtext">A B</h1>'
            "</section></body></html>"
        )
        content = {
            "hero": {"title": 'A <span class="cms-tspan" style="color: #e11d48">B</span>'},
            "_styles": {"hero.title": {"color": "#ffffff"}},
        }
        html = render_site(template, content)
        self.assertIn('*:not(.cms-tspan) { color: #ffffff !important; }', html)
        span = BeautifulSoup(html, "lxml").select_one('[data-edit="hero.title"] span.cms-tspan')
        self.assertIsNotNone(span)
        self.assertIn("#e11d48", span.get("style", ""))

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
    client can recolor part of a heading, e.g. a two-colour header. This
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
        # execCommand('foreColor') emits rgb(), not hex, which is the value the editor
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


_TEXT_TEMPLATE = (
    "<html><head></head><body>"
    '<section data-section="hero">'
    '<p data-edit="hero.tagline" data-type="text">Tag</p>'
    "</section></body></html>"
)


class TextFieldSelectionStyleTests(SimpleTestCase):
    """A plain `text` field can also carry a selection-styled span (lp-cms lets
    you style any text, not just 'rich' fields). Once it holds inline markup it
    renders as sanitized HTML; a plain value stays literal text."""

    def test_text_field_with_span_renders_as_html(self):
        content = {"hero": {"tagline":
                            'Big <span class="cms-tspan" style="color: #e11d48">deal</span>'}}
        html = render_site(_TEXT_TEMPLATE, content)
        span = BeautifulSoup(html, "lxml").select_one('[data-edit="hero.tagline"] span.cms-tspan')
        self.assertIsNotNone(span)
        self.assertIn("#e11d48", span.get("style", ""))
        self.assertEqual(span.get_text(), "deal")

    def test_plain_text_field_stays_literal(self):
        content = {"hero": {"tagline": "Just plain text"}}
        html = render_site(_TEXT_TEMPLATE, content)
        p = BeautifulSoup(html, "lxml").find(attrs={"data-edit": "hero.tagline"})
        self.assertEqual(p.get_text(), "Just plain text")
        self.assertIsNone(p.find("span"))

    def test_text_field_angle_bracket_not_treated_as_markup(self):
        # "a < b" is not inline markup and must not be swallowed as a tag.
        content = {"hero": {"tagline": "a < b and c"}}
        html = render_site(_TEXT_TEMPLATE, content)
        p = BeautifulSoup(html, "lxml").find(attrs={"data-edit": "hero.tagline"})
        self.assertEqual(p.get_text(), "a < b and c")


class AutoAnnotateTests(SimpleTestCase):
    """Un-annotated text-leaf elements get a data-edit id at render time so they
    become editable/styleable through the normal pipeline (combining lp-cms
    auto-detection with our annotations). Purely additive."""

    def _render(self, body_html, content=None):
        template = "<html><head></head><body>" + body_html + "</body></html>"
        return BeautifulSoup(render_site(template, content or {}), "lxml")

    def test_unannotated_text_leaves_get_data_edit(self):
        soup = self._render("<h1>Hi</h1><p>Body</p>")
        h1 = soup.find("h1")
        p = soup.find("p")
        self.assertTrue(h1.get("data-edit", "").startswith("auto."))
        self.assertTrue(p.get("data-edit", "").startswith("auto."))
        self.assertNotEqual(h1.get("data-edit"), p.get("data-edit"))

    def test_existing_annotation_is_untouched(self):
        soup = self._render('<h1 data-edit="hero.title" data-type="text">Hi</h1>')
        self.assertEqual(soup.find("h1").get("data-edit"), "hero.title")

    def test_container_not_annotated_only_leaf(self):
        soup = self._render("<div><p>Leaf</p></div>")
        self.assertIsNone(soup.find("div").get("data-edit"))
        self.assertTrue(soup.find("p").get("data-edit", "").startswith("auto."))

    def test_nested_inside_field_not_annotated(self):
        soup = self._render('<div data-edit="s.f" data-type="richtext"><p>x</p></div>')
        self.assertIsNone(soup.find("p").get("data-edit"))

    def test_empty_element_not_annotated(self):
        soup = self._render("<p>   </p>")
        self.assertIsNone(soup.find("p").get("data-edit"))

    def test_styled_content_applies_to_auto_element(self):
        # First render to discover the id assigned to the paragraph…
        soup = self._render("<h1>Head</h1><p>Body text</p>")
        pid = soup.find("p").get("data-edit")
        section, field = pid.split(".", 1)
        # …then store a selection-styled span under that id and re-render.
        content = {section: {field: 'Body <span class="cms-tspan" style="color: #e11d48">text</span>'}}
        soup2 = self._render("<h1>Head</h1><p>Body text</p>", content)
        span = soup2.find("p").find("span")
        self.assertIsNotNone(span)
        self.assertIn("#e11d48", span.get("style", ""))
        # Unstyled auto element keeps its original text.
        self.assertEqual(soup2.find("h1").get_text(), "Head")


class PreviewBridgeStyleTests(SimpleTestCase):
    def test_bridge_has_style_handlers(self):
        html = render_site(_TEMPLATE, {"hero": {"title": "Hi"}}, preview=True)
        self.assertIn("apply-styles", html)
        self.assertIn("apply-global", html)
        self.assertIn("cmsEnsureFont", html)


class UrlFieldSanitizeTests(SimpleTestCase):
    """Typed URL field values are scheme-checked on render (E7)."""

    def _render(self, body_html, content):
        template = "<html><head></head><body>" + body_html + "</body></html>"
        return BeautifulSoup(render_site(template, content), "lxml")

    def test_javascript_link_rejected_keeps_default(self):
        soup = self._render(
            '<a data-edit="hero.cta" data-type="link" href="/ok">Go</a>',
            {"hero": {"cta": "javascript:alert(1)"}},
        )
        self.assertEqual(soup.find("a").get("href"), "/ok")

    def test_https_link_applied(self):
        soup = self._render(
            '<a data-edit="hero.cta" data-type="link" href="/ok">Go</a>',
            {"hero": {"cta": "https://example.com"}},
        )
        self.assertEqual(soup.find("a").get("href"), "https://example.com")

    def test_mailto_and_anchor_and_tel_allowed(self):
        for value in ("mailto:a@b.com", "#section", "tel:+123", "/rel/path"):
            soup = self._render(
                '<a data-edit="s.f" data-type="link" href="#">x</a>',
                {"s": {"f": value}},
            )
            self.assertEqual(soup.find("a").get("href"), value)

    def test_javascript_image_src_rejected(self):
        soup = self._render(
            '<img data-edit="hero.img" data-type="image" src="/ok.png">',
            {"hero": {"img": "javascript:alert(1)"}},
        )
        self.assertEqual(soup.find("img").get("src"), "/ok.png")

    def test_data_image_allowed_but_data_html_rejected(self):
        ok = self._render(
            '<img data-edit="s.f" data-type="image" src="/x.png">',
            {"s": {"f": "data:image/png;base64,AAAA"}},
        )
        self.assertEqual(ok.find("img").get("src"), "data:image/png;base64,AAAA")
        bad = self._render(
            '<img data-edit="s.f" data-type="image" src="/x.png">',
            {"s": {"f": "data:text/html,<script>alert(1)</script>"}},
        )
        self.assertEqual(bad.find("img").get("src"), "/x.png")

    def test_unsafe_color_field_rejected(self):
        soup = self._render(
            '<span data-edit="s.f" data-type="color">x</span>',
            {"s": {"f": "red; } body { display:none"}},
        )
        self.assertNotIn("display:none", soup.find("span").get("style", ""))


class BrandTokenSanitizeTests(SimpleTestCase):
    """Brand token values can't break out of the :root rule (E6)."""

    def _css(self, brand):
        template = (
            "<html><head><style data-tokens>:root { --primary: #000; }</style>"
            "</head><body><h1>H</h1></body></html>"
        )
        soup = BeautifulSoup(render_site(template, {"brand": brand}), "lxml")
        return soup.find("style", attrs={"data-tokens": True}).string

    def test_safe_color_applied(self):
        self.assertIn("--primary: #ff0000;", self._css({"primary": "#ff0000"}))

    def test_breakout_value_keeps_default(self):
        css = self._css({"primary": "#fff; } body { display:none; } :root{"})
        self.assertNotIn("display:none", css)
        self.assertIn("--primary: #000;", css)


class GlobalStyleInjectionTests(SimpleTestCase):
    def _css(self, g):
        soup = BeautifulSoup(
            "<html><head></head><body><h1>H</h1></body></html>", "lxml")
        _apply_global_styles(soup, g)
        block = soup.find("style", attrs={"data-cms-global": True})
        return block.string if block else ""

    def test_textcolor_breakout_dropped(self):
        css = self._css({"textColor": "#fff} body{display:none}"})
        self.assertNotIn("display:none", css)

    def test_font_family_stripped_of_injection(self):
        css = self._css({"fontFamily": "Inter;}body{display:none"})
        self.assertNotIn("display:none", css)


class CodeFieldPreviewTests(SimpleTestCase):
    """Code fields run raw on the public site but are inert in preview (E1)."""

    _CODE_TEMPLATE = (
        "<html><head></head><body>"
        '<div data-edit="hero.code" data-type="code"></div>'
        "</body></html>"
    )
    _PAYLOAD = {"hero": {"code": "<script>window.__pwned=1</script>"}}

    def test_public_render_keeps_raw_script(self):
        html = render_site(self._CODE_TEMPLATE, self._PAYLOAD, preview=False)
        self.assertIn("<script>window.__pwned=1</script>", html)

    def test_preview_render_escapes_code(self):
        html = render_site(self._CODE_TEMPLATE, self._PAYLOAD, preview=True)
        # The code field's element carries the value as escaped text, not a
        # live <script> that would run inside the authenticated dashboard iframe.
        soup = BeautifulSoup(html, "lxml")
        code_el = soup.find(attrs={"data-edit": "hero.code"})
        self.assertIsNone(code_el.find("script"))
        self.assertIn("window.__pwned=1", code_el.get_text())
        # The live-apply bridge sets code via textContent (inert), never innerHTML.
        self.assertNotIn("el.innerHTML = value", html)
