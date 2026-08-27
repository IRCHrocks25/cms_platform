"""The annotator must not leave a mixed-style heading typed as ``text``.

``_backfill_missed_text_fields`` already picks ``richtext`` for child-bearing
tags, but only for fields the *model skipped*. The system prompt tells the
model an ``<h2>`` is ``text``, so a heading it annotated itself arrives typed
``text`` with an accent ``<span>`` inside, and the renderer's ``el.string =
value`` deletes the span on the first render.

``_upgrade_child_bearing_text_fields`` runs over every annotated field,
model-assigned included, so the stored HTML is honest and the dashboard offers
a rich-text control instead of a single-line input that cannot hold the markup.
"""
from bs4 import BeautifulSoup
from django.test import TestCase

from core.services.annotator import _upgrade_child_bearing_text_fields


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class UpgradeChildBearingTextFieldsTests(TestCase):
    def test_model_assigned_text_on_an_accent_heading_becomes_richtext(self):
        s = _soup(
            "<section data-section='hero'>"
            "<h1 data-edit='hero.title' data-type='text'>"
            "Navigate menopause. <span class='italic'>Naturally, powerfully.</span>"
            "</h1></section>"
        )
        self.assertEqual(_upgrade_child_bearing_text_fields(s), 1)
        self.assertEqual(s.find("h1")["data-type"], "richtext")

    def test_plain_text_field_is_left_alone(self):
        s = _soup(
            "<section data-section='hero'>"
            "<p data-edit='hero.eyebrow' data-type='text'>Belfast</p></section>"
        )
        self.assertEqual(_upgrade_child_bearing_text_fields(s), 0)
        self.assertEqual(s.find("p")["data-type"], "text")

    def test_a_field_with_no_data_type_defaults_to_text_and_is_upgraded(self):
        s = _soup(
            "<section data-section='hero'>"
            "<h2 data-edit='hero.sub'>Hi <em>there</em></h2></section>"
        )
        self.assertEqual(_upgrade_child_bearing_text_fields(s), 1)
        self.assertEqual(s.find("h2")["data-type"], "richtext")

    def test_non_text_types_are_never_retyped(self):
        s = _soup(
            "<section data-section='hero'>"
            "<a data-edit='hero.cta' data-type='link' href='/x'>Go <svg></svg></a>"
            "<img data-edit='hero.img' data-type='image' src='/a.png'>"
            "</section>"
        )
        self.assertEqual(_upgrade_child_bearing_text_fields(s), 0)
        self.assertEqual(s.find("a")["data-type"], "link")
