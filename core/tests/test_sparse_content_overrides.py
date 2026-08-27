"""``Tenant.content`` holds client-authored overrides only, never a full copy
of the template defaults.

Seeding content with every default (and rewriting every default back on every
editor save) made stored values permanently sticky: the tenant row kept
answering with the value a field had at import time, forever, no matter what
the template later said. That is what turned a benign re-annotation into
silent, sitewide content displacement.

The failure shape, reproduced in ``test_reused_generated_id_cannot_displace_
unedited_copy``: an agency inserts a paragraph, the annotator renumbers the
generated ``p_N`` ids, and every stored default lands one element late. The
template-save field-loss guard cannot see it, because no id was removed:
``facts.p_2`` still exists, it just points somewhere else now.

Live example: themenopausecoach.com, where 63 of 100 damaged fields were
displaced this way.
"""
from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.parser import build_schema
from core.renderer import merge_with_defaults, render_site, strip_defaults
from core.services import content_versions as cv
from core.services.accounts import create_tenant_account

User = get_user_model()

FACTS_V1 = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-TWO</p>
  <p data-edit="facts.p_3" data-type="richtext">SOURCE-THREE</p>
</section>
"""

# Same section after a new intro paragraph is annotated in. Every generated id
# now points one element later than it did in V1. No id was removed.
FACTS_V2 = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">NEW-INTRO</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_3" data-type="richtext">SOURCE-TWO</p>
  <p data-edit="facts.p_4" data-type="richtext">SOURCE-THREE</p>
</section>
"""


