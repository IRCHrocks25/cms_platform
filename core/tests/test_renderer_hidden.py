"""Tests for client-controlled hide/show of sections and fields."""
from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.renderer import render_site, merge_with_defaults


def _classes(html, attr, value):
    """Return the class list of the element matching attr=value (order-agnostic
    — lxml alphabetizes attributes, so we can't assert on raw string order)."""
    el = BeautifulSoup(html, "lxml").find(attrs={attr: value})
    return el.get("class", []) if el else None


TEMPLATE = """<html><head><title>t</title></head><body>
<section data-section="hero" data-label="Hero">
  <h1 data-edit="hero.title" data-type="text">Hi</h1>
  <a data-edit="hero.cta" data-type="link" href="#">Go</a>
</section>
<section data-section="testimonials" data-label="Testimonials">
  <p data-edit="testimonials.quote" data-type="text">Quote</p>
</section>
</body></html>"""


def _content(hidden):
    return {
        "hero": {"title": "Hi", "cta": "#"},
        "testimonials": {"quote": "Quote"},
        "_hidden": hidden,
    }


class HiddenRenderTests(SimpleTestCase):
    def test_public_hides_section_with_display_none(self):
        html = render_site(TEMPLATE, _content(["testimonials"]))
        # The whole section wrapper is marked, and a display:none rule is added.
        self.assertIn("cms-hidden", _classes(html, "data-section", "testimonials"))
        self.assertIn("display:none", html.replace(" ", ""))

    def test_public_hides_individual_field(self):
        html = render_site(TEMPLATE, _content(["hero.cta"]))
        self.assertIn("cms-hidden", _classes(html, "data-edit", "hero.cta"))
        # The sibling field in the same section stays visible.
        self.assertNotIn("cms-hidden", _classes(html, "data-edit", "hero.title") or [])

    def test_preview_dims_but_does_not_remove(self):
        html = render_site(TEMPLATE, _content(["testimonials"]), preview=True)
        # Marked hidden, but NO display:none injected (preview dims via the bridge).
        self.assertIn("cms-hidden", _classes(html, "data-section", "testimonials"))
        self.assertNotIn("display:none !important", html)
        # Bridge is present so live toggling works.
        self.assertIn("toggle-visibility", html)

    def test_no_hidden_key_is_safe(self):
        html = render_site(TEMPLATE, {"hero": {"title": "Hi"}})
        self.assertNotIn("cms-hidden", html)

    def test_merge_preserves_hidden_meta(self):
        schema = {"defaults": {"hero": {"title": "default"}}}
        merged = merge_with_defaults(schema, {"hero": {"title": "Hi"}, "_hidden": ["testimonials"]})
        self.assertEqual(merged["_hidden"], ["testimonials"])
        self.assertEqual(merged["hero"]["title"], "Hi")

    def test_merge_with_list_hidden_does_not_crash(self):
        # A list under _hidden must not be merged as if it were section fields.
        merged = merge_with_defaults({}, {"_hidden": ["a", "b.c"]})
        self.assertEqual(merged["_hidden"], ["a", "b.c"])
