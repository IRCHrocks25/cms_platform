"""_annotate_template_in_background must apply the annotated HTML and rebuild
the template schema. Regression: it treated annotate_html's return (a str) as
an object (`result.html` / `result.sections`), which raised AttributeError,
got swallowed, and left imported pages with raw HTML + an empty schema — so the
editor showed no fields even though the page rendered fine.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import AnnotationJob, Template
from core.services.annotator import AnnotationResult, AnnotatorError


PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}


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

    def test_records_sibling_annotation_failure_on_its_job(self):
        raw = "<div><h1>Hi</h1></div>"
        tpl = Template.objects.create(name="failed", html_source=raw)
        job = AnnotationJob.objects.create()
        from dashboard.views import _annotate_template_in_background

        with mock.patch(
            "dashboard.views.annotate_html",
            side_effect=AnnotatorError("Provider rejected the request."),
        ), mock.patch.object(connection, "close"):
            _annotate_template_in_background(tpl.pk, raw, str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, AnnotationJob.STATUS_ERROR)
        self.assertEqual(job.error, "Provider rejected the request.")

    def test_records_sibling_annotation_success_only_after_template_save(self):
        raw = "<div><h1>Hi</h1></div>"
        tpl = Template.objects.create(name="successful", html_source=raw)
        job = AnnotationJob.objects.create()
        annotated = (
            "<section data-section='hero' data-label='Hero'>"
            "<h1 data-edit='hero.title'>Hi</h1></section>"
        )
        from dashboard.views import _annotate_template_in_background

        with mock.patch(
            "dashboard.views.annotate_html", return_value=annotated
        ), mock.patch.object(connection, "close"):
            _annotate_template_in_background(tpl.pk, raw, str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, AnnotationJob.STATUS_DONE)
        self.assertEqual(job.result_html, annotated)
        self.assertEqual(job.sections["items"][0]["id"], "hero")


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
            model="gpt-5.6-luna",
            prompt_tokens=101,
            completion_tokens=52,
            reasoning_tokens=17,
            total_tokens=153,
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
                "model": "gpt-5.6-luna",
                "prompt_tokens": 101,
                "completion_tokens": 52,
                "reasoning_tokens": 17,
                "total_tokens": 153,
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
                "model": "gpt-5.6-luna",
                "prompt_tokens": 101,
                "completion_tokens": 52,
                "reasoning_tokens": 17,
                "total_tokens": 153,
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
                "model": "gpt-5.6-luna",
                "prompt_tokens": 101,
                "completion_tokens": 52,
                "reasoning_tokens": 17,
                "total_tokens": 153,
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

    def test_status_preserves_the_workers_exact_error_message(self):
        message = "The model returned invalid JSON after chunk 2."
        job = AnnotationJob.objects.create(
            created_by=self.staff,
            status=AnnotationJob.STATUS_ERROR,
            error=message,
        )

        response = self.client.get(
            reverse("dashboard:template_annotate_status", args=[job.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], message)

    def test_missing_job_returns_the_exact_server_message(self):
        response = self.client.get(
            reverse(
                "dashboard:template_annotate_status",
                args=["00000000-0000-0000-0000-000000000001"],
            )
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "Job not found."})


@override_settings(STORAGES=PLAIN_STATIC)
class AnnotationEditorUiTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            "annotation-ui-operator", password="pw", is_staff=True
        )
        self.client.force_login(self.staff)

    def _source(self):
        response = self.client.get(reverse("dashboard:template_create"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _new_site_source(self):
        response = self.client.get(reverse("dashboard:tenant_create"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_poll_fails_immediately_for_non_2xx_and_error_only_json(self):
        source = self._source()

        self.assertIn("if (!r.ok) return fail(responseMessage", source)
        self.assertIn("if (r.body.error && !r.body.status)", source)

    def test_overlay_has_explicit_error_retry_and_close_controls(self):
        source = self._source()

        self.assertIn('id="compare-loading-retry"', source)
        self.assertIn('id="compare-loading-close"', source)
        self.assertIn("compareLoading.classList.add(\"is-error\")", source)

    def test_zero_sections_is_an_explicit_warning_not_success(self):
        source = self._source()

        self.assertIn(
            "Applying this result will produce a template with no editable fields.",
            source,
        )
        self.assertIn('setStatus("warning"', source)
        self.assertIn('compareApply.textContent = "Apply without editable fields"', source)

    def test_compare_summary_reports_integrity_counts(self):
        source = self._source()

        self.assertIn("body.reconciled_fields", source)
        self.assertIn("body.dropped_fields", source)
        self.assertIn("body.backfilled_fields", source)

    def test_new_site_annotation_ui_has_the_same_terminal_failure_contract(self):
        source = self._new_site_source()

        self.assertIn("if (!r.ok) return fail(responseMessage", source)
        self.assertIn("if (r.body.error && !r.body.status)", source)
        self.assertIn('id="compare-loading-close"', source)
        self.assertIn("compareLoading.classList.add(\"is-error\")", source)

    def test_new_site_annotation_ui_reports_counts_and_zero_section_warning(self):
        source = self._new_site_source()

        self.assertIn("body.reconciled_fields", source)
        self.assertIn("body.dropped_fields", source)
        self.assertIn("body.backfilled_fields", source)
        self.assertIn(
            "Applying this result will produce a template with no editable fields.",
            source,
        )
