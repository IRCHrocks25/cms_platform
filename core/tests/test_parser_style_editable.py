"""Tests for the style_editable schema flag."""
from django.test import SimpleTestCase

from core.parser import build_schema

_HTML = """<html><body>
<section data-section="hero" data-label="Hero">
  <h1 data-edit="hero.title" data-type="text">Hi</h1>
  <div data-edit="hero.body" data-type="richtext"><p>b</p></div>
  <a data-edit="hero.cta" data-type="link" href="#">Button</a>
  <img data-edit="hero.image" data-type="image" src="x.jpg">
  <span data-edit="hero.locked" data-type="text" data-style="off">L</span>
</section>
</body></html>"""


class StyleEditableFlagTests(SimpleTestCase):
    def _fields(self):
        schema = build_schema(_HTML)
        hero = next(s for s in schema["sections"] if s["id"] == "hero")
        return {f["id"]: f for f in hero["fields"]}

    def test_text_and_richtext_are_style_editable(self):
        fields = self._fields()
        self.assertTrue(fields["hero.title"]["style_editable"])
        self.assertTrue(fields["hero.body"]["style_editable"])

    def test_link_is_style_editable(self):
        self.assertTrue(self._fields()["hero.cta"]["style_editable"])

    def test_image_is_not_style_editable(self):
        self.assertFalse(self._fields()["hero.image"]["style_editable"])

    def test_data_style_off_opts_out(self):
        self.assertFalse(self._fields()["hero.locked"]["style_editable"])
