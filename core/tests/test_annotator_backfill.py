"""Tests for deterministic text and content-image annotation backfill.

The annotator's first pass is whatever the model returns. That's good enough
most of the time, but body text reliably leaks through: short paragraphs,
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
    AnnotatorError,
    _assert_annotation_preserved_structure,
    _assert_content_sections_have_fields,
    _backfill_landmark_sections,
    _backfill_missed_image_fields,
    _backfill_missed_text_fields,
    _backfill_missed_video_fields,
    _drop_nested_wrapper_fields,
    _unmarked_text_count,
    _upgrade_child_bearing_text_fields,
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
        """The model already marked the title, so backfill must not overwrite it."""
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
        """Body-text backfill should NOT touch chrome: nav link labels,
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
        """The brand section is synthetic (built from CSS variables), so it
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

    def test_designed_quote_marks_leaves_not_the_blockquote(self):
        """A framed quote is structure. Annotating the wrapper would let a
        later write wipe the <p> and <cite> and flatten the design."""
        s = _soup(
            "<section data-section='problem'>"
            "<blockquote class='quote'>"
            "<p>“Being busy didn't help them.”</p>"
            "<cite>Daniel Burrus</cite>"
            "</blockquote>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 2)
        self.assertIsNone(s.find("blockquote").get("data-edit"))
        self.assertEqual(s.find("blockquote")["class"], ["quote"])
        self.assertEqual(s.find("p").get("data-type"), "richtext")
        self.assertEqual(s.find("cite").get("data-type"), "text")
        self.assertIn("Daniel Burrus", s.find("cite").get_text())

    def test_drops_model_field_on_a_wrapper_that_has_child_fields(self):
        s = _soup(
            "<section data-section='problem'>"
            "<blockquote class='quote' data-edit='problem.blockquote_1' "
            "data-type='richtext'>"
            "<p data-edit='problem.quote' data-type='richtext'>Quote</p>"
            "<cite data-edit='problem.author' data-type='text'>Dan</cite>"
            "</blockquote>"
            "</section>"
        )
        self.assertEqual(_drop_nested_wrapper_fields(s), 1)
        self.assertIsNone(s.find("blockquote").get("data-edit"))
        self.assertEqual(s.find("blockquote")["class"], ["quote"])
        self.assertEqual(s.find("p")["data-edit"], "problem.quote")
        self.assertEqual(s.find("cite")["data-edit"], "problem.author")

    def test_data_label_uses_role_not_truncated_copy(self):
        s = _soup(
            "<section data-section='hero'>"
            "<h2>Welcome to our site</h2>"
            "<p class='body-t'>The top five executives at General Motors were busy.</p>"
            "</section>"
        )
        _backfill_missed_text_fields(s)
        self.assertEqual(s.find("h2").get("data-label"), "Section headline")
        self.assertEqual(s.find("p").get("data-label"), "Body copy")

    def test_standalone_eyebrow_span_is_marked_inline_hl_is_not(self):
        s = _soup(
            "<section data-section='problem'>"
            "<span class='eyebrow'>The Problem</span>"
            "<h2>Busy isn't a strategy. <span class='hl'>It's a symptom.</span></h2>"
            "</section>"
        )
        added = _backfill_missed_text_fields(s)
        self.assertEqual(added, 2)
        eyebrow = s.find("span", class_="eyebrow")
        self.assertEqual(eyebrow.get("data-edit"), "problem.span_1")
        self.assertEqual(eyebrow.get("data-label"), "Eyebrow")
        self.assertIsNone(s.find("span", class_="hl").get("data-edit"))
        self.assertEqual(s.find("h2").get("data-type"), "richtext")

    def test_content_section_with_copy_and_no_fields_fails_audit(self):
        s = _soup(
            "<section data-section='problem' data-group='Sections'>"
            "<div class='split'>Busy isn't a strategy.</div>"
            "</section>"
        )
        self.assertGreater(_unmarked_text_count(s), 0)
        with self.assertRaises(AnnotatorError):
            _assert_content_sections_have_fields(s)

    def test_chrome_header_without_fields_does_not_fail_audit(self):
        s = _soup(
            "<header data-section='header' data-group='Header'>"
            "<nav><a href='#top'>Home</a></nav>"
            "</header>"
        )
        _assert_content_sections_have_fields(s)

    def test_heading_with_inline_children_gets_richtext_type(self):
        """If we mark an <h2> with inner <span> / <strong> / <em> as plain
        text, the renderer's text path does `el.string = value` and FLATTENS
        the inline children. The highlight span vanishes and the visual
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

    def test_marquee_logo_strip_images_are_skipped(self):
        s = _soup(
            "<section class='clients' data-section='clients'>"
            "<p>Clients include</p>"
            "<div class='marquee'><img src='ge.png' alt='GE'></div>"
            "<div aria-hidden='true'><img src='ge.png' alt='GE'></div>"
            "</section>"
            "<section data-section='problem'>"
            "<img src='portrait.jpg' alt='Daniel Burrus'>"
            "</section>"
        )
        added = _backfill_missed_image_fields(s)
        self.assertEqual(added, 1)
        self.assertIsNone(s.find("div", class_="marquee").find("img").get("data-edit"))
        self.assertEqual(s.find("section", attrs={"data-section": "problem"}).find("img").get("data-type"), "image")

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


