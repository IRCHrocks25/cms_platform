"""Renderer: image-type fields must update modern responsive/lazy markup.

The naive renderer set `src` and stopped — which silently failed on real-world
HTML where the browser picks an `<img srcset>` candidate, or a lazy-load
library (lazyload, lozad, etc.) overwrites `src` from `data-src`, or the
`<img>` lives inside a `<picture>` whose `<source srcset>` siblings win.

When the agency client replaces a content image, all three must be reconciled
to the new value so the swap actually appears on the rendered page.
"""
from django.test import TestCase

from core.renderer import render_site


class ImageFieldClearsResponsiveAttrsTests(TestCase):
    def test_srcset_on_img_is_cleared_when_src_changes(self):
        template = (
            "<html><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<img data-edit='hero.photo' data-type='image' "
            "src='old.jpg' srcset='old.jpg 1x, old@2x.jpg 2x'>"
            "</section></body></html>"
        )
        content = {"hero": {"photo": "new.jpg"}}
        out = render_site(template, content)
        self.assertIn('src="new.jpg"', out)
        # If we leave srcset in place, the browser picks an old candidate and
        # the swap is invisible. The renderer must drop srcset entirely.
        self.assertNotIn("srcset=", out, "srcset must be removed when src changes")

    def test_data_src_is_updated_for_lazy_load_libraries(self):
        template = (
            "<html><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<img data-edit='hero.photo' data-type='image' "
            "class='lazyload' src='placeholder.gif' data-src='old.jpg'>"
            "</section></body></html>"
        )
        content = {"hero": {"photo": "new.jpg"}}
        out = render_site(template, content)
        self.assertIn('src="new.jpg"', out)
        self.assertIn(
            'data-src="new.jpg"', out,
            "Lazy-load libraries copy data-src → src on load; data-src "
            "must be updated or the new image never appears.",
        )

    def test_data_srcset_is_cleared_for_lazy_responsive_libraries(self):
        template = (
            "<html><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<img data-edit='hero.photo' data-type='image' "
            "src='placeholder.gif' data-src='old.jpg' "
            "data-srcset='old.jpg 1x, old@2x.jpg 2x'>"
            "</section></body></html>"
        )
        content = {"hero": {"photo": "new.jpg"}}
        out = render_site(template, content)
        self.assertIn('data-src="new.jpg"', out)
        self.assertNotIn(
            "data-srcset=", out,
            "data-srcset must be cleared so the lazy-load library promotes "
            "data-src to src instead of an old candidate.",
        )

    def test_picture_source_srcset_is_cleared(self):
        template = (
            "<html><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<picture>"
            "<source media='(min-width: 768px)' srcset='big.jpg, big@2x.jpg 2x'>"
            "<source srcset='small.jpg, small@2x.jpg 2x'>"
            "<img data-edit='hero.photo' data-type='image' src='fallback.jpg'>"
            "</picture>"
            "</section></body></html>"
        )
        content = {"hero": {"photo": "new.jpg"}}
        out = render_site(template, content)
        self.assertIn('src="new.jpg"', out)
        # Both <source srcset> siblings must be cleared, otherwise the picture
        # element picks an old candidate ahead of the <img src> fallback.
        self.assertNotIn(
            "srcset=", out,
            "All <source srcset> attributes inside the <picture> must be "
            "cleared so the browser falls back to the new <img src>.",
        )

    def test_non_image_fields_are_not_affected(self):
        """Sanity check: we didn't accidentally start mangling other types."""
        template = (
            "<html><body>"
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title' data-type='text'>Old</h1>"
            "</section></body></html>"
        )
        out = render_site(template, {"hero": {"title": "New"}})
        self.assertIn(">New<", out)
