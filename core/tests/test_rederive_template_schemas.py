"""``Template.schema`` is stored, so a parser fix does not reach existing rows.

Public and preview rendering both merge against ``Template.schema`` from the
database, not a freshly parsed one. Every schema stored before the text-field
fix had its defaults extracted with ``get_text(strip=True)``: the flattened,
space-less form. Feed that stale default back through the fixed renderer and it
looks like a real edit, so the accent span is destroyed exactly as before. The
runtime type coercion cannot save a site whose stored default is already flat.

Re-saving the template does not fix it either: ``save_template_version``
returns early when the HTML is byte-identical, before ``Template.save()`` gets
a chance to re-derive.

Found by review, verified: "span survives w/ STALE schema: False".
"""
from __future__ import annotations

from io import StringIO

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Template
from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site
from core.services.templates import save_template_version

User = get_user_model()

HTML = (
    "<section data-section='hero'>"
    '<h1 data-edit="hero.title" data-type="text">Navigate menopause. '
    '<span class="italic">Naturally, powerfully.</span></h1>'
    "</section>"
)


def _stale_schema() -> dict:
    """What build_schema stored before the fix: type text, flattened default."""
    fresh = build_schema(HTML)
    flat = BeautifulSoup(HTML, "lxml").find("h1").get_text(strip=True)
    sections = [
        {
            **section,
            "fields": [{**f, "type": "text", "default": flat} for f in section["fields"]],
        }
        for section in fresh["sections"]
    ]
    return {"sections": sections, "defaults": {"hero": {"title": flat}}}


class StaleStoredSchemaTests(TestCase):
    def test_a_stale_stored_schema_still_flattens(self):
        """The bug this command exists to fix. Guards the diagnosis itself."""
        out = render_site(HTML, merge_with_defaults(_stale_schema(), {}))
        self.assertNotIn('class="italic"', out)

    def test_a_fresh_schema_does_not(self):
        out = render_site(HTML, merge_with_defaults(build_schema(HTML), {}))
        self.assertIn('class="italic"', out)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class RederiveCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("op", "op@ex.com", "x")
        self.tpl = Template.objects.create(name="hero", html_source=HTML)
        # Force the stored schema back to its pre-fix shape without touching HTML.
        Template.objects.filter(pk=self.tpl.pk).update(schema=_stale_schema())
        self.tpl.refresh_from_db()

    def _run(self, *args):
        out = StringIO()
        call_command("rederive_template_schemas", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_reports_the_stale_template_without_writing(self):
        out = self._run()
        self.assertIn("Would re-derive", out)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.schema["defaults"]["hero"]["title"], "Navigate menopause.Naturally, powerfully.")

    def test_apply_restores_the_span_in_the_default(self):
        self._run("--apply")
        self.tpl.refresh_from_db()
        self.assertIn('class="italic"', self.tpl.schema["defaults"]["hero"]["title"])
        self.assertEqual(self.tpl.schema["sections"][0]["fields"][0]["type"], "richtext")

    def test_apply_makes_the_public_render_correct(self):
        self._run("--apply")
        self.tpl.refresh_from_db()
        out = render_site(self.tpl.html_source, merge_with_defaults(self.tpl.schema, {}))
        self.assertIn('class="italic"', out)

    def test_html_is_never_touched(self):
        before = self.tpl.html_source
        self._run("--apply")
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, before)

    def test_an_already_current_template_is_left_alone(self):
        self._run("--apply")
        out = self._run("--apply")
        self.assertIn("0 template(s)", out)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ByteIdenticalSaveRederivesTests(TestCase):
    def test_resaving_identical_html_still_repairs_a_stale_schema(self):
        """The early return reported 'unchanged' and left the stale schema in
        place, so an operator re-pasting the same HTML got no repair."""
        user = User.objects.create_user("op2", "op2@ex.com", "x")
        tpl = Template.objects.create(name="hero", html_source=HTML)
        save_template_version(tpl, HTML, user=user)  # cut version 1
        Template.objects.filter(pk=tpl.pk).update(schema=_stale_schema())
        tpl.refresh_from_db()

        result = save_template_version(tpl, HTML, user=user)
        self.assertTrue(result.unchanged)
        tpl.refresh_from_db()
        self.assertIn('class="italic"', tpl.schema["defaults"]["hero"]["title"])
