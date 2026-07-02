"""Tests for the data-URI strip/restore pair in the annotator.

Embedded `data:image/...;base64,XXX...` URIs in source HTML (common in
Figma Make / Vite-bundled exports) can be tens of megabytes per image.
The LLM input has to fit in the model's context, so the annotator strips
these the same way it strips <style>/<script> blocks, then restores them
once the model has done its work.
"""
from django.test import TestCase

from core.services.annotator import _restore_data_uris, _strip_data_uris


class StripDataUrisTests(TestCase):
    def test_strips_data_uri_from_img_src(self):
        html = '<img src="data:image/png;base64,iVBORw0KGgoAAAA==" alt="x">'
        slim, uris = _strip_data_uris(html)
        self.assertNotIn("iVBORw0KGgo", slim)
        self.assertEqual(len(uris), 1)
        self.assertEqual(uris[0], "data:image/png;base64,iVBORw0KGgoAAAA==")
        # Marker still looks like an image to the LLM so it gets annotated.
        self.assertIn('<img src="', slim)

    def test_strips_data_uri_from_css_url(self):
        html = (
            '<div style="background-image: url(data:image/jpeg;base64,'
            '/9j/4AAQSkZJRgAB==);">x</div>'
        )
        slim, uris = _strip_data_uris(html)
        self.assertNotIn("/9j/4AAQSkZ", slim)
        self.assertEqual(len(uris), 1)
        # CSS form preserved enough for the browser/parser to still see it.
        self.assertIn("url(", slim)

    def test_strips_multiple_data_uris_independently(self):
        html = (
            '<img src="data:image/png;base64,AAAAAAAA==">'
            '<img src="data:image/png;base64,BBBBBBBB==">'
            '<div style="background-image: url(data:image/png;base64,CCCCCCCC==);"></div>'
        )
        slim, uris = _strip_data_uris(html)
        self.assertEqual(len(uris), 3)
        for needle in ("AAAAAAAA", "BBBBBBBB", "CCCCCCCC"):
            self.assertNotIn(needle, slim)

    def test_does_not_touch_non_data_urls(self):
        html = (
            '<img src="https://cdn.example.com/x.png">'
            '<a href="https://example.com/file.pdf">PDF</a>'
            '<link rel="icon" href="/static/favicon.ico">'
        )
        slim, uris = _strip_data_uris(html)
        self.assertEqual(uris, [])
        self.assertEqual(slim, html)

    def test_no_data_uris_returns_input_unchanged(self):
        html = "<p>Hello world</p>"
        slim, uris = _strip_data_uris(html)
        self.assertEqual(slim, html)
        self.assertEqual(uris, [])


class RestoreDataUrisTests(TestCase):
    def test_roundtrip_restores_original(self):
        original = (
            '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA==" alt="hero">'
            "<p>Some copy.</p>"
            '<div style="background-image: url(data:image/jpeg;base64,/9j/4AAQ==);">x</div>'
        )
        slim, uris = _strip_data_uris(original)
        restored = _restore_data_uris(slim, uris)
        self.assertEqual(restored, original)

    def test_restore_handles_added_attributes_around_marker(self):
        """The annotator adds data-edit/data-type attrs to <img> between strip and restore.
        Restore must still find and replace the marker even though the surrounding
        attributes have grown."""
        html = '<img src="data:image/png;base64,RAW==">'
        slim, uris = _strip_data_uris(html)
        annotated_slim = slim.replace(
            "<img ",
            '<img data-edit="hero.photo" data-type="image" data-label="Photo" ',
        )
        restored = _restore_data_uris(annotated_slim, uris)
        self.assertIn("data:image/png;base64,RAW==", restored)
        self.assertIn('data-edit="hero.photo"', restored)
        self.assertNotIn("__DATAURI_", restored)

    def test_empty_uri_list_returns_input_unchanged(self):
        html = "<p>nothing to do</p>"
        self.assertEqual(_restore_data_uris(html, []), html)


class StripDataUrisIntegrationWithBlockStripTests(TestCase):
    """Verify the data-URI stripper composes with the existing <style>/<script> stripper.
    The annotator runs them in sequence so neither should re-leak the other's content."""

    def test_data_uri_inside_inline_style_block_still_gets_stripped(self):
        """A <style> block content gets removed by _strip_blocks first.
        A data-URI in an inline style="..." attribute on an element is what
        _strip_data_uris targets."""
        from core.services.annotator import _restore_blocks, _strip_blocks

        html = (
            "<style>.x { color: red; }</style>"
            '<div style="background-image: url(data:image/png;base64,ZZZZ==);">'
            '<img src="data:image/png;base64,QQQQ==">'
            "</div>"
        )
        no_blocks, blocks = _strip_blocks(html)
        no_uris, uris = _strip_data_uris(no_blocks)
        self.assertNotIn("ZZZZ", no_uris)
        self.assertNotIn("QQQQ", no_uris)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(len(uris), 2)
        # Restore in reverse order: URIs first, then blocks
        with_uris = _restore_data_uris(no_uris, uris)
        final = _restore_blocks(with_uris, blocks)
        self.assertEqual(final, html)
