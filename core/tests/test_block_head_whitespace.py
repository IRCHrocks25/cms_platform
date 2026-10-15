"""The conversion paint gate must not invent whitespace in the page head."""
from unittest.mock import patch

from django.test import SimpleTestCase

from core.services import blocks


HTML = (
    '<!doctype html><html><head>'
    '<style>.hero{color:red!important}</style>'
    '<style>html, body { margin: 0 }</style>'
    '<script>window.CONVERSION_SENTINEL = "literal  space";</script>'
    '</head><body><main>'
    '<section data-section="hero" data-label="Hero" data-group="Home">'
    '<h1 data-edit="hero.title" data-type="text">Hello</h1>'
    '</section></main></body></html>'
)


class HeadWhitespaceParityTests(SimpleTestCase):
    def test_preview_preserves_adjacent_style_and_script_head_whitespace(self):
        matches, diff = blocks.preview_classic_upgrade(HTML, {})
        self.assertTrue(matches, diff)

    def test_real_head_css_change_still_refuses_conversion(self):
        original_split = blocks.split_shell_and_blocks

        def changed_shell(html, *, region="main"):
            shell, fragments = original_split(html, region=region)
            return shell.replace("color:red", "color:blue"), fragments

        with patch.object(blocks, "split_shell_and_blocks", side_effect=changed_shell):
            matches, diff = blocks.preview_classic_upgrade(HTML, {})
        self.assertFalse(matches)
        self.assertIn("classic=", diff)

    def test_script_body_text_and_attributes_remain_strict(self):
        original = blocks.normalize_for_diff(HTML)
        for changed in (
            HTML.replace('CONVERSION_SENTINEL', 'CHANGED_SENTINEL'),
            HTML.replace('>Hello</h1>', '>Goodbye</h1>'),
            HTML.replace('data-label="Hero"', 'data-label="Other"'),
        ):
            with self.subTest(changed=changed != HTML):
                self.assertNotEqual(original, blocks.normalize_for_diff(changed))
