"""Two clients converting a section with the same id must not share a block.

Found live 2026-09-02: nolan-group.sites.katek.app served the Rinaldi Group's
hero on its home page. Blocks were one global library keyed on `key` alone, so
two templates converting a section with the same id resolved to the same row
and the later conversion overwrote `html_source` for both.
"""
from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from importlib import import_module

from core.models import BlockType, Template
from core.services.blocks import apply_classic_upgrade

# The migration module name starts with a digit, so it cannot be imported with
# a normal import statement.
split_shared_blocks = import_module(
    "core.migrations.0027_blocktype_scoped_to_template"
).split_shared_blocks


def section_html(section_id, heading):
    """A converted page section: data-section, the client-markup shape."""
    return (
        "<!doctype html><html><body><main>"
        f'<section id="{section_id}" data-section="{section_id}" '
        f'data-label="Hero" data-group="Page sections">'
        f'<h1 data-edit="{section_id}.title" data-label="Hero title">{heading}</h1>'
        "</section></main></body></html>"
    )


def primitive_html(key):
    """A curated palette primitive: data-block, not data-section."""
    return f'<div data-block="{key}" data-label="{key}"><p>primitive</p></div>'


class ConversionIsolationTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_user("owner", password="x")
        self.a = Template.objects.create(
            name="Client A", slug="client-a",
            html_source=section_html("hero", "A country club realtor"),
        )
        self.b = Template.objects.create(
            name="Client B", slug="client-b",
            html_source=section_html("hero", "The Insider Power Couple"),
        )
        apply_classic_upgrade(self.a)
        apply_classic_upgrade(self.b)

    def test_each_template_gets_its_own_block_row(self):
        self.assertEqual(BlockType.objects.filter(key="hero", template=self.a).count(), 1)
        self.assertEqual(BlockType.objects.filter(key="hero", template=self.b).count(), 1)

    def test_second_conversion_does_not_overwrite_the_first_markup(self):
        a_block = BlockType.objects.get(key="hero", template=self.a)
        self.assertIn("A country club realtor", a_block.html_source)
        self.assertNotIn("Insider Power Couple", a_block.html_source)

    def test_each_template_allowlists_its_own_row_and_not_the_other_s(self):
        # Asserted positively so it cannot pass by both allowlists being empty,
        # which is how the first version of this test could pass vacuously.
        a_row = BlockType.objects.get(key="hero", template=self.a)
        b_row = BlockType.objects.get(key="hero", template=self.b)
        a_ids = set(self.a.allowed_block_types.values_list("id", flat=True))
        b_ids = set(self.b.allowed_block_types.values_list("id", flat=True))
        self.assertIn(a_row.id, a_ids)
        self.assertIn(b_row.id, b_ids)
        self.assertNotIn(b_row.id, a_ids)
        self.assertNotIn(a_row.id, b_ids)

    def test_the_markup_each_template_can_render_is_its_own(self):
        # The rows exist and are scoped; this proves what a render would pull.
        a_html = " ".join(
            self.a.allowed_block_types.filter(key="hero").values_list("html_source", flat=True)
        )
        self.assertIn("A country club realtor", a_html)
        self.assertNotIn("Insider Power Couple", a_html)


