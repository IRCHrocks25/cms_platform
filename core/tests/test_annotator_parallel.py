"""Tests for parallel, chunked annotation.

Large pages are annotated by splitting the DOM into chunks of *whole* top-level
subtrees (never a byte-offset slice, so a tag or word is never cut in half),
sending the chunks to the model concurrently, and merging the per-chunk JSON
back by global ``data-cms-ref``. Each chunk retries itself on failure; if a
chunk still can't be annotated the whole run fails (no silently-missing
sections).
"""
from types import SimpleNamespace
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase, override_settings

from core.services.annotator import (
    AnnotatorError,
    _apply_annotations,
    _annotate_one_chunk,
    _chunk_nodes,
    _find_split_root,
    _merge_chunk_results,
    _merge_chunk_usage,
    _reconcile_annotated_fields,
    annotate_html,
    annotate_html_result,
)
from core.parser import build_schema


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _fake_completion(content, finish_reason="stop", usage=None):
    completion = SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )]
    )
    if usage is not None:
        completion.usage = usage
    return completion


class FindSplitRootTests(TestCase):
    def test_descends_through_single_wrapper_to_real_blocks(self):
        """A page wrapped in one <div id=root> (Figma/Vite export) splits at the
        wrapper's children, not at the lone wrapper."""
        s = _soup(
            "<body><div id='root'>"
            "<header><p>a</p></header><main><p>b</p></main><footer><p>c</p></footer>"
            "</div></body>"
        )
        root = _find_split_root(s)
        block_names = [c.name for c in root.children if getattr(c, "name", None)]
        self.assertEqual(block_names, ["header", "main", "footer"])

    def test_multiple_body_children_are_the_blocks(self):
        s = _soup("<body><section><p>a</p></section><section><p>b</p></section></body>")
        root = _find_split_root(s)
        block_names = [c.name for c in root.children if getattr(c, "name", None)]
        self.assertEqual(block_names, ["section", "section"])

    def test_does_not_overdescend_into_a_leaf(self):
        """Single wrapper whose only child is a leaf must not descend into the
        leaf (which would leave zero blocks)."""
        s = _soup("<body><div><p>only text</p></div></body>")
        root = _find_split_root(s)
        block_names = [c.name for c in root.children if getattr(c, "name", None)]
        self.assertEqual(block_names, ["p"])

    def test_keeps_single_semantic_section_as_a_model_visible_wrapper(self):
        """A small page still needs its section wrapper in the model payload.

        If the splitter descends into the section, its heading, paragraph, and
        image become unrelated siblings. The model can then choose one sibling
        as the section and leave every field without a valid owning ancestor.
        """
        s = _soup(
            "<body><main><section class='consultation-intro'>"
            "<h1>Thoughtful care for a healthier tomorrow</h1>"
            "<p>Book a private consultation with our experienced clinical team.</p>"
            "<img src='consultation-room.jpg' alt='A welcoming medical consultation room'>"
            "</section></main></body>"
        )

        root = _find_split_root(s)
        block_names = [c.name for c in root.children if getattr(c, "name", None)]

        self.assertEqual(root.name, "main")
        self.assertEqual(block_names, ["section"])


class ChunkNodesTests(TestCase):
    def test_groups_whole_nodes_under_target(self):
        s = _soup("<a>1234</a><b>1234</b><c>1234</c>")
        nodes = [c for c in s.children if getattr(c, "name", None)]
        # Each <x>1234</x> serializes to 12 chars. Target 25 -> 2 per chunk.
        chunks = _chunk_nodes(nodes, target_chars=25)
        self.assertEqual([len(c) for c in chunks], [2, 1])

    def test_never_splits_a_single_oversized_node(self):
        s = _soup("<a>1234</a><b>this one is very very large</b><c>1234</c>")
        nodes = [c for c in s.children if getattr(c, "name", None)]
        chunks = _chunk_nodes(nodes, target_chars=15)
        # The big <b> lands alone; no chunk ever contains a partial node.
        flat = [n for ch in chunks for n in ch]
        self.assertEqual(len(flat), 3)
        for ch in chunks:
            self.assertGreaterEqual(len(ch), 1)


