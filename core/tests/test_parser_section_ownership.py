"""Nearest-section ownership rules for nested annotation blocks."""

from django.test import SimpleTestCase

from core.parser import build_schema


class NestedSectionOwnershipTests(SimpleTestCase):
    def test_outer_section_never_claims_a_nested_sections_field(self):
        html = (
            "<section data-section='outer'>"
            "<h1 data-edit='outer.title'>Outer</h1>"
            "<section data-section='inner'>"
            "<h2 data-edit='inner.title'>Inner</h2>"
            "<p data-edit='outer.leaked'>Wrong prefix</p>"
            "</section>"
            "</section>"
        )

        schema = build_schema(html)
        fields = {
            section["id"]: [field["id"] for field in section["fields"]]
            for section in schema["sections"]
        }

        self.assertEqual(fields["outer"], ["outer.title"])
        self.assertEqual(fields["inner"], ["inner.title"])
