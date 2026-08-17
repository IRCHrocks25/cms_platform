"""Regression guards for the template HTML save path.

Covers the two defects behind the 2026-08-17 incident: a blank ``html_source``
being silently treated as "keep the current HTML", and the field-loss conflict
page handing the operator back the OLD HTML so confirming it re-saved the bytes
they were trying to replace.
"""
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.parser import build_schema
from core.renderer import merge_with_defaults
from core.services import templates as template_svc

OLD = (
    '<html><body>'
    '<section data-section="hero" data-label="Hero">'
    '<h1 data-edit="hero.title" data-type="text">Old headline</h1>'
    '<p data-edit="hero.sub" data-type="text">Old sub</p>'
    '</section></body></html>'
)
NEW_SAME_FIELDS = OLD.replace("Old headline", "New headline")
NEW_DROPS_FIELDS = '<html><body><h1>Unannotated rewrite</h1></body></html>'

# The repo ships a hashed-manifest staticfiles storage; a bare checkout has no
# built manifest, so any test that renders a dashboard template needs this.
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class BlankHtmlRejectedTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.tpl = Template.objects.create(name="T", description="d", html_source=OLD)

    def _post(self, **extra):
        data = {"name": "Renamed", "description": "new desc",
                "editing_mode": "editable"}
        data.update(extra)
        return self.client.post(f"/dashboard/templates/{self.tpl.pk}/", data)

    def test_missing_html_source_is_rejected(self):
        before = self.tpl.versions.count()
        resp = self._post()
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)
        self.assertEqual(self.tpl.versions.count(), before)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn("HTML source cannot be empty.", msgs)

    def test_whitespace_only_html_source_is_rejected(self):
        resp = self._post(html_source="   \n\t  ")
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.tpl.html_source, OLD)

    def test_rejection_does_not_persist_metadata(self):
        self._post()
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.name, "T")
        self.assertEqual(self.tpl.description, "d")

    def test_rejection_redisplays_what_the_operator_typed(self):
        resp = self._post()
        body = resp.content.decode()
        self.assertIn("Renamed", body)
        self.assertIn("new desc", body)
        self.assertIn("Old headline", body)


@override_settings(STORAGES=PLAIN_STATIC)
class FieldLossKeepsCandidateTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops2", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.tpl = Template.objects.create(name="T", html_source=OLD)
        owner = User.objects.create_user("client2", password="pw")
        Tenant.objects.create(
            name="Denny", subdomain="denny", template=self.tpl, owner=owner,
            content=merge_with_defaults(build_schema(OLD), {}),
            is_published=True,
        )

    def _post(self, html, allow=False):
        data = {"name": "T", "description": "", "editing_mode": "editable",
                "html_source": html}
        if allow:
            data["allow_field_loss"] = "1"
        return self.client.post(f"/dashboard/templates/{self.tpl.pk}/", data)

    def test_conflict_page_shows_the_submitted_html_not_the_old_one(self):
        resp = self._post(NEW_DROPS_FIELDS)
        self.assertEqual(resp.status_code, 409)
        body = resp.content.decode()
        self.assertIn("Unannotated rewrite", body)
        self.assertNotIn("Old headline", body)

    def test_confirming_the_conflict_saves_the_new_html(self):
        self._post(NEW_DROPS_FIELDS)
        resp = self._post(NEW_DROPS_FIELDS, allow=True)
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.tpl.html_source, NEW_DROPS_FIELDS)


class NoOpVersionTest(TestCase):
    """A byte-identical save must not manufacture a version row."""

    def setUp(self):
        self.user = User.objects.create_user("ops3", password="pw", is_staff=True)
        self.tpl = Template.objects.create(name="T", html_source=OLD)

    def test_first_save_creates_v1_even_though_bytes_match(self):
        self.tpl.versions.all().delete()
        result = template_svc.save_template_version(
            self.tpl, OLD, user=self.user, label="Initial",
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), 1)

    def test_identical_resave_creates_no_version(self):
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(self.tpl, OLD, user=self.user)
        self.assertTrue(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before)

    def test_version_is_cut_when_latest_does_not_archive_current_bytes(self):
        # A direct Template.save() can move html_source without a version.
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        self.tpl.html_source = NEW_SAME_FIELDS
        self.tpl.save()
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(
            self.tpl, NEW_SAME_FIELDS, user=self.user,
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before + 1)

    def test_changed_html_still_creates_a_version(self):
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        before = self.tpl.versions.count()
        result = template_svc.save_template_version(
            self.tpl, NEW_SAME_FIELDS, user=self.user,
        )
        self.assertFalse(result.unchanged)
        self.assertEqual(self.tpl.versions.count(), before + 1)

    def test_stale_if_match_is_refused_under_the_lock(self):
        template_svc.save_template_version(self.tpl, OLD, user=self.user)
        with self.assertRaises(template_svc.ConcurrentWriteError):
            template_svc.save_template_version(
                self.tpl, NEW_SAME_FIELDS, user=self.user,
                expect_html_etag="deadbeef",
            )
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, OLD)


@override_settings(STORAGES=PLAIN_STATIC)
class NoOpKeepsMetadataTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("ops4", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.tpl = Template.objects.create(name="Before", description="", html_source=OLD)
        # A template with no versions must still cut v1, so archive the current
        # bytes first; only then is a re-post of the same HTML a true no-op.
        template_svc.save_template_version(self.tpl, OLD, user=self.staff, label="Initial")

    def test_unchanged_html_still_saves_a_rename(self):
        resp = self.client.post(
            f"/dashboard/templates/{self.tpl.pk}/",
            {"name": "After", "description": "now described",
             "editing_mode": "editable", "html_source": OLD},
        )
        self.tpl.refresh_from_db()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.tpl.name, "After")
        self.assertEqual(self.tpl.description, "now described")
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertIn("No HTML changes — metadata saved.", msgs)


class AdminCannotBypassTheServiceTest(TestCase):
    def test_structural_fields_are_readonly_in_admin(self):
        from django.contrib import admin as dj_admin

        from core.models import Page as PageModel

        self.assertIn("html_source", dj_admin.site._registry[Template].readonly_fields)
        self.assertIn("content", dj_admin.site._registry[Tenant].readonly_fields)
        self.assertIn("content", dj_admin.site._registry[PageModel].readonly_fields)