class LandmarkSectionBackfillTests(TestCase):
    def test_marks_unmarked_header_sections_and_footer_not_menu(self):
        s = _soup(
            "<body>"
            '<header class="nav" id="nav"><a href="#top">Home</a></header>'
            '<div class="menu" id="menu"><a href="#faq">FAQ</a></div>'
            '<section class="hero" id="top"><h1>Welcome</h1></section>'
            '<section id="faq"><h2>Before you start</h2></section>'
            '<footer class="foot"><p>Copyright</p></footer>'
            "</body>"
        )

        added = _backfill_landmark_sections(s)

        self.assertEqual(added, 4)
        self.assertEqual(s.header.get("data-section"), "nav")
        self.assertEqual(s.header.get("data-group"), "Header")
        self.assertIsNone(s.find(id="menu").get("data-section"))
        self.assertEqual(s.find(id="top").get("data-section"), "top")
        self.assertEqual(s.find(id="faq").get("data-section"), "faq")
        self.assertEqual(s.footer.get("data-section"), "footer")
        self.assertEqual(s.footer.get("data-group"), "Footer")

    def test_does_not_mark_nav_nested_inside_header(self):
        s = _soup(
            '<header class="nav"><nav class="nav-links"><a href="#a">A</a></nav></header>'
            "<section id='hero'><h1>Hi</h1></section>"
        )

        added = _backfill_landmark_sections(s)

        self.assertEqual(added, 2)
        self.assertTrue(s.header.get("data-section"))
        self.assertIsNone(s.nav.get("data-section"))

    def test_skips_already_marked_sections(self):
        s = _soup(
            '<section data-section="hero" data-label="Hero"><h1>Hi</h1></section>'
            "<footer><p>Bye</p></footer>"
        )

        added = _backfill_landmark_sections(s)

        self.assertEqual(added, 1)
        self.assertEqual(s.section.get("data-section"), "hero")
        self.assertEqual(s.footer.get("data-section"), "footer")


class VideoBackfillTests(TestCase):
    def test_marks_unmarked_section_video(self):
        s = _soup(
            '<section data-section="hero">'
            '<video class="hero-video" src="film.mp4"></video>'
            "<h1>Title</h1>"
            "</section>"
        )

        added = _backfill_missed_video_fields(s)

        self.assertEqual(added, 1)
        video = s.find("video")
        self.assertEqual(video.get("data-edit"), "hero.video")
        self.assertEqual(video.get("data-type"), "video")

    def test_skips_video_without_src(self):
        s = _soup('<section data-section="hero"><video></video></section>')

        self.assertEqual(_backfill_missed_video_fields(s), 0)


class StructurePreservationTests(TestCase):
    def test_rejects_empty_main_region_rewrite(self):
        raw = (
            "<html><body>"
            "<header></header><section></section><section></section>"
            "<footer></footer></body></html>"
        )
        hollow = (
            "<html><body>"
            '<header></header><div data-region="main"></div>'
            "<footer></footer></body></html>"
        )
        with self.assertRaises(AnnotatorError):
            _assert_annotation_preserved_structure(raw, hollow)

    def test_rejects_dropped_landmarks(self):
        raw = (
            "<html><body>"
            "<header></header><section></section><section></section>"
            "<section></section><footer></footer></body></html>"
        )
        hollow = "<html><body><header></header><footer></footer></body></html>"
        with self.assertRaises(AnnotatorError):
            _assert_annotation_preserved_structure(raw, hollow)

    def test_accepts_same_landmarks(self):
        html = (
            "<html><body>"
            "<header></header><section></section><footer></footer>"
            "</body></html>"
        )
        _assert_annotation_preserved_structure(html, html)


PROBLEM_FIXTURE = """
<section class="sec sec-alt" id="problem" data-section="problem"
         data-label="The Problem" data-group="Sections">
  <div class="split split-r">
    <div>
      <p class="eyebrow">The Problem</p>
      <h2 class="h-sec">
        Busy isn't a strategy. <span class="hl">It's a symptom.</span>
      </h2>
      <p class="body-t">
        The top five executives at General Motors were busy.
      </p>
      <p class="body-t">
        And reacting, by definition, is what you do after something happened.
      </p>
      <blockquote class="quote">
        <p>Being busy didn't help them.</p>
        <cite>Daniel Burrus</cite>
      </blockquote>
    </div>
    <figure class="portrait">
      <div class="portrait-in">
        <img alt="" src="https://cdn.example.com/problem.jpg"/>
      </div>
    </figure>
  </div>
</section>
"""


class DesignedProblemBackfillTests(TestCase):
    def test_problem_band_marks_leaves_not_wrappers(self):
        s = _soup(PROBLEM_FIXTURE)
        text_added = _backfill_missed_text_fields(s)
        image_added = _backfill_missed_image_fields(s)
        _drop_nested_wrapper_fields(s)
        _upgrade_child_bearing_text_fields(s)

        self.assertGreaterEqual(text_added, 6)
        self.assertEqual(image_added, 1)
        self.assertEqual(s.find("p", class_="eyebrow").get("data-label"), "Eyebrow")
        title = s.find("h2")
        self.assertEqual(title.get("data-type"), "richtext")
        self.assertEqual(title.get("data-label"), "Section headline")
        self.assertIsNone(s.find("span", class_="hl").get("data-edit"))
        bodies = s.find_all("p", class_="body-t")
        self.assertEqual(len(bodies), 2)
        self.assertTrue(all(p.get("data-edit") for p in bodies))
        self.assertIsNone(s.find("blockquote").get("data-edit"))
        self.assertIsNone(s.find("div", class_="split").get("data-edit"))
        self.assertEqual(s.find("blockquote").find("p").get("data-type"), "richtext")
        self.assertEqual(s.find("cite").get("data-type"), "text")
        self.assertEqual(s.find("cite").get("data-label"), "Quote author")
        self.assertEqual(s.find("img").get("data-type"), "image")
        self.assertEqual(_unmarked_text_count(s), 0)
        _assert_content_sections_have_fields(s)
