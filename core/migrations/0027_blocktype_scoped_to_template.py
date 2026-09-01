"""Scope BlockType to its template so two clients cannot share one block.

Found live 2026-09-02: nolan-group.sites.katek.app served the Rinaldi Group's
hero. Blocks were one global library keyed on `key` alone, so two templates
converting a section with the same id resolved to the same row, and the later
conversion overwrote `html_source` for both.
"""
import django.db.models.deletion
from django.db import migrations, models

# Snapshot of BUILDER_BLOCKS keys as of 2026-09-02, frozen on purpose. A
# migration must describe the database at the time it ran, so it must not
# import live application code whose contents change underneath it.
PALETTE_KEYS_AT_MIGRATION = {
    "button", "code", "countdown", "counter", "divider", "faq", "feature",
    "form", "gallery", "headline", "icon", "image", "list", "logos", "map",
    "nav-link", "paragraph", "pricing", "progress", "qr", "reviews",
    "richtext", "row-1", "row-2", "row-3", "row-4", "row-5", "row-6",
    "section", "section-tight", "slider", "social", "spacer", "subheadline",
    "video",
}


def _is_curated(bt):
    """True only when a row is positively a curated palette primitive.

    This FAILS CLOSED. Anything ambiguous is treated as client-derived and gets
    scoped to a template, because the cost of wrongly scoping a curated block is
    a duplicated palette entry, while the cost of wrongly globalising a client
    block is one client's copy appearing on another client's site.

    The first version delegated to seed_builder_blocks._is_migrated_section_html.
    That helper answers a different question, "should this row be protected from
    being overwritten by the seeder", and it answers "curated" for HTML with
    neither marker, HTML with both markers, and a data-section naming some other
    key. Any of those can hold client markup.

    Curated therefore requires all three:
      * a key in the palette snapshot,
      * a positive data-block="<key>" marker,
      * no data-section marker anywhere in the fragment.
    """
    if bt.key not in PALETTE_KEYS_AT_MIGRATION:
        return False
    html = bt.html_source or ""
    if not html.strip():
        # Nothing to leak, and the seeder repopulates it on the next run.
        return True
    has_primitive_marker = (
        f'data-block="{bt.key}"' in html or f"data-block='{bt.key}'" in html
    )
    carries_a_section = "data-section=" in html
    return has_primitive_marker and not carries_a_section


def split_shared_blocks(apps, schema_editor):
    """Give every template-derived block its own row.

    Rules:
      * Curated palette rows stay global (template NULL). They are meant to be
        shared, and they are identified by BOTH a palette key AND primitive
        HTML, never by key alone.
      * A derived row referenced by exactly one template is assigned to it.
      * A derived row referenced by several templates is CLONED, one per
        template, each template's allowlist repointed at its own clone.
      * A derived row referenced by NO template is deactivated. Its scope
        column stays NULL because there is no template to own it, so
        deactivation is what stops it being rendered or attached later.

    Whichever html_source survived belongs to at most one of the colliding
    templates, and there is no record of which, so EVERY participant is
    reported, not only the clones. This migration stops the leak and makes the
    damage per-template and repairable. It cannot recover overwritten markup.
    """
    BlockType = apps.get_model("core", "BlockType")

    needs_reimport, orphans = [], []
    for bt in BlockType.objects.all().iterator():
        if _is_curated(bt):
            continue
        templates = list(bt.templates.all())
        if not templates:
            if bt.is_active:
                bt.is_active = False
                bt.save(update_fields=["is_active"])
            orphans.append((bt.pk, bt.key))
            continue
        first, rest = templates[0], templates[1:]
        bt.template = first
        bt.save(update_fields=["template"])
        for other in rest:
            clone = BlockType.objects.create(
                template=other,
                key=bt.key,
                label=bt.label,
                icon=bt.icon,
                category=bt.category,
                html_source=bt.html_source,
                schema=bt.schema,
                is_active=bt.is_active,
            )
            other.allowed_block_types.remove(bt)
            other.allowed_block_types.add(clone)
        if rest:
            # The retained row is reported too: its markup is no more likely to
            # be correct than any clone's.
            for t in templates:
                needs_reimport.append((t.pk, t.name, bt.key))

    if needs_reimport:
        print(
            "\n  Shared blocks split. Each of these templates shared one block "
            "row with another template, so its markup may already have been "
            "overwritten. Re-import or re-convert each from source:"
        )
        for pk, name, key in needs_reimport:
            print(f"    template {pk} {name!r}: block {key!r}")
    if orphans:
        print(
            "\n  Deactivated derived blocks that no template references. "
            "Review before deleting:"
        )
        for pk, key in orphans:
            print(f"    blocktype {pk}: key {key!r}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_merge_20260828_2109"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="blocktype",
            name="uniq_blocktype_key",
        ),
        migrations.AddField(
            model_name="blocktype",
            name="template",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Template this block was derived from. NULL for curated "
                    "library blocks shared by every site."
                ),
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="own_block_types",
                to="core.template",
            ),
        ),
        # Reverse is a no-op rather than raising, because
        # test_template_ownership_backfill legitimately walks the migration
        # graph backwards and a hard IrreversibleError would break it.
        #
        # That is safe without being a licence to reverse this on real data.
        # Reversing drops the scope column and returns the table to the shared
        # library shape, and the reverse of the AddConstraint below restores
        # uniq_blocktype_key, which FAILS if this migration created any clones.
        # So an empty database rewinds cleanly and a database that actually had
        # collisions refuses. Restore from a backup rather than relying on that.
        migrations.RunPython(split_shared_blocks, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="blocktype",
            constraint=models.UniqueConstraint(
                fields=("template", "key"), name="uniq_blocktype_template_key"
            ),
        ),
        migrations.AddConstraint(
            model_name="blocktype",
            constraint=models.UniqueConstraint(
                condition=models.Q(("template__isnull", True)),
                fields=("key",),
                name="uniq_global_blocktype_key",
            ),
        ),
    ]
