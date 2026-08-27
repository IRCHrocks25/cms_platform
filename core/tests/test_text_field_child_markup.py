"""A `data-type="text"` field whose element carries child markup must not be
flattened.

The text write path in the renderer is ``el.string = value``, which deletes
every child node. When the annotated element is a mixed-style heading (a
headline with an accent ``<span>``, an ``<em>``, an inline ``<a>``, a pair of
``display:block`` spans acting as line breaks), that write silently destroys
the design on the very first render, with nobody having edited anything.

It got there because parser and renderer disagreed about what a text field's
value even is:

* ``parser._extract_default`` used ``el.get_text(strip=True)``, which strips
  each text node *and joins with nothing*: "Navigate menopause." + "Naturally,
  powerfully." came back as ``Navigate menopause.Naturally, powerfully.``
* ``renderer._apply_field`` compared that against ``el.get_text()``, which still
  has the inter-node space. The no-op check therefore never matched, and the
  destructive branch ran on every render.

Live example this is drawn from: themenopausecoach.com, where 14 of 16
child-bearing ``text`` fields had lost their accent spans and their spaces.
"""
from django.test import TestCase

from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site

# The real hero heading off the damaged site, verbatim.
KIERAN_HERO = (
    "<section data-section='hero'>"
    '<h1 data-edit="hero.title" data-type="text">'
    "Navigate menopause. "
    '<span class="italic" style="color: rgb(196, 113, 75);">Naturally, powerfully.</span>'
    "</h1>"
    "</section>"
)


class TextFieldWithChildMarkupTests(TestCase):
    def test_schema_types_a_child_bearing_text_field_as_richtext(self):
        schema = build_schema(KIERAN_HERO)
        field = schema["sections"][0]["fields"][0]
        self.assertEqual(field["type"], "richtext")

    def test_default_keeps_the_accent_span_and_the_space(self):
        schema = build_schema(KIERAN_HERO)
        default = schema["sections"][0]["fields"][0]["default"]
        self.assertIn('class="italic"', default)
        self.assertIn("Navigate menopause. ", default)
        self.assertNotIn("menopause.Naturally", default)

    def test_no_op_render_preserves_the_accent_span(self):
        schema = build_schema(KIERAN_HERO)
        out = render_site(KIERAN_HERO, merge_with_defaults(schema, {}))
        self.assertIn('class="italic"', out)
        self.assertIn("color: rgb(196, 113, 75)", out)
        self.assertNotIn("menopause.Naturally", out)

    def test_no_op_render_preserves_display_block_line_breaks(self):
        """hero.subtitle on the same site: two ``display:block`` spans standing
        in for a line break. Flattening them rewraps the headline."""
        template = (
            "<section data-section='hero'>"
            '<h2 data-edit="hero.subtitle" data-type="text">'
            '<span style="display: block;">The Old You Isn\'t Gone.</span>'
            '<span style="display: block;">Let\'s Bring Her Back.</span>'
            "</h2>"
            "</section>"
        )
        schema = build_schema(template)
        out = render_site(template, merge_with_defaults(schema, {}))
        self.assertEqual(out.count("display: block"), 2)

    def test_a_real_edit_still_replaces_the_value(self):
        """Upgrading the type must not make the field read-only."""
        schema = build_schema(KIERAN_HERO)
        content = merge_with_defaults(schema, {})
        content["hero"]["title"] = "Something else entirely"
        out = render_site(KIERAN_HERO, content)
        self.assertIn("Something else entirely", out)
        self.assertNotIn("Navigate menopause", out)

    def test_plain_text_field_is_still_text(self):
        """No child tags, no upgrade; the editor keeps its single-line input."""
        template = (
            "<section data-section='hero'>"
            "<p data-edit='hero.eyebrow' data-type='text'>Belfast &amp; online</p>"
            "</section>"
        )
        field = build_schema(template)["sections"][0]["fields"][0]
        self.assertEqual(field["type"], "text")
        self.assertEqual(field["default"], "Belfast & online")

    def test_link_and_image_types_are_untouched_by_the_upgrade(self):
        """An <a> wrapping an icon <svg> is still a link field, not richtext."""
        template = (
            "<section data-section='hero'>"
            "<a data-edit='hero.cta' data-type='link' href='/book'>"
            "Book <svg></svg></a>"
            "</section>"
        )
        field = build_schema(template)["sections"][0]["fields"][0]
        self.assertEqual(field["type"], "link")
        self.assertEqual(field["default"], "/book")