class MergeChunkResultsTests(TestCase):
    def test_concatenates_sections_and_fields(self):
        a = {"sections": [{"ref": 0, "id": "hero"}], "fields": [{"ref": 1, "edit": "hero.title"}]}
        b = {"sections": [{"ref": 9, "id": "footer"}], "fields": [{"ref": 10, "edit": "footer.copy"}]}
        merged = _merge_chunk_results([a, b])
        self.assertEqual([s["id"] for s in merged["sections"]], ["hero", "footer"])
        self.assertEqual(len(merged["fields"]), 2)

    def test_uniquifies_colliding_section_ids_and_rewrites_field_prefix(self):
        """Two chunks both name a section 'features'. The second must be renamed
        AND its fields' edit prefixes rewritten to match, or fields orphan."""
        a = {
            "sections": [{"ref": 0, "id": "features"}],
            "fields": [{"ref": 1, "edit": "features.title"}],
        }
        b = {
            "sections": [{"ref": 5, "id": "features"}],
            "fields": [{"ref": 6, "edit": "features.title"}],
        }
        merged = _merge_chunk_results([a, b])
        ids = [s["id"] for s in merged["sections"]]
        self.assertEqual(ids, ["features", "features_2"])
        edits = sorted(f["edit"] for f in merged["fields"])
        self.assertEqual(edits, ["features.title", "features_2.title"])

    def test_sums_available_usage_across_chunks(self):
        usage = _merge_chunk_usage(
            [
                {
                    "_usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "reasoning_tokens": 10,
                        "total_tokens": 150,
                    }
                },
                {
                    "_usage": {
                        "prompt_tokens": 80,
                        "completion_tokens": 40,
                        "reasoning_tokens": 5,
                        "total_tokens": 120,
                    }
                },
                {"sections": [], "fields": []},
            ]
        )

        self.assertEqual(
            usage,
            {
                "prompt_tokens": 180,
                "completion_tokens": 90,
                "reasoning_tokens": 15,
                "total_tokens": 270,
            },
        )


class ReconcileAnnotatedFieldsTests(TestCase):
    def test_rewrites_mismatched_prefix_to_nearest_section(self):
        soup = _soup("<section><h1>Title</h1></section>")
        section = soup.find("section")
        heading = soup.find("h1")
        applied = _apply_annotations(
            {"0": section, "1": heading},
            {
                "sections": [{"ref": "0", "id": "hero"}],
                "fields": [
                    {
                        "ref": "1",
                        "edit": "wrong.title",
                        "type": "text",
                        "label": "Title",
                    }
                ],
            },
        )

        reconciled, dropped = _reconcile_annotated_fields(soup)

        self.assertEqual(applied, 1)
        self.assertEqual((reconciled, dropped), (1, 0))
        self.assertEqual(heading["data-edit"], "hero.title")

    def test_strips_orphan_field_attributes_instead_of_guessing(self):
        soup = _soup(
            "<section data-section='hero'><h1>Title</h1></section>"
            "<p data-edit='hero.orphan' data-type='text' "
            "data-label='Orphan'>Outside</p>"
        )
        orphan = soup.find("p")

        reconciled, dropped = _reconcile_annotated_fields(soup)

        self.assertEqual((reconciled, dropped), (0, 1))
        self.assertNotIn("data-edit", orphan.attrs)
        self.assertNotIn("data-type", orphan.attrs)
        self.assertNotIn("data-label", orphan.attrs)

    def test_uniquifies_duplicate_field_ids_within_the_owning_section(self):
        soup = _soup(
            "<section data-section='hero'>"
            "<h1 data-edit='hero.title'>First</h1>"
            "<p data-edit='hero.title'>Second</p>"
            "</section>"
        )

        reconciled, dropped = _reconcile_annotated_fields(soup)

        self.assertEqual((reconciled, dropped), (1, 0))
        self.assertEqual(
            [element["data-edit"] for element in soup.find_all(attrs={"data-edit": True})],
            ["hero.title", "hero.title_2"],
        )


