"""Tests for the deterministic backfill that catches text-bearing elements
the LLM missed during annotation.

The annotator's first pass is whatever the model returns. That's good enough
most of the time, but body text reliably leaks through — short paragraphs,
the second card description in a repeated group, an h3 inside a deep wrapper.
_backfill_missed_text_fields runs after _apply_annotations and promotes any
unmarked text-bearing tag inside a data-section to an editable field so the
result is robust regardless of how thorough the model was.
"""
from bs4 import BeautifulSoup
from django.test import TestCase

from core.services.annotator import _backfill_missed_text_fields


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class BackfillCatchesUnmarkedBodyTextTests(TestCase):
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