class StripDefaultsTests(TestCase):
    def test_values_equal_to_defaults_are_dropped(self):
        schema = build_schema(FACTS_V1)
        full = merge_with_defaults(schema, {})
        self.assertEqual(strip_defaults(schema, full), {})

    def test_authored_values_are_kept(self):
        schema = build_schema(FACTS_V1)
        content = merge_with_defaults(schema, {})
        content["facts"]["p_2"] = "SOURCE-TWO, revised"
        self.assertEqual(
            strip_defaults(schema, content),
            {"facts": {"p_2": "SOURCE-TWO, revised"}},
        )

    def test_meta_namespaces_survive(self):
        """``_styles`` / ``_hidden`` / ``_tokens`` are editor state, not fields."""
        schema = build_schema(FACTS_V1)
        content = merge_with_defaults(schema, {})
        content["_hidden"] = ["facts.p_3"]
        content["_styles"] = {"facts.p_1": {"color": "#c4714b"}}
        stripped = strip_defaults(schema, content)
        self.assertEqual(stripped["_hidden"], ["facts.p_3"])
        self.assertEqual(stripped["_styles"], {"facts.p_1": {"color": "#c4714b"}})

    def test_richtext_round_trip_drift_is_not_mistaken_for_an_edit(self):
        """BeautifulSoup is not byte-idempotent. A field that only differs by
        attribute quoting must still count as unedited, or it gets stored and
        goes sticky again."""
        html = (
            "<section data-section='hero'>"
            "<h2 data-edit='hero.title' data-type='richtext'>"
            "Hello <span class='accent'>world</span></h2>"
            "</section>"
        )
        schema = build_schema(html)
        content = {"hero": {"title": 'Hello <span class="accent">world</span>'}}
        self.assertEqual(strip_defaults(schema, content), {})

    def test_unknown_sections_and_fields_pass_through(self):
        """Content for a field the current template no longer declares is kept.
        Dropping it would destroy client copy on a bad template save."""
        schema = build_schema(FACTS_V1)
        content = {"facts": {"p_9": "orphan"}, "ghost": {"x": "y"}}
        self.assertEqual(strip_defaults(schema, content), content)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class ContentStaysSparseOnWriteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("editor", "e@ex.com", "x")
        self.tpl = Template.objects.create(name="facts", html_source=FACTS_V1)
        self.tenant = Tenant.objects.create(
            name="Alpha", subdomain="alpha", template=self.tpl, owner=self.user
        )

    def test_new_account_starts_with_empty_content(self):
        library = Template.objects.create(name="seed", html_source=FACTS_V1)
        tenant, _user, _pw = create_tenant_account(
            name="Beta",
            subdomain="beta",
            custom_domain="",
            username="beta-owner",
            email="b@ex.com",
            template=library,
        )
        self.assertEqual(tenant.content, {})

    def test_saving_the_untouched_form_stores_nothing(self):
        """The editor POSTs every field, pre-filled from merge_with_defaults.
        A save with no edits must not persist 300 defaults."""
        cv.save_tenant_content(
            self.tenant,
            merge_with_defaults(self.tpl.schema, {}),
            user=self.user,
            source=cv.SOURCE_DASHBOARD,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {})

    def test_saving_one_edit_stores_only_that_edit(self):
        content = merge_with_defaults(self.tpl.schema, {})
        content["facts"]["p_2"] = "SOURCE-TWO, revised"
        cv.save_tenant_content(
            self.tenant, content, user=self.user, source=cv.SOURCE_DASHBOARD
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"facts": {"p_2": "SOURCE-TWO, revised"}})

    def test_reused_generated_id_cannot_displace_unedited_copy(self):
        """The whole point. Save the untouched form, then re-annotate the
        template so every ``p_N`` shifts by one. Unedited copy must follow the
        new template, not the old stored defaults."""
        cv.save_tenant_content(
            self.tenant,
            merge_with_defaults(self.tpl.schema, {}),
            user=self.user,
            source=cv.SOURCE_DASHBOARD,
        )
        self.tpl.html_source = FACTS_V2
        self.tpl.save()
        self.tenant.refresh_from_db()

        out = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        for expected in ("NEW-INTRO", "SOURCE-ONE", "SOURCE-TWO", "SOURCE-THREE"):
            self.assertIn(expected, out)
        # The old bug rendered SOURCE-THREE twice and dropped NEW-INTRO.
        self.assertEqual(out.count("SOURCE-THREE"), 1)

    def test_an_authored_edit_still_wins_over_the_template_default(self):
        content = merge_with_defaults(self.tpl.schema, {})
        content["facts"]["p_1"] = "MY OWN WORDS"
        cv.save_tenant_content(
            self.tenant, content, user=self.user, source=cv.SOURCE_DASHBOARD
        )
        self.tenant.refresh_from_db()
        out = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        self.assertIn("MY OWN WORDS", out)
        self.assertNotIn("SOURCE-ONE", out)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class RestoreKeepsContentSparseTests(TestCase):
    """``restore_tenant_content`` assigned ``version.snapshot`` verbatim.

    Every snapshot taken before sparse content is a full copy of the defaults,
    so restoring one after a prune put all of them straight back, and if the
    template had moved on, re-created the displacement the prune just cleared.
    """

    def setUp(self):
        self.user = User.objects.create_user("editor2", "e2@ex.com", "x")
        self.tpl = Template.objects.create(name="facts", html_source=FACTS_V1)
        self.tenant = Tenant.objects.create(
            name="Alpha", subdomain="alpha", template=self.tpl, owner=self.user
        )

    def _legacy_snapshot(self):
        from core.models import ContentVersion

        fat = merge_with_defaults(self.tpl.schema, {})
        return ContentVersion.objects.create(
            tenant=self.tenant, snapshot=fat, saved_by=self.user, source=cv.SOURCE_DASHBOARD
        )

    def test_restoring_a_legacy_fat_snapshot_stores_nothing(self):
        version = self._legacy_snapshot()
        cv.restore_tenant_content(self.tenant, version, user=self.user)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {})

    def test_restoring_keeps_the_authored_values_in_the_snapshot(self):
        from core.models import ContentVersion

        fat = merge_with_defaults(self.tpl.schema, {})
        fat["facts"]["p_2"] = "MY OWN WORDS"
        version = ContentVersion.objects.create(
            tenant=self.tenant, snapshot=fat, saved_by=self.user, source=cv.SOURCE_DASHBOARD
        )
        cv.restore_tenant_content(self.tenant, version, user=self.user)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content, {"facts": {"p_2": "MY OWN WORDS"}})

    def test_restoring_after_the_template_moved_on_cannot_attribute_the_values(self):
        """The honest limit, asserted so nobody assumes otherwise.

        A snapshot taken under template V1 and restored under V2 holds values
        that match no current default, so the strip keeps them and the row is
        displaced again. Nothing in the snapshot says whether the client typed
        those words or the old template supplied them. That is the same
        unknowable authorship the prune command hits, and the operator's answer
        is ``prune_content_defaults --clear-generated``.
        """
        version = self._legacy_snapshot()
        self.tpl.html_source = FACTS_V2
        self.tpl.save()
        cv.restore_tenant_content(self.tenant, version, user=self.user)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.content["facts"]["p_1"], "SOURCE-ONE")

        call_command(
            "prune_content_defaults",
            "--site", "alpha", "--clear-generated", "--apply",
            stdout=StringIO(),
        )
        self.tenant.refresh_from_db()
        out = render_site(
            self.tpl.html_source,
            merge_with_defaults(self.tpl.schema, self.tenant.content),
        )
        self.assertIn("NEW-INTRO", out)
        self.assertEqual(out.count("SOURCE-THREE"), 1)
