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
    _annotate_one_chunk,
    _chunk_nodes,
    _find_split_root,
    _merge_chunk_results,
    annotate_html,
)


def _soup(html):
    return BeautifulSoup(html, "html.parser")


def _fake_completion(content, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )]
    )


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


class AnnotateHtmlParallelIntegrationTests(TestCase):
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
