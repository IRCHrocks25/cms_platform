"""``prune_content_defaults`` heals sites created before sparse content.

The code fix stops *new* default copies being stored. It cannot help a site
whose row already holds 340 of them; those keep overriding the template. This
command is what makes an existing site follow its template again.
"""
from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.parser import build_schema
from core.renderer import merge_with_defaults

User = get_user_model()

HTML_V1_FOR_SHIFT = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-TWO</p>
</section>
"""

HTML = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-TWO</p>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class PruneContentDefaultsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("op", "op@ex.com", "x")
        self.tpl = Template.objects.create(name="facts", html_source=HTML)
        fat = merge_with_defaults(self.tpl.schema, {})
        fat["facts"]["p_2"] = "MY OWN WORDS"
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.user,
            content=fat,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("prune_content_defaults", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_reports_without_writing(self):
        out = self._run("--site", "alpha")
        self.assertIn("Would prune", out)
        self.tenant.refresh_from_db()
        self.assertIn("p_1", self.tenant.content["facts"])

    def test_apply_keeps_only_authored_values(self):
        self._run("--site", "alpha", "--apply")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"facts": {"p_2": "MY OWN WORDS"}})

    def test_rendered_output_is_unchanged_by_the_prune(self):
        before = merge_with_defaults(self.tpl.schema, self.tenant.content)
        self._run("--site", "alpha", "--apply")
        self.tenant.refresh_from_db()
        after = merge_with_defaults(self.tpl.schema, self.tenant.content)
        self.assertEqual(before, after)

    def test_unknown_site_is_an_error_not_a_silent_no_op(self):
        out = self._run("--site", "nope")
        self.assertIn("No site with subdomain", out)

    def test_other_sites_are_untouched_when_scoped(self):
        other = Tenant.objects.create(
            name="Beta",
            subdomain="beta",
            template=self.tpl,
            owner=self.user,
            content=merge_with_defaults(self.tpl.schema, {}),
        )
        self._run("--site", "alpha", "--apply")
        other.refresh_from_db()
        self.assertIn("p_1", other.content["facts"])


SHIFTED = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">NEW-INTRO</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_3" data-type="richtext">SOURCE-TWO</p>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class DisplacedRowRepairTests(TestCase):
    """Equality with the *current* default cannot heal a displaced row.

    Found by review. After a renumbering the stored value matches nothing at
    its own id, so the plain prune leaves every displaced value in place and
    the site still renders the old copy one element late. Repair needs either
    an archived schema to compare against, or an explicit operator-reviewed
    clear of the generated ids.
    """

    def setUp(self):
        self.user = User.objects.create_user("op2", "op2@ex.com", "x")
        # Template as it was when the site was seeded.
        self.tpl = Template.objects.create(name="facts", html_source=HTML_V1_FOR_SHIFT)
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.user,
            content=merge_with_defaults(self.tpl.schema, {}),
        )
        # Re-annotation inserts a paragraph; every generated id shifts.
        self.tpl.html_source = SHIFTED
        self.tpl.save()
        self.tenant.refresh_from_db()

    def _run(self, *args):
        out = StringIO()
        call_command("prune_content_defaults", *args, stdout=out, stderr=out)
        return out.getvalue()

    def _render(self):
        from core.renderer import render_site

        self.tenant.refresh_from_db()
        return render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )

    def test_the_row_really_is_displaced_before_repair(self):
        """Guards the diagnosis: without this the repair tests prove nothing."""
        out = self._render()
        self.assertNotIn("NEW-INTRO", out)
        self.assertEqual(out.count("SOURCE-TWO"), 2)

    def test_a_plain_prune_does_not_repair_it(self):
        self._run("--site", "alpha", "--apply")
        out = self._render()
        self.assertNotIn("NEW-INTRO", out)

    def test_clear_generated_repairs_it(self):
        self._run("--site", "alpha", "--clear-generated", "--apply")
        out = self._render()
        self.assertIn("NEW-INTRO", out)
        self.assertEqual(out.count("SOURCE-TWO"), 1)

    def test_clear_generated_dry_run_lists_every_value_it_would_drop(self):
        out = self._run("--site", "alpha", "--clear-generated")
        self.assertIn("facts.p_1", out)
        self.assertIn("SOURCE-ONE", out)
        self.tenant.refresh_from_db()
        self.assertIn("p_1", self.tenant.content["facts"])

    def test_clear_generated_leaves_semantic_ids_alone(self):
        self.tenant.content = {"facts": {"p_1": "x", "title": "MY HEADLINE"}}
        self.tenant.save(update_fields=["content", "updated_at"])
        self._run("--site", "alpha", "--clear-generated", "--apply")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"facts": {"title": "MY HEADLINE"}})

    def test_across_versions_drops_values_that_were_an_archived_default(self):
        """When the old template state was archived, authorship is knowable
        without asking the operator to accept a blind clear."""
        from core.models import TemplateVersion

        TemplateVersion.objects.create(
            template=self.tpl,
            number=1,
            html_source=HTML_V1_FOR_SHIFT,
            schema=build_schema(HTML_V1_FOR_SHIFT),
            label="before re-annotation",
            saved_by=self.user,
        )
        self.tenant.content = {"facts": {"p_1": "SOURCE-ONE", "p_2": "MY OWN WORDS"}}
        self.tenant.save(update_fields=["content", "updated_at"])
        self._run("--site", "alpha", "--across-versions", "--apply")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"facts": {"p_2": "MY OWN WORDS"}})


@override_settings(TENANT_BASE_DOMAIN="localhost")
class DropFlattenedTests(TestCase):
    """A stored value that is the flattened form of the current default is the
    damage, not an edit.

    After ``rederive_template_schemas`` corrects the default to
    ``Navigate menopause. <span>…</span>``, the row still holds the flat
    ``Navigate menopause.Naturally, powerfully.``, which no longer equals the
    default, so the plain prune keeps it and the span stays gone. Nobody types
    a headline with the space missing between two spans, so this one *is*
    inferrable, unlike a displaced value.
    """

    ACCENT = (
        "<section data-section='hero'>"
        '<h1 data-edit="hero.title" data-type="text">Navigate menopause. '
        '<span class="italic">Naturally, powerfully.</span></h1>'
        "</section>"
    )

    def setUp(self):
        self.user = User.objects.create_user("op3", "op3@ex.com", "x")
        self.tpl = Template.objects.create(name="hero", html_source=self.ACCENT)
        self.tenant = Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.user,
            content={"hero": {"title": "Navigate menopause.Naturally, powerfully."}},
        )

    def _run(self, *args):
        out = StringIO()
        call_command("prune_content_defaults", *args, stdout=out, stderr=out)
        return out.getvalue()

    def _render(self):
        from core.renderer import render_site

        self.tenant.refresh_from_db()
        return render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )

    def test_a_plain_prune_keeps_the_flattened_value(self):
        self._run("--site", "alpha", "--apply")
        self.assertNotIn('class="italic"', self._render())

    def test_drop_flattened_restores_the_span(self):
        self._run("--site", "alpha", "--drop-flattened", "--apply")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {})
        self.assertIn('class="italic"', self._render())

    def test_a_real_edit_that_happens_to_be_plain_text_is_kept(self):
        self.tenant.content = {"hero": {"title": "Something the client typed"}}
        self.tenant.save(update_fields=["content", "updated_at"])
        self._run("--site", "alpha", "--drop-flattened", "--apply")
        self.tenant.refresh_from_db()
        self.assertEqual(
            self.tenant.content, {"hero": {"title": "Something the client typed"}}
        )

    def test_a_client_edit_keeping_the_markup_is_kept(self):
        edited = 'Navigate it. <span class="italic">Your way.</span>'
        self.tenant.content = {"hero": {"title": edited}}
        self.tenant.save(update_fields=["content", "updated_at"])
        self._run("--site", "alpha", "--drop-flattened", "--apply")
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"hero": {"title": edited}})
