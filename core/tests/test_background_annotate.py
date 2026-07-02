"""_annotate_template_in_background must apply the annotated HTML and rebuild
the template schema. Regression: it treated annotate_html's return (a str) as
an object (`result.html` / `result.sections`), which raised AttributeError,
got swallowed, and left imported pages with raw HTML + an empty schema — so the
editor showed no fields even though the page rendered fine.
"""
from unittest import mock

from django.db import connection
from django.test import TestCase

from core.models import Template


class BackgroundAnnotateTests(TestCase):
    def test_applies_annotated_html_and_rebuilds_schema(self):
        raw = "<div><h1>Hi</h1></div>"  # no data-section -> empty schema
        tpl = Template.objects.create(name="t", html_source=raw)
        self.assertEqual(tpl.schema.get("sections", []), [])

        annotated = (
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title' data-type='text'>Hi</h1></section>"
        )
        from dashboard.views import _annotate_template_in_background

        # annotate_html returns a STRING; connection.close() would drop the test
        # transaction, so no-op it here.
        with mock.patch("dashboard.views.annotate_html", return_value=annotated), \
                mock.patch.object(connection, "close"):
            _annotate_template_in_background(tpl.pk, raw)

        tpl.refresh_from_db()
        self.assertIn("data-section", tpl.html_source)
        ids = [s["id"] for s in tpl.schema.get("sections", [])]
        self.assertIn("hero", ids)
