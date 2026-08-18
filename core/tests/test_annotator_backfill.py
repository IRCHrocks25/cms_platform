"""Tests for deterministic text and content-image annotation backfill.

The annotator's first pass is whatever the model returns. That's good enough
most of the time, but body text reliably leaks through — short paragraphs,
the second card description in a repeated group, an h3 inside a deep wrapper.
_backfill_missed_text_fields runs after _apply_annotations and promotes any
unmarked text-bearing tag inside a data-section to an editable field so the
result is robust regardless of how thorough the model was. Image backfill does
the same for content photos while excluding deterministic chrome and tracking
signals.
"""
from bs4 import BeautifulSoup
from django.test import TestCase

from core.services.annotator import (
    _backfill_missed_image_fields,
    _backfill_missed_text_fields,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class BackfillCatchesUnmarkedBodyTextTests(TestCase):
    def test_nested_section_owns_its_own_backfilled_fields(self):
        s = _soup(
            "<section data-section='outer'>"
            "<h1>Outer title</h1>"
            "<section data-section='inner'><h2>Inner title</h2></section>"
            "</section>"
        )

        added = _backfill_missed_text_fields(s)

        self.assertEqual(added, 2)
        self.assertEqual(s.find("h1")["data-edit"], "outer.h1_1")
        self.assertEqual(s.find("h2")["data-edit"], "inner.h2_1")

    def test_unmarked_h2_and_p_inside_section_get_data_edit(self):
        s = _soup(
            "<section data-section='hero' data-label='Hero'>"
            "<h2>Welcome</h2>"
            "<p>We build websites.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 2)
        h2 = s.find("h2")
        p = s.find("p")
        self.assertEqual(h2.get("data-edit"), "hero.h2_1")
        self.assertEqual(h2.get("data-type"), "text")
        self.assertEqual(p.get("data-edit"), "hero.p_1")
        self.assertEqual(p.get("data-type"), "richtext")

    def test_already_marked_fields_are_left_alone(self):
        """The model already marked the title — backfill must not overwrite it."""
        s = _soup(
            "<section data-section='hero'>"
            "<h2 data-edit='hero.title' data-type='text' data-label='Title'>X</h2>"
            "<p>Body the model missed.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 1)
        self.assertEqual(s.find("h2").get("data-edit"), "hero.title")
        self.assertEqual(s.find("p").get("data-edit"), "hero.p_1")

    def test_repeated_tags_get_distinct_field_ids(self):
        s = _soup(
            "<section data-section='features'>"
            "<h3>One</h3><p>First.</p>"
            "<h3>Two</h3><p>Second.</p>"
            "<h3>Three</h3><p>Third.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 6)
        h3_ids = [h.get("data-edit") for h in s.find_all("h3")]
        p_ids = [p.get("data-edit") for p in s.find_all("p")]
        self.assertEqual(h3_ids, ["features.h3_1", "features.h3_2", "features.h3_3"])
        self.assertEqual(p_ids, ["features.p_1", "features.p_2", "features.p_3"])

    def test_generated_field_id_dodges_collision_with_model_id(self):
        """If the model used 'p_1' for one paragraph, the backfill must
        pick 'p_2' for the next, not stomp on the model's choice."""
        s = _soup(
            "<section data-section='hero'>"
            "<p data-edit='hero.p_1' data-type='richtext'>Existing.</p>"
            "<p>Missed.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 1)
        ps = s.find_all("p")
        self.assertEqual(ps[0].get("data-edit"), "hero.p_1")
        self.assertEqual(ps[1].get("data-edit"), "hero.p_2")

    def test_whitespace_only_elements_are_skipped(self):
        s = _soup(
            "<section data-section='hero'>"
            "<h2>   </h2>"
            "<p>\n\n</p>"
            "<p>Real text.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 1, "Only the paragraph with real text should be promoted")

    def test_elements_inside_nav_button_anchor_are_skipped(self):
        """Body-text backfill should NOT touch chrome — nav link labels,
        button labels, and anchor text are handled by the link/text rules
        in the model pass."""
        s = _soup(
            "<section data-section='page'>"
            "<nav><a href='/about'><span>About us</span></a></nav>"
            "<button><span>Sign up</span></button>"
            "<a href='/x'>Just a link</a>"
            "<p>Body the model missed.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 1, "Only the <p> should be promoted, not nav/button/anchor text")
        self.assertEqual(s.find("p").get("data-edit"), "page.p_1")

    def test_brand_section_is_not_touched(self):
        """The brand section is synthetic (built from CSS variables) — it
        has no data-section attribute on a real wrapper, but skip
        defensively if someone ever annotates one."""
        s = _soup(
            "<section data-section='brand'>"
            "<p>Reserved.</p>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 0)

    def test_elements_outside_any_section_are_skipped(self):
        s = _soup(
            "<div><p>Outside everything.</p></div>"
            "<section data-section='hero'><h1>Inside.</h1></section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 1)
        self.assertIsNone(s.find("p").get("data-edit"))
        self.assertEqual(s.find("h1").get("data-edit"), "hero.h1_1")

    def test_blockquote_figcaption_li_get_promoted(self):
        s = _soup(
            "<section data-section='content'>"
            "<blockquote>A quote.</blockquote>"
            "<figure><figcaption>A caption.</figcaption></figure>"
            "<ul><li>One</li><li>Two</li></ul>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 4)
        self.assertEqual(s.find("blockquote").get("data-type"), "richtext")
        self.assertEqual(s.find("figcaption").get("data-type"), "richtext")
        self.assertEqual(s.find_all("li")[0].get("data-type"), "text")

    def test_data_label_is_derived_from_text(self):
        s = _soup(
            "<section data-section='hero'>"
            "<h2>Welcome to our site</h2>"
            "</section>"
        )
        _backfill_missed_text_fields(s)
        label = s.find("h2").get("data-label", "")
        self.assertIn("Welcome", label)

    def test_heading_with_inline_children_gets_richtext_type(self):
        """If we mark an <h2> with inner <span> / <strong> / <em> as plain
        text, the renderer's text path does `el.string = value` and FLATTENS
        the inline children — the highlight span vanishes and the visual
        design breaks. Pick richtext when the element has any child tags so
        the inline structure survives on render."""
        s = _soup(
            "<section data-section='hero'>"
            "<h2>Hello <span class='highlight'>world</span></h2>"
            "<h3>Plain heading</h3>"
            "</section>"
        )
        _backfill_missed_text_fields(s)
        self.assertEqual(
            s.find("h2").get("data-type"), "richtext",
            "Headings with inline child tags must be richtext or design breaks on render",
        )
        self.assertEqual(
            s.find("h3").get("data-type"), "text",
            "Headings with only plain text stay text-typed",
        )

    def test_list_item_with_inline_children_gets_richtext_type(self):
        s = _soup(
            "<section data-section='nav'>"
            "<ul>"
            "<li><strong>Bold</strong> bullet</li>"
            "<li>Plain bullet</li>"
            "</ul>"
            "</section>"
        )
        # Skip-ancestor check: <li> inside <ul> only, no <nav>/<a>/<button>.
        _backfill_missed_text_fields(s)
        lis = s.find_all("li")
        self.assertEqual(lis[0].get("data-type"), "richtext")
        self.assertEqual(lis[1].get("data-type"), "text")


class BackfillCatchesUnmarkedContentImagesTests(TestCase):
    def test_model_marked_image_is_not_backfilled_or_given_a_second_id(self):
        s = _soup(
            "<section data-section='hero'>"
            "<img src='hero.jpg' alt='' data-edit='hero.primary_photo' "
            "data-type='image' data-label='Primary photo'>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        image = s.find("img")
        self.assertEqual(added, 0)
        self.assertEqual(image.get("data-edit"), "hero.primary_photo")
        self.assertEqual(
            [element.get("data-edit") for element in s.select("[data-edit]")],
            ["hero.primary_photo"],
        )

    def test_empty_alt_content_image_is_promoted(self):
        s = _soup(
            "<section data-section='hero' data-label='Hero'>"
            "<div class='hero-image'><img src='hero.jpg' alt=''></div>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        image = s.find("img")
        self.assertEqual(added, 1)
        self.assertEqual(image.get("data-edit"), "hero.image_1")
        self.assertEqual(image.get("data-type"), "image")
        self.assertEqual(image.get("data-label"), "Hero image")

    def test_alt_text_supplies_label_and_picture_image_is_promoted(self):
        s = _soup(
            "<section data-section='about'>"
            "<picture><source srcset='chef.webp'>"
            "<img src='chef.jpg' alt='Chef Maria plating dinner'></picture>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        image = s.find("img")
        self.assertEqual(added, 1)
        self.assertEqual(image.get("data-edit"), "about.image_1")
        self.assertEqual(image.get("data-label"), "Chef Maria plating dinner")
        self.assertIsNone(s.find("source").get("data-edit"))

    def test_existing_annotation_and_nested_section_ownership_are_preserved(self):
        s = _soup(
            "<section data-section='outer'>"
            "<img src='existing.jpg' data-edit='outer.image_1' data-type='image'>"
            "<img src='outer.jpg'>"
            "<section data-section='inner'><img src='inner.jpg'></section>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        images = s.find_all("img")
        self.assertEqual(added, 2)
        self.assertEqual(images[0].get("data-edit"), "outer.image_1")
        self.assertEqual(images[1].get("data-edit"), "outer.image_2")
        self.assertEqual(images[2].get("data-edit"), "inner.image_1")

    def test_nav_footer_and_shared_chrome_ancestors_are_skipped(self):
        s = _soup(
            "<section data-section='nav'><img src='brand.jpg'></section>"
            "<footer data-section='footer'><img src='payment.jpg'></footer>"
            "<section data-section='gallery'>"
            "<a href='/full'><img src='linked.jpg'></a>"
            "<button><img src='button.jpg'></button>"
            "<img src='content.jpg'>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        self.assertEqual(added, 1)
        self.assertIsNone(s.find("img", src="brand.jpg").get("data-edit"))
        self.assertIsNone(s.find("img", src="payment.jpg").get("data-edit"))
        self.assertIsNone(s.find("img", src="linked.jpg").get("data-edit"))
        self.assertIsNone(s.find("img", src="button.jpg").get("data-edit"))
        self.assertEqual(
            s.find("img", src="content.jpg").get("data-edit"),
            "gallery.image_1",
        )

    def test_explicit_presentation_hidden_and_missing_source_are_skipped(self):
        s = _soup(
            "<section data-section='hero'>"
            "<img src='presentation.jpg' role='presentation'>"
            "<img src='hidden.jpg' aria-hidden='true'>"
            "<img alt='No source'>"
            "<img src='content.jpg'>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        self.assertEqual(added, 1)
        self.assertEqual(
            s.find("img", src="content.jpg").get("data-edit"),
            "hero.image_1",
        )
        self.assertIsNone(s.find("img", src="presentation.jpg").get("data-edit"))
        self.assertIsNone(s.find("img", src="hidden.jpg").get("data-edit"))
        self.assertIsNone(s.find("img", alt="No source").get("data-edit"))

    def test_tiny_dimensions_and_exact_icon_tokens_are_skipped(self):
        s = _soup(
            "<section data-section='features'>"
            "<img src='pixel.gif' width='1' height='1'>"
            "<img src='small.png' style='width: 24px; height: 24px'>"
            "<img src='logo.png' class='company-logo'>"
            "<img src='icon.png' id='feature_icon'>"
            "<img src='large.jpg' width='900' height='600'>"
            "</section>"
        )

        added = _backfill_missed_image_fields(s)

        self.assertEqual(added, 1)
        self.assertEqual(
            s.find("img", src="large.jpg").get("data-edit"),
            "features.image_1",
        )
        for src in ("pixel.gif", "small.png", "logo.png", "icon.png"):
            self.assertIsNone(s.find("img", src=src).get("data-edit"))
