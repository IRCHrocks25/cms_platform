import json
from types import SimpleNamespace
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings

from core.parser import build_schema
from core.services.annotator import annotate_html_result
from dashboard.views import STARTER_TEMPLATE_HTML
from scripts.run_annotation_corpus import schema_metrics, strip_annotations


def _completion(payload):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(payload)),
            )
        ]
    )


class StarterTemplateRoundTripTests(TestCase):
    @override_settings(
        OPENAI_API_KEY="sk-test",
        OPENAI_ANNOTATE_MODEL="gpt-5.6-luna",
        OPENAI_ANNOTATE_REASONING_EFFORT="medium",
        ANNOTATE_CHUNK_TARGET_CHARS=1000,
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_stripped_starter_recovers_every_field_through_corpus_path(self):
        section_specs = {
            "starter-hero": {
                "id": "hero",
                "label": "Welcome",
                "group": "Home",
                "fields": {
                    "starter-kicker": ("kicker", "text", "Kicker"),
                    "starter-title": ("title", "text", "Headline"),
                    "starter-intro": ("intro", "richtext", "Introduction"),
                    "starter-image": ("image", "image", "Feature photo"),
                    "starter-cta": ("cta", "link", "Primary link"),
                },
            },
            "starter-highlight": {
                "id": "highlight",
                "label": "Highlight",
                "group": "Sections",
                "fields": {
                    "starter-highlight-title": ("title", "text", "Heading"),
                    "starter-highlight-color": ("background", "color", "Background"),
                },
            },
        }

        def create(**kwargs):
            marked = kwargs["messages"][1]["content"].split(
                "=== HTML TO ANNOTATE (marked) ===", 1
            )[1]
            soup = BeautifulSoup(marked, "html.parser")
            sections = []
            fields = []
            for section_class, spec in section_specs.items():
                section = soup.select_one(f".{section_class}")
                if section is None:
                    continue
                sections.append(
                    {
                        "ref": int(section["data-cms-ref"]),
                        "id": spec["id"],
                        "label": spec["label"],
                        "group": spec["group"],
                    }
                )
                for field_class, (field_id, field_type, label) in spec[
                    "fields"
                ].items():
                    field = section.select_one(f".{field_class}")
                    fields.append(
                        {
                            "ref": int(field["data-cms-ref"]),
                            "edit": f"{spec['id']}.{field_id}",
                            "type": field_type,
                            "label": label,
                        }
                    )
            return _completion({"sections": sections, "fields": fields})

        original_schema = build_schema(STARTER_TEMPLATE_HTML)
        original_fields = {
            field["id"]: field["type"]
            for section in original_schema["sections"]
            for field in section["fields"]
        }
        original_content_types = {
            field["type"]
            for section in original_schema["sections"]
            if section["id"] != "brand"
            for field in section["fields"]
        }
        self.assertEqual(
            {"text", "richtext", "image", "link", "color"},
            original_content_types,
        )

        raw_html = strip_annotations(STARTER_TEMPLATE_HTML)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ):
            result = annotate_html_result(raw_html)

        roundtrip_schema = build_schema(result.html)
        roundtrip_fields = {
            field["id"]: field["type"]
            for section in roundtrip_schema["sections"]
            for field in section["fields"]
        }
        self.assertEqual(roundtrip_fields, original_fields)
        self.assertEqual(
            schema_metrics(build_schema, result.html),
            schema_metrics(build_schema, STARTER_TEMPLATE_HTML),
        )
        self.assertEqual(result.dropped_fields, 0)
