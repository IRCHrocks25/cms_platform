"""Two clients converting a section with the same id must not share a block.

Found live on 2026-09-02: nolan-group.sites.katek.app served the Rinaldi Group's
hero on its home page. Both templates had a section called `hero`, blocks were
one global library keyed by `key` alone, and the second conversion overwrote
`html_source` for both.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import BlockType, Template
from core.services.blocks import apply_classic_upgrade


def annotated(section_id, heading):
    return (
        "<!doctype html><html><body><main>"
        f'<section id="{section_id}" data-section="{section_id}" '
        f'data-label="Hero" data-group="Page sections">'
        f'<h1 data-edit="{section_id}.title" data-label="Hero title">{heading}</h1>'
        "</section></main></body></html>"
    )


class BlockTypeIsolationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.a = Template.objects.create(
            name="Client A", slug="client-a",
            html_source=annotated("hero", "A country club realtor"),
        )
        self.b = Template.objects.create(
            name="Client B", slug="client-b",
            html_source=annotated("hero", "The Insider Power Couple"),
        )

    def convert_both(self):
        apply_classic_upgrade(self.a)
        apply_classic_upgrade(self.b)

    def test_each_template_gets_its_own_block_row(self):
        self.convert_both()
        self.assertEqual(BlockType.objects.filter(key="hero", template=self.a).count(), 1)
        self.assertEqual(BlockType.objects.filter(key="hero", template=self.b).count(), 1)

    def test_second_conversion_does_not_overwrite_the_first_markup(self):
        # This is the regression. Before the fix, converting B rewrote the one
        # global `hero` row and A rendered B's heading.
        self.convert_both()
        a_block = BlockType.objects.get(key="hero", template=self.a)
        self.assertIn("A country club realtor", a_block.html_source)
        self.assertNotIn("Insider Power Couple", a_block.html_source)

    def test_neither_template_allowlists_the_other_s_block(self):
        self.convert_both()
        a_ids = set(self.a.allowed_block_types.values_list("id", flat=True))
        b_ids = set(self.b.allowed_block_types.values_list("id", flat=True))
        shared = a_ids & b_ids
        for bt in BlockType.objects.filter(id__in=shared):
            # Only the curated library may be shared, and it is template NULL.
            self.assertIsNone(
                bt.template,
                f"template-derived block {bt.key!r} is shared between templates",
            )

    def test_curated_library_blocks_stay_global_and_shared(self):
        from core.management.commands.seed_builder_blocks import seed_block_types

        seed_block_types()
        globals_ = BlockType.objects.filter(template__isnull=True)
        self.assertTrue(globals_.exists())
        # A shared palette is the point; they must not be duplicated per site.
        keys = list(globals_.values_list("key", flat=True))
        self.assertEqual(len(keys), len(set(keys)))