class ConstraintTests(TestCase):
    def test_two_templates_may_hold_the_same_key(self):
        t1 = Template.objects.create(name="t1", slug="t1", html_source=section_html("hero", "one"))
        t2 = Template.objects.create(name="t2", slug="t2", html_source=section_html("hero", "two"))
        BlockType.objects.create(template=t1, key="hero", html_source=section_html("hero", "one"))
        BlockType.objects.create(template=t2, key="hero", html_source=section_html("hero", "two"))
        self.assertEqual(BlockType.objects.filter(key="hero").count(), 2)

    def test_one_template_may_not_hold_a_key_twice(self):
        t = Template.objects.create(name="t", slug="t", html_source=section_html("hero", "x"))
        BlockType.objects.create(template=t, key="hero", html_source=section_html("hero", "x"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            BlockType.objects.create(template=t, key="hero", html_source=section_html("hero", "y"))

    def test_two_global_rows_may_not_share_a_key(self):
        # Postgres treats NULLs as distinct, so (template, key) alone would
        # allow this. The partial index on key WHERE template IS NULL stops it.
        BlockType.objects.create(template=None, key="button", html_source=primitive_html("button"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            BlockType.objects.create(
                template=None, key="button", html_source=primitive_html("button")
            )


class MigrationRepairTests(TestCase):
    """Exercises split_shared_blocks directly against the same model shapes."""

    def setUp(self):
        self.t1 = Template.objects.create(name="T1", slug="t1", html_source="<html></html>")
        self.t2 = Template.objects.create(name="T2", slug="t2", html_source="<html></html>")

    def run_repair(self):
        split_shared_blocks(global_apps, None)

    def test_row_shared_by_two_templates_is_split_and_repointed(self):
        shared = BlockType.objects.create(
            template=None, key="hero", html_source=section_html("hero", "whoever won")
        )
        self.t1.allowed_block_types.add(shared)
        self.t2.allowed_block_types.add(shared)
        self.run_repair()
        self.assertEqual(BlockType.objects.filter(key="hero").count(), 2)
        for t in (self.t1, self.t2):
            rows = list(t.allowed_block_types.filter(key="hero"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].template_id, t.id)

    def test_row_used_by_one_template_is_assigned_to_it(self):
        only = BlockType.objects.create(
            template=None, key="cta_ban", html_source=section_html("cta_ban", "x")
        )
        self.t1.allowed_block_types.add(only)
        self.run_repair()
        only.refresh_from_db()
        self.assertEqual(only.template_id, self.t1.id)

    def test_palette_key_holding_a_converted_section_is_still_split(self):
        # This is the finding that blocked the first version. `faq` is a palette
        # key AND a common section id, so keying on the name alone left a
        # client's annotated section global and still leaking.
        faq = BlockType.objects.create(
            template=None, key="faq", html_source=section_html("faq", "client copy")
        )
        self.t1.allowed_block_types.add(faq)
        self.t2.allowed_block_types.add(faq)
        self.run_repair()
        self.assertEqual(BlockType.objects.filter(key="faq", template__isnull=True).count(), 0)
        self.assertEqual(BlockType.objects.filter(key="faq").count(), 2)

    def test_genuine_palette_primitive_stays_global_and_shared(self):
        prim = BlockType.objects.create(
            template=None, key="faq", html_source=primitive_html("faq")
        )
        self.t1.allowed_block_types.add(prim)
        self.t2.allowed_block_types.add(prim)
        self.run_repair()
        prim.refresh_from_db()
        self.assertIsNone(prim.template_id)
        self.assertEqual(BlockType.objects.filter(key="faq").count(), 1)
        # Still the same row on both sides: a shared palette is the point.
        self.assertEqual(self.t1.allowed_block_types.get(key="faq").id, prim.id)
        self.assertEqual(self.t2.allowed_block_types.get(key="faq").id, prim.id)

    def test_derived_row_no_template_references_is_deactivated(self):
        orphan = BlockType.objects.create(
            template=None, key="stray", html_source=section_html("stray", "x")
        )
        self.run_repair()
        orphan.refresh_from_db()
        self.assertFalse(orphan.is_active)

    def test_repair_is_idempotent(self):
        shared = BlockType.objects.create(
            template=None, key="hero", html_source=section_html("hero", "x")
        )
        self.t1.allowed_block_types.add(shared)
        self.t2.allowed_block_types.add(shared)
        self.run_repair()
        before = BlockType.objects.filter(key="hero").count()
        self.run_repair()
        self.assertEqual(BlockType.objects.filter(key="hero").count(), before)


class ClassifierFailsClosedTests(TestCase):
    """Ambiguous HTML on a palette key must be treated as client-derived.

    The cost of wrongly scoping a curated block is a duplicated palette entry.
    The cost of wrongly globalising a client block is one client's copy on
    another client's site. So ambiguity fails closed.
    """

    def setUp(self):
        self.t1 = Template.objects.create(name="T1", slug="t1", html_source="<html></html>")
        self.t2 = Template.objects.create(name="T2", slug="t2", html_source="<html></html>")

    def split(self, html, key="faq"):
        bt = BlockType.objects.create(template=None, key=key, html_source=html)
        self.t1.allowed_block_types.add(bt)
        self.t2.allowed_block_types.add(bt)
        split_shared_blocks(global_apps, None)
        return BlockType.objects.filter(key=key)

    def assert_scoped(self, rows):
        self.assertEqual(rows.filter(template__isnull=True).count(), 0)
        self.assertEqual(rows.count(), 2)

    def test_neither_marker_is_treated_as_derived(self):
        self.assert_scoped(self.split('<section id="faq"><h2>Client questions</h2></section>'))

    def test_both_markers_is_treated_as_derived(self):
        self.assert_scoped(
            self.split('<div data-block="faq" data-section="faq"><p>client</p></div>')
        )

    def test_a_section_marker_for_another_key_is_treated_as_derived(self):
        self.assert_scoped(
            self.split('<div data-block="faq"><section data-section="hero">x</section></div>')
        )

    def test_single_quoted_primitive_is_still_curated(self):
        rows = self.split("<div data-block='faq'><p>primitive</p></div>")
        self.assertEqual(rows.filter(template__isnull=True).count(), 1)
        self.assertEqual(rows.count(), 1)

    def test_empty_html_stays_curated_because_it_cannot_leak(self):
        rows = self.split("")
        self.assertEqual(rows.filter(template__isnull=True).count(), 1)

    def test_a_non_palette_key_is_always_derived_whatever_the_markup(self):
        self.assert_scoped(
            self.split('<div data-block="hero"><p>x</p></div>', key="hero")
        )
