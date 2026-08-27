"""A template save must refuse to silently re-point an existing field id at a
different element.

``save_template_version`` already guards field *loss*: ids that disappear. It
cannot see the more damaging case: an id that survives but now owns a different
piece of the page. Re-annotating after inserting a paragraph renumbers the
generated ``p_N`` / ``li_N`` ids, so ``facts.p_2`` still exists and the loss
guard waves the save through, while every client edit stored against it lands
one element late.

Sparse content (see ``test_sparse_content_overrides``) means unedited copy now
follows the template instead of the stored row, so this only bites fields the
client actually authored. That is exactly the content worth refusing to move
without a human saying so.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import Template, Tenant
from core.services.templates import (
    FieldDriftError,
    FieldLossError,
    save_template_version,
)

#: Either guard may fire first; both carry the other's findings now.
template_svc_errors = (FieldLossError, FieldDriftError)

User = get_user_model()

V1 = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-TWO</p>
  <p data-edit="facts.p_3" data-type="richtext">SOURCE-THREE</p>
</section>
"""

# A paragraph inserted at the top; every generated id shifts by one.
V2_SHIFTED = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">NEW-INTRO</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-ONE</p>
  <p data-edit="facts.p_3" data-type="richtext">SOURCE-TWO</p>
  <p data-edit="facts.p_4" data-type="richtext">SOURCE-THREE</p>
</section>
"""

# Ordinary copy editing: same ids, same elements, new words.
V2_REWORDED = """
<section data-section="facts" data-label="Facts">
  <p data-edit="facts.p_1" data-type="richtext">SOURCE-ONE, revised</p>
  <p data-edit="facts.p_2" data-type="richtext">SOURCE-TWO, revised</p>
  <p data-edit="facts.p_3" data-type="richtext">SOURCE-THREE, revised</p>
</section>
"""


@override_settings(TENANT_BASE_DOMAIN="localhost")
class FieldDriftGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("op", "op@ex.com", "x", is_staff=True)
        self.tpl = Template.objects.create(name="facts", html_source=V1)

    def _tenant(self, content):
        return Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.user,
            content=content,
            is_published=True,
        )

    def test_shift_is_refused_when_a_published_site_authored_a_drifted_field(self):
        self._tenant({"facts": {"p_2": "MY OWN WORDS"}})
        with self.assertRaises(FieldDriftError) as ctx:
            save_template_version(self.tpl, V2_SHIFTED, user=self.user)
        self.assertIn("facts.p_2", ctx.exception.drifted_fields)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, V1)

    def test_the_operator_can_override(self):
        self._tenant({"facts": {"p_2": "MY OWN WORDS"}})
        result = save_template_version(
            self.tpl, V2_SHIFTED, user=self.user, allow_field_drift=True
        )
        self.assertIn("facts.p_2", result.drifted_fields)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, V2_SHIFTED)

    def test_no_authored_content_means_no_refusal(self):
        """Sparse content already handles this case correctly; don't nag."""
        self._tenant({})
        save_template_version(self.tpl, V2_SHIFTED, user=self.user)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, V2_SHIFTED)

    def test_unpublished_sites_do_not_block_a_save(self):
        Tenant.objects.create(
            name="Draft",
            subdomain="draft",
            template=self.tpl,
            owner=self.user,
            content={"facts": {"p_2": "MY OWN WORDS"}},
            is_published=False,
        )
        save_template_version(self.tpl, V2_SHIFTED, user=self.user)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, V2_SHIFTED)

    def test_plain_copy_editing_is_not_drift(self):
        """Rewording every paragraph in place changes defaults but moves no id."""
        self._tenant({"facts": {"p_2": "MY OWN WORDS"}})
        save_template_version(self.tpl, V2_REWORDED, user=self.user)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, V2_REWORDED)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class DriftDetectionPrecisionTests(TestCase):
    """Text equality is not a DOM-owner fingerprint.

    Found by review, both directions verified against the old helper:
      false-positive drift on reword: {'facts.p_1'}
      drift detected on insertion:    ['facts.p_2', 'facts.p_3']   # p_1 also moved

    A section whose generated-id count changed is the real signal. Every
    generated id in it may now own a different element, including p_1, whose
    default happens to match nothing. An in-place reword leaves the count alone.
    """

    def setUp(self):
        self.user = User.objects.create_user("op3", "op3@ex.com", "x", is_staff=True)
        self.tpl = Template.objects.create(name="facts", html_source=V1)

    def _tenant(self, content):
        return Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.user,
            content=content,
            is_published=True,
        )

    def test_p_1_is_caught_even_though_its_new_text_matches_nothing(self):
        """The insertion repoints p_1 at NEW-INTRO. Its default matches no old
        value, so text equality missed it and the save went through."""
        self._tenant({"facts": {"p_1": "MY OWN WORDS"}})
        with self.assertRaises(FieldDriftError) as ctx:
            save_template_version(self.tpl, V2_SHIFTED, user=self.user)
        self.assertIn("facts.p_1", ctx.exception.drifted_fields)

    def test_rewording_into_a_neighbours_old_words_is_not_drift(self):
        """p_1 edited from SOURCE-ONE to SOURCE-TWO, which is what p_2 said.
        No element moved. Text equality called this drift and blocked it."""
        reworded = V1.replace(">SOURCE-ONE<", ">SOURCE-TWO<")
        self._tenant({"facts": {"p_1": "MY OWN WORDS"}})
        save_template_version(self.tpl, reworded, user=self.user)
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, reworded)

    def test_a_renumbering_whose_copy_was_also_rewritten_is_caught(self):
        """Text equality saw nothing at all here; every default is new."""
        rewritten = """
        <section data-section="facts" data-label="Facts">
          <p data-edit="facts.p_1" data-type="richtext">ALPHA</p>
          <p data-edit="facts.p_2" data-type="richtext">BETA</p>
          <p data-edit="facts.p_3" data-type="richtext">GAMMA</p>
          <p data-edit="facts.p_4" data-type="richtext">DELTA</p>
        </section>
        """
        self._tenant({"facts": {"p_2": "MY OWN WORDS"}})
        with self.assertRaises(FieldDriftError):
            save_template_version(self.tpl, rewritten, user=self.user)

    def test_an_authored_empty_string_still_counts_as_content(self):
        """_value_nonempty('') is False, so a deliberate blanking slipped the
        guard and would blank whichever element the id now points at."""
        self._tenant({"facts": {"p_2": ""}})
        with self.assertRaises(FieldDriftError):
            save_template_version(self.tpl, V2_SHIFTED, user=self.user)

    def test_named_ids_are_not_treated_as_generated(self):
        """Adding a paragraph to a section whose ids are all semantic moves
        nothing; the ids say what they own."""
        named_v1 = (
            "<section data-section='facts'>"
            "<p data-edit='facts.intro' data-type='richtext'>ONE</p>"
            "<p data-edit='facts.outro' data-type='richtext'>TWO</p>"
            "</section>"
        )
        named_v2 = (
            "<section data-section='facts'>"
            "<p data-edit='facts.kicker' data-type='richtext'>NEW</p>"
            "<p data-edit='facts.intro' data-type='richtext'>ONE</p>"
            "<p data-edit='facts.outro' data-type='richtext'>TWO</p>"
            "</section>"
        )
        tpl = Template.objects.create(name="named", html_source=named_v1)
        Tenant.objects.create(
            name="Named",
            subdomain="named",
            template=tpl,
            owner=self.user,
            content={"facts": {"intro": "MY OWN WORDS"}},
            is_published=True,
        )
        save_template_version(tpl, named_v2, user=self.user)
        tpl.refresh_from_db()
        self.assertEqual(tpl.html_source, named_v2)


