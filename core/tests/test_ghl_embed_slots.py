from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site


FORM_SLOT = """
<!doctype html>
<html><head><title>Form</title></head><body>
  <section data-section="contact" data-label="Contact">
    <div data-edit="contact.embed" data-type="ghl-embed"
         data-ghl-kind="form" data-label="Contact form"></div>
  </section>
</body></html>
"""


class GhlEmbedParserTests(SimpleTestCase):
    def test_form_slot_is_recorded_in_schema(self):
        schema = build_schema(FORM_SLOT)

        field = schema["sections"][0]["fields"][0]
        self.assertEqual(field["id"], "contact.embed")
        self.assertEqual(field["type"], "ghl-embed")
        self.assertEqual(field["ghl_kind"], "form")
        self.assertEqual(field["default"], "")
        self.assertFalse(field["style_editable"])

    def test_kind_prefixed_default_is_rejected(self):
        html = FORM_SLOT.replace("></div>", ">form:abc_123-Z</div>")

        with self.assertRaisesRegex(ValueError, "must be empty"):
            build_schema(html)

    def test_missing_kind_is_rejected_at_parse_time(self):
        html = FORM_SLOT.replace(' data-ghl-kind="form"', "")

        with self.assertRaisesRegex(ValueError, "data-ghl-kind"):
            build_schema(html)

    def test_unknown_kind_is_rejected_at_parse_time(self):
        html = FORM_SLOT.replace('data-ghl-kind="form"', 'data-ghl-kind="calendar"')

        with self.assertRaisesRegex(ValueError, "calendar"):
            build_schema(html)

    def test_raw_form_id_default_is_rejected(self):
        html = FORM_SLOT.replace("></div>", ">abc123</div>")

        with self.assertRaisesRegex(ValueError, "form:<id>"):
            build_schema(html)


class GhlEmbedRendererTests(SimpleTestCase):
    def _soup(self, value, *, preview=False):
        html = render_site(
            FORM_SLOT,
            {"contact": {"embed": value}},
            preview=preview,
        )
        return BeautifulSoup(html, "lxml")

    def test_public_render_emits_allowlisted_form_embed_and_resize_script(self):
        soup = self._soup("form:abc_123-Z")

        iframe = soup.find("iframe", attrs={"data-ghl-form-id": "abc_123-Z"})
        self.assertIsNotNone(iframe)
        self.assertEqual(
            iframe["src"], "https://msgsndr.com/widget/form/abc_123-Z"
        )
        self.assertEqual(iframe["title"], "Contact form")
        scripts = soup.find_all(
            "script", src="https://link.msgsndr.com/js/form_embed.js"
        )
        self.assertEqual(len(scripts), 1)

    def test_legacy_template_default_fails_closed_but_stored_content_wins(self):
        legacy_html = FORM_SLOT.replace("></div>", ">form:legacy_form</div>")
        schema = build_schema(FORM_SLOT)
        schema["defaults"]["contact"]["embed"] = "form:legacy_form"

        without_content = BeautifulSoup(
            render_site(legacy_html, merge_with_defaults(schema, {})), "lxml"
        )
        with_content = BeautifulSoup(
            render_site(
                legacy_html,
                merge_with_defaults(
                    schema, {"contact": {"embed": "form:tenant_form"}}
                ),
            ),
            "lxml",
        )

        self.assertIsNone(without_content.find("iframe"))
        self.assertNotIn("legacy_form", str(without_content))
        iframe = with_content.find(
            "iframe", attrs={"data-ghl-form-id": "tenant_form"}
        )
        self.assertIsNotNone(iframe)
        self.assertNotIn("legacy_form", str(with_content))

    def test_multiple_form_slots_share_one_resize_script(self):
        html = FORM_SLOT.replace(
            "</section>",
            '<div data-edit="contact.backup" data-type="ghl-embed" '
            'data-ghl-kind="form" data-label="Backup form"></div></section>',
        )
        rendered = render_site(
            html,
            {"contact": {"embed": "form:first", "backup": "form:second"}},
        )
        soup = BeautifulSoup(rendered, "lxml")

        self.assertEqual(len(soup.find_all("iframe")), 2)
        self.assertEqual(
            len(
                soup.find_all(
                    "script", src="https://link.msgsndr.com/js/form_embed.js"
                )
            ),
            1,
        )

    def test_empty_public_slot_emits_no_slot_or_embed_assets(self):
        soup = self._soup("")

        self.assertIsNone(soup.find(attrs={"data-edit": "contact.embed"}))
        self.assertIsNone(soup.find("iframe"))
        self.assertIsNone(soup.find("script", src=True))

    def test_invalid_or_raw_values_never_become_embed_urls(self):
        for value in ("abc123", "form:", "form:../../evil", "calendar:abc"):
            with self.subTest(value=value):
                soup = self._soup(value)
                self.assertIsNone(soup.find("iframe"))
                self.assertNotIn("evil", str(soup))

    def test_preview_shows_form_but_disables_submission_with_visible_note(self):
        soup = self._soup("form:abc123", preview=True)

        iframe = soup.find("iframe", attrs={"data-ghl-form-id": "abc123"})
        self.assertIsNotNone(iframe)
        self.assertEqual(iframe.get("tabindex"), "-1")
        self.assertTrue(iframe.has_attr("inert"))
        self.assertIn("pointer-events: none", iframe.get("style", ""))
        note = soup.find(attrs={"data-cms-ghl-preview-note": True})
        self.assertIsNotNone(note)
        self.assertEqual(
            note.get_text(" ", strip=True),
            "This is a preview, nothing is sent.",
        )

    def test_empty_preview_keeps_a_visible_editable_placeholder(self):
        soup = self._soup("", preview=True)

        slot = soup.find(attrs={"data-edit": "contact.embed"})
        self.assertIsNotNone(slot)
        self.assertIn("No GHL form selected", slot.get_text(" ", strip=True))
