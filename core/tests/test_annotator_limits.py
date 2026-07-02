"""Tests for the annotator's large-input guards.

Two failure modes on very large pages (e.g. Figma Make / Vite exports with
thousands of DOM nodes) both surfaced to the operator as the confusing
"Annotation timed out on the server" stale-job message:

1. The OpenAI client was built with the SDK default ``max_retries=2``, so a
   request that hit ``OPENAI_TIMEOUT`` retried twice — multiplying wall-clock
   time (~120s x 3) past the 300s background-job stale threshold.
2. Nothing rejected an input too big to ever succeed, so a doomed request ran
   for minutes before failing.

The client now disables retries, and oversized input is rejected up front with
an actionable error instead of a slow doomed call.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.services.annotator import (
    AnnotatorError,
    _make_openai_client,
    _reject_if_too_large,
)


class OpenAIClientRetryTests(TestCase):
    def test_client_built_with_retries_disabled(self):
        """A single attempt keeps worst-case wall-clock at OPENAI_TIMEOUT, well
        under the 300s job-stale guard, so a real timeout surfaces as a clean
        error rather than the misleading 'timed out on the server' message."""
        with patch("openai.OpenAI") as mock_openai:
            _make_openai_client("sk-test")
        self.assertTrue(mock_openai.called)
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs.get("max_retries"), 0)


class InputSizeGuardTests(TestCase):
    @override_settings(ANNOTATE_MAX_INPUT_CHARS=100)
    def test_oversized_input_raises_annotator_error(self):
        with self.assertRaises(AnnotatorError) as ctx:
            _reject_if_too_large("x" * 101)
        self.assertIn("too large", str(ctx.exception).lower())

    @override_settings(ANNOTATE_MAX_INPUT_CHARS=100)
    def test_input_at_limit_is_allowed(self):
        # Exactly at the limit must not raise (boundary is inclusive).
        self.assertIsNone(_reject_if_too_large("x" * 100))