class AnnotateOneChunkRetryTests(TestCase):
    def _client_raising_then_ok(self, fail_times, ok_content):
        calls = {"n": 0}

        def create(**kwargs):
            if calls["n"] < fail_times:
                calls["n"] += 1
                raise RuntimeError("transient API error")
            return _fake_completion(ok_content)

        self._calls = calls
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def test_retries_then_succeeds(self):
        client = self._client_raising_then_ok(2, '{"sections":[],"fields":[]}')
        data = _annotate_one_chunk(client, "<div data-cms-ref='0'></div>", model="m", retries=2)
        self.assertEqual(data, {"sections": [], "fields": []})
        self.assertEqual(self._calls["n"], 2)  # failed twice, 3rd attempt won

    def test_raises_after_exhausting_retries(self):
        client = self._client_raising_then_ok(99, "unused")
        with self.assertRaises(AnnotatorError):
            _annotate_one_chunk(client, "<div data-cms-ref='0'></div>", model="m", retries=2)

    def test_length_finish_reason_is_not_retried(self):
        calls = {"n": 0}

        def create(**kwargs):
            calls["n"] += 1
            return _fake_completion('{"sections":[]}', finish_reason="length")

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaises(AnnotatorError):
            _annotate_one_chunk(client, "x", model="m", retries=2)
        self.assertEqual(calls["n"], 1)  # a truncated chunk won't fix itself on retry

    def test_luna_request_uses_supported_kwargs_and_large_reasoning_budget(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return _fake_completion('{"sections":[],"fields":[]}')

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        _annotate_one_chunk(client, "x", model="gpt-5.6-luna", retries=0)

        self.assertEqual(captured["max_completion_tokens"], 65_536)
        self.assertEqual(captured["reasoning_effort"], "medium")
        self.assertEqual(captured["response_format"], {"type": "json_object"})
        self.assertNotIn("max_tokens", captured)
        self.assertNotIn("temperature", captured)

    def test_image_prompt_uses_context_for_empty_alt_and_decorative_exclusions(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return _fake_completion('{"sections":[],"fields":[]}')

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        _annotate_one_chunk(client, "x", model="gpt-5.6-luna", retries=0)

        prompt = " ".join(captured["messages"][0]["content"].split())
        self.assertIn(
            'alt="" is not by itself proof that an image is decorative',
            prompt,
        )
        self.assertIn('role="presentation"', prompt)
        self.assertIn('aria-hidden="true"', prompt)
        for signal in ("icons", "logos", "spacers", "tracking pixels", "badges"):
            self.assertIn(signal, prompt)

    @override_settings(OPENAI_ANNOTATE_REASONING_EFFORT="high")
    def test_luna_request_uses_configured_reasoning_effort(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return _fake_completion('{"sections":[],"fields":[]}')

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        _annotate_one_chunk(client, "x", model="gpt-5.6-luna", retries=0)

        self.assertEqual(captured["reasoning_effort"], "high")

    @override_settings(OPENAI_ANNOTATE_REASONING_EFFORT="high")
    def test_non_reasoning_model_overrides_keep_live_verified_output_caps(self):
        expected_caps = {
            "gpt-4o-mini": 16_384,
            "gpt-4o": 16_384,
            "gpt-4.1-mini": 32_768,
        }

        for model, expected_cap in expected_caps.items():
            with self.subTest(model=model):
                captured = {}

                def create(**kwargs):
                    captured.update(kwargs)
                    return _fake_completion('{"sections":[],"fields":[]}')

                client = SimpleNamespace(
                    chat=SimpleNamespace(completions=SimpleNamespace(create=create))
                )
                _annotate_one_chunk(client, "x", model=model, retries=0)

                self.assertEqual(captured["max_completion_tokens"], expected_cap)
                self.assertNotIn("reasoning_effort", captured)
                self.assertNotIn("max_tokens", captured)
                self.assertNotIn("temperature", captured)

    def test_chunk_captures_sdk_token_usage_including_reasoning(self):
        usage = SimpleNamespace(
            prompt_tokens=101,
            completion_tokens=52,
            total_tokens=153,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=17),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: _fake_completion(
                        '{"sections":[],"fields":[]}', usage=usage
                    )
                )
            )
        )

        data = _annotate_one_chunk(client, "x", model="gpt-5.6-luna", retries=0)

        self.assertEqual(
            data["_usage"],
            {
                "prompt_tokens": 101,
                "completion_tokens": 52,
                "reasoning_tokens": 17,
                "total_tokens": 153,
            },
        )


