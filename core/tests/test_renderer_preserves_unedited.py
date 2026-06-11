"""Renderer: when a field's value equals what's already in the element
(i.e. no client edit), the renderer must NOT roundtrip it through the
sanitizer or re-injection path. The sanitizer is designed for untrusted
client-authored content (blog bodies) — applying it to the agency's
own trusted template HTML strips CSS classes, unwraps tags outside its
tight allowlist (span, div, h1, h5, h6, etc.), and collapses internal
whitespace inside text nodes. Designs get visibly broken without anyone
having actually edited anything.
"""
from django.test import TestCase

from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site


class NoOpApplyPreservesOriginalHtmlTests(TestCase):
    def test_unedited_text_preserves_internal_whitespace_between_words(self):
        template = (
            "<section data-section='hero'>"
            "<h2 data-edit='hero.title' data-type='text'>Hello   world</h2>"
            "</section>"
        )
        # Content equals the default (no edit) — element must not be rewritten.
        out = render_site(template, {"hero": {"title": "Hello   world"}})
        self.assertIn("Hello   world", out)

    def test_unedited_richtext_preserves_inline_classes(self):
        """A <span class='accent'> inside a richtext heading must survive a
        no-edit render. Sanitizer would unwrap span + drop the class.
        Use the real schema→merge flow so the default value matches the
        renderer's decode_contents() byte-for-byte (BS uses double quotes)."""
        template = (
            "<section data-section='hero'>"
            "<h2 data-edit='hero.title' data-type='richtext'>"
            "Hello <span class='accent'>world</span>"
            "</h2>"
            "</section>"
        )
        schema = build_schema(template)
        content = merge_with_defaults(schema, {})  # no tenant edits
        out = render_site(template, content)
        self.assertIn("<span", out, "<span> wrapper must not be unwrapped")
        self.assertIn("accent", out, "class='accent' must not be stripped")

    def test_unedited_richtext_preserves_div_and_other_tags(self):
        """Sanitizer's ALLOWED_TAGS is a tight set — div, section, h1, h5,
        h6, button, etc. all get unwrapped. The renderer must skip the
        sanitize step entirely when the field hasn't been edited."""
        template = (
            "<section data-section='hero'>"
            "<div data-edit='hero.body' data-type='richtext'>"
            "<div class='wrapper'><h1>Big title</h1></div>"
            "</div>"
            "</section>"
        )
        schema = build_schema(template)
        content = merge_with_defaults(schema, {})  # no tenant edits
        out = render_site(template, content)
        self.assertIn('class="wrapper"', out,
                      "nested <div class='wrapper'> must survive a no-op render")
        self.assertIn("<h1", out, "<h1> must not be unwrapped on a no-op render")

    def test_real_text_edit_still_replaces_the_field(self):
        """Sanity: when value actually differs, the renderer DOES apply it.
        We didn't accidentally turn off all editing."""
        template = (
            "<section data-section='hero'>"
            "<h2 data-edit='hero.title' data-type='text'>Original</h2>"
            "</section>"
        )
        out = render_site(template, {"hero": {"title": "Changed"}})
        self.assertIn(">Changed<", out)
        self.assertNotIn(">Original<", out)

    def test_unedited_image_doesnt_strip_srcset_or_data_src(self):
        """Image path also has a no-op short-circuit: if value equals the
        current src, leave srcset / data-src alone too."""
        template = (
            "<section data-section='hero'>"
            "<img data-edit='hero.photo' data-type='image' "
            "src='hero.jpg' srcset='hero.jpg 1x, hero@2x.jpg 2x' "
            "data-src='hero.jpg'>"
            "</section>"
        )
        out = render_site(template, {"hero": {"photo": "hero.jpg"}})
        self.assertIn("srcset=", out, "no-op image render should not clear srcset")
        self.assertIn("data-src=", out, "no-op image render should not clear data-src")

    def test_real_image_edit_still_clears_srcset(self):
        """Sanity: with an actual src change, the responsive-attr clearing
        from the earlier renderer fix still runs."""
        template = (
            "<section data-section='hero'>"
            "<img data-edit='hero.photo' data-type='image' "
            "src='old.jpg' srcset='old.jpg 1x, old@2x.jpg 2x'>"
            "</section>"
        )
        out = render_site(template, {"hero": {"photo": "new.jpg"}})
        self.assertIn('src="new.jpg"', out)
        self.assertNotIn("srcset=", out)