@override_settings(TENANT_BASE_DOMAIN="localhost")
class BothGuardsInOnePassTests(TestCase):
    """A candidate that drops one id and shifts others used to deadlock: the
    first response offered only the loss checkbox, the retry offered only the
    drift checkbox and forgot the loss confirmation, and round it went."""

    def setUp(self):
        self.user = User.objects.create_user("op4", "op4@ex.com", "x", is_staff=True)
        with_named = V1.replace(
            "</section>",
            '  <p data-edit="facts.intro" data-type="richtext">INTRO</p>\n</section>',
        )
        self.tpl = Template.objects.create(name="facts", html_source=with_named)
        Tenant.objects.create(
            name="Alpha",
            subdomain="alpha",
            template=self.tpl,
            owner=self.user,
            content={"facts": {"p_2": "MY OWN WORDS", "intro": "ALSO MINE"}},
            is_published=True,
        )

    #: drops the named facts.intro and inserts a paragraph, so the generated
    #: ids renumber. Loss fires on the named id, drift on the count increase.
    LOSS_AND_DRIFT = """
    <section data-section="facts" data-label="Facts">
      <p data-edit="facts.p_1" data-type="richtext">NEW-INTRO</p>
      <p data-edit="facts.p_2" data-type="richtext">SOURCE-ONE</p>
      <p data-edit="facts.p_3" data-type="richtext">SOURCE-TWO</p>
      <p data-edit="facts.p_4" data-type="richtext">SOURCE-THREE</p>
    </section>
    """

    def test_the_first_refusal_reports_both_problems(self):
        with self.assertRaises(template_svc_errors) as ctx:
            save_template_version(self.tpl, self.LOSS_AND_DRIFT, user=self.user)
        exc = ctx.exception
        self.assertTrue(getattr(exc, "lost_fields", None))
        self.assertTrue(getattr(exc, "drifted_fields", None))

    def test_confirming_both_at_once_saves(self):
        save_template_version(
            self.tpl,
            self.LOSS_AND_DRIFT,
            user=self.user,
            allow_field_loss=True,
            allow_field_drift=True,
        )
        self.tpl.refresh_from_db()
        self.assertEqual(self.tpl.html_source, self.LOSS_AND_DRIFT)