class AnnotateHtmlParallelIntegrationTests(TestCase):
    @override_settings(
        OPENAI_API_KEY="sk-test",
        OPENAI_ANNOTATE_MODEL="gpt-5.6-luna",
        OPENAI_ANNOTATE_REASONING_EFFORT="medium",
        ANNOTATE_CHUNK_TARGET_CHARS=1000,
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_recovers_fields_when_model_section_is_their_sibling(self):
        """Regression fixture from the failed production smoke annotation."""
        html = (
            "<!doctype html><html lang='en'><head>"
            "<title>Production annotation smoke</title></head><body><main>"
            "<section class='consultation-intro'>"
            "<h1>Thoughtful care for a healthier tomorrow</h1>"
            "<p>Book a private consultation with our experienced clinical team.</p>"
            "<img src='https://example.com/consultation-room.jpg' "
            "alt='A welcoming medical consultation room' width='900' height='600'>"
            "</section></main></body></html>"
        )
        model_json = (
            '{"sections":[{"ref":6,"id":"consultation","label":"Consultation"}],'
            '"fields":['
            '{"ref":6,"edit":"consultation.title","type":"text","label":"Title"},'
            '{"ref":7,"edit":"consultation.body","type":"richtext","label":"Body"},'
            '{"ref":8,"edit":"consultation.image","type":"image","label":"Room"}]}'
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: _fake_completion(model_json)
                )
            )
        )

        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ):
            result = annotate_html_result(html)

        schema = build_schema(result.html)
        sections = [s for s in schema["sections"] if s["id"] != "brand"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(len(sections[0]["fields"]), 3)
        self.assertEqual(result.promoted_sections, 1)
        self.assertEqual(result.salvaged_fields, 3)
        self.assertEqual(result.dropped_fields, 0)
        image = BeautifulSoup(result.html, "lxml").find("img")
        self.assertEqual(image.get("data-type"), "image")

    @override_settings(
        OPENAI_API_KEY="sk-test",
        OPENAI_ANNOTATE_MODEL="gpt-5.6-luna",
        OPENAI_ANNOTATE_REASONING_EFFORT="medium",
        ANNOTATE_CHUNK_TARGET_CHARS=1000,
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_recovers_all_starter_fields_from_partially_misaligned_sections(self):
        """Corpus regression: Luna kept only the nested paragraph field."""
        html = (
            "<section><h1>Welcome</h1><div><p>Tell visitors what you do.</p></div>"
            "<img src='hero.jpg' alt=''><a href='#'>Learn more</a></section>"
        )
        model_json = (
            '{"sections":['
            '{"ref":1,"id":"hero","label":"Hero"},'
            '{"ref":2,"id":"intro","label":"Introduction"},'
            '{"ref":4,"id":"content_image","label":"Content Image"},'
            '{"ref":5,"id":"cta","label":"Call to Action"}],'
            '"fields":['
            '{"ref":1,"edit":"hero.title","type":"text","label":"Title"},'
            '{"ref":3,"edit":"intro.body","type":"richtext","label":"Body"},'
            '{"ref":4,"edit":"content_image.image","type":"image","label":"Image"},'
            '{"ref":5,"edit":"cta.label","type":"text","label":"CTA"}]}'
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: _fake_completion(model_json)
                )
            )
        )

        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ):
            result = annotate_html_result(html)

        schema = build_schema(result.html)
        fields = [
            field
            for section in schema["sections"]
            if section["id"] != "brand"
            for field in section["fields"]
        ]
        self.assertEqual(len(fields), 4)
        self.assertEqual(sum(field["type"] == "image" for field in fields), 1)
        self.assertEqual(result.promoted_sections, 1)
        self.assertEqual(result.salvaged_fields, 3)
        self.assertEqual(result.dropped_fields, 0)

    @override_settings(
        OPENAI_API_KEY="sk-test",
        OPENAI_ANNOTATE_MODEL="gpt-5.6-luna",
        OPENAI_ANNOTATE_REASONING_EFFORT="medium",
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_truly_empty_model_result_remains_an_honest_error(self):
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: _fake_completion(
                        '{"sections":[],"fields":[]}'
                    )
                )
            )
        )

        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ), self.assertRaisesRegex(AnnotatorError, "no editable sections"):
            annotate_html_result("<main><h1>Nothing selected</h1></main>")

    @override_settings(
        OPENAI_API_KEY="sk-test",
        OPENAI_ANNOTATE_MODEL="gpt-5.6-luna",
        OPENAI_ANNOTATE_REASONING_EFFORT="medium",
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_does_not_promote_document_root_for_unrelated_orphan_fields(self):
        """Salvage must require a real shared wrapper below html/body."""
        model_json = (
            '{"sections":[{"ref":0,"id":"content","label":"Content"}],'
            '"fields":['
            '{"ref":0,"edit":"content.title","type":"text","label":"Title"},'
            '{"ref":1,"edit":"content.body","type":"richtext","label":"Body"}]}'
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: _fake_completion(model_json)
                )
            )
        )

        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ), self.assertRaisesRegex(AnnotatorError, "no editable sections"):
            annotate_html_result("<h1>Unwrapped title</h1><p>Unwrapped body</p>")

    @override_settings(
        OPENAI_API_KEY="sk-test",
        ANNOTATE_CHUNK_TARGET_CHARS=1000,
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_model_skipped_content_image_is_backfilled_into_final_schema(self):
        html = (
            "<body><section><h1>Hero title</h1>"
            "<img src='hero.jpg' alt=''></section>"
            "<footer><small>Copyright</small></footer></body>"
        )

        def create(**kwargs):
            user = kwargs["messages"][1]["content"]
            chunk = user.split("=== HTML TO ANNOTATE (marked) ===", 1)[1]
            section_ref = chunk.split('data-cms-ref="')[1].split('"')[0]
            field_ref = chunk.split("Hero title")[0].rsplit(
                'data-cms-ref="', 1
            )[1].split('"')[0]
            return _fake_completion(
                '{"sections":[{"ref":%s,"id":"hero","label":"Hero"}],'
                '"fields":[{"ref":%s,"edit":"hero.title",'
                '"type":"text","label":"Title"}]}'
                % (section_ref, field_ref)
            )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ):
            result = annotate_html_result(html)

        soup = BeautifulSoup(result.html, "lxml")
        image = soup.find("img")
        schema = build_schema(result.html)
        non_brand_fields = [
            field
            for section in schema.get("sections", [])
            if section.get("id") != "brand"
            for field in section.get("fields", [])
        ]
        self.assertEqual(result.backfilled_fields, 1)
        self.assertEqual(image.get("data-edit"), "hero.image_1")
        self.assertEqual(image.get("data-type"), "image")
        self.assertEqual(len(non_brand_fields), 2)

    @override_settings(
        OPENAI_API_KEY="sk-test",
        ANNOTATE_CHUNK_TARGET_CHARS=60,  # tiny -> forces a split
        ANNOTATE_MAX_WORKERS=4,
    )
    def test_two_blocks_annotated_in_parallel_and_merged(self):
        html = (
            "<body>"
            "<section><h1>Hero title</h1></section>"
            "<section><h2>Feature title</h2></section>"
            "</body>"
        )

        def create(**kwargs):
            # Route by heading text. Parse refs from the CHUNK only (after the
            # marker) — the message also embeds the few-shot example whose refs
            # would otherwise be picked up.
            user = kwargs["messages"][1]["content"]
            chunk = user.split("=== HTML TO ANNOTATE (marked) ===", 1)[1]
            section_ref = chunk.split('data-cms-ref="')[1].split('"')[0]
            if "Hero title" in chunk:
                field_ref = chunk.split("Hero title")[0].rsplit('data-cms-ref="', 1)[1].split('"')[0]
                return _fake_completion(
                    '{"sections":[{"ref":%s,"id":"hero","label":"Hero","group":"Home"}],'
                    '"fields":[{"ref":%s,"edit":"hero.title","type":"text","label":"Title"}]}'
                    % (section_ref, field_ref)
                )
            field_ref = chunk.split("Feature title")[0].rsplit('data-cms-ref="', 1)[1].split('"')[0]
            return _fake_completion(
                '{"sections":[{"ref":%s,"id":"features","label":"Features","group":"Sections"}],'
                '"fields":[{"ref":%s,"edit":"features.title","type":"text","label":"Title"}]}'
                % (section_ref, field_ref)
            )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch("core.services.annotator._make_openai_client", return_value=fake_client):
            out = annotate_html(html)

        self.assertIn('data-section="hero"', out)
        self.assertIn('data-section="features"', out)
        self.assertIn('data-edit="hero.title"', out)
        self.assertIn('data-edit="features.title"', out)
        self.assertNotIn("data-cms-ref", out)  # helper refs stripped

    @override_settings(
        OPENAI_API_KEY="sk-test",
        ANNOTATE_CHUNK_TARGET_CHARS=1000,
        ANNOTATE_MAX_WORKERS=1,
    )
    def test_output_data_edit_count_matches_non_brand_schema_fields(self):
        html = (
            "<body><section><h1>Hero title</h1></section>"
            "<footer><small>Copyright</small></footer></body>"
        )

        def create(**kwargs):
            user = kwargs["messages"][1]["content"]
            chunk = user.split("=== HTML TO ANNOTATE (marked) ===", 1)[1]
            section_ref = chunk.split('data-cms-ref="')[1].split('"')[0]
            field_ref = chunk.split("Hero title")[0].rsplit(
                'data-cms-ref="', 1
            )[1].split('"')[0]
            return _fake_completion(
                '{"sections":[{"ref":%s,"id":"hero","label":"Hero"}],'
                '"fields":[{"ref":%s,"edit":"wrong.title",'
                '"type":"text","label":"Title"}]}'
                % (section_ref, field_ref)
            )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch(
            "core.services.annotator._make_openai_client",
            return_value=fake_client,
        ):
            out = annotate_html(html)

        soup = BeautifulSoup(out, "lxml")
        field_count = sum(
            len(section.get("fields", []))
            for section in build_schema(out).get("sections", [])
            if section.get("id") != "brand"
        )
        self.assertEqual(len(soup.find_all(attrs={"data-edit": True})), field_count)
        self.assertEqual(soup.find("h1")["data-edit"], "hero.title")
