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

    def test_duplicate_field_ids_are_not_added_twice(self):
        schema = build_schema(
            "<section data-section='hero'>"
            "<h1 data-edit='hero.title'>First</h1>"
            "<p data-edit='hero.title'>Second</p>"
            "</section>"
        )

        self.assertEqual(
            [field["id"] for field in schema["sections"][0]["fields"]],
            ["hero.title"],
        )
        self.assertEqual(schema["defaults"], {"hero": {"title": "First"}})

    def test_duplicate_section_ids_are_not_added_twice(self):
        schema = build_schema(
            "<section data-section='hero'>"
            "<h1 data-edit='hero.title'>First</h1>"
            "</section>"
            "<section data-section='hero'>"
            "<p data-edit='hero.copy'>Second</p>"
            "</section>"
        )

        self.assertEqual([section["id"] for section in schema["sections"]], ["hero"])
        self.assertEqual(schema["defaults"], {"hero": {"title": "First"}})
