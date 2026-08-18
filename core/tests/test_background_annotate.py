"""_annotate_template_in_background must apply the annotated HTML and rebuild
the template schema. Regression: it treated annotate_html's return (a str) as
an object (`result.html` / `result.sections`), which raised AttributeError,
got swallowed, and left imported pages with raw HTML + an empty schema — so the
editor showed no fields even though the page rendered fine.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from core.models import AnnotationJob, Template
from core.services.annotator import AnnotationResult


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


class AnnotationJobSummaryTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            "annotation-operator", password="pw", is_staff=True
        )
        self.client.force_login(self.staff)

    def test_worker_persists_integrity_counts_with_section_summary(self):
        job = AnnotationJob.objects.create(created_by=self.staff)
        annotated = (
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title'>Hi</h1></section>"
        )
        result = AnnotationResult(
            html=annotated,
            reconciled_fields=2,
            dropped_fields=1,
            backfilled_fields=3,
        )
        from dashboard.views import _run_annotation_job

        with mock.patch(
            "dashboard.views.annotate_html_result", return_value=result
        ), mock.patch.object(connection, "close"):
            _run_annotation_job(str(job.id), "<h1>Hi</h1>")

        job.refresh_from_db()
        self.assertEqual(job.status, AnnotationJob.STATUS_DONE)
        self.assertEqual(
            job.sections,
            {
                "items": [{"id": "hero", "label": "Hero", "field_count": 1}],
                "reconciled_fields": 2,
                "dropped_fields": 1,
                "backfilled_fields": 3,
            },
        )

    def test_status_unwraps_integrity_counts_for_the_browser(self):
        job = AnnotationJob.objects.create(
            created_by=self.staff,
            status=AnnotationJob.STATUS_DONE,
            result_html="<section></section>",
            sections={
                "items": [{"id": "hero", "label": "Hero", "field_count": 1}],
                "reconciled_fields": 2,
                "dropped_fields": 1,
                "backfilled_fields": 3,
            },
        )

        response = self.client.get(
            reverse("dashboard:template_annotate_status", args=[job.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "job_id": str(job.id),
                "status": "done",
                "html": "<section></section>",
                "sections": [
                    {"id": "hero", "label": "Hero", "field_count": 1}
                ],
                "reconciled_fields": 2,
                "dropped_fields": 1,
                "backfilled_fields": 3,
            },
        )

    def test_status_keeps_legacy_list_shaped_summaries_compatible(self):
        sections = [{"id": "hero", "label": "Hero", "field_count": 1}]
        job = AnnotationJob.objects.create(
            created_by=self.staff,
            status=AnnotationJob.STATUS_DONE,
            result_html="<section></section>",
            sections=sections,
        )

        response = self.client.get(
            reverse("dashboard:template_annotate_status", args=[job.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sections"], sections)
