"""Scope BlockType to its template so two clients cannot share one block.

Found live 2026-09-02: nolan-group.sites.katek.app served the Rinaldi Group's
hero. Blocks were one global library keyed by `key` alone, so two templates
converting a section with the same id resolved to the same row and the later
conversion overwrote `html_source` for both.
"""
import django.db.models.deletion
from django.db import migrations, models


def split_shared_blocks(apps, schema_editor):
    """Give every template-derived block its own row.

    Rules:
      * A key in the curated palette (BUILDER_BLOCKS) stays global, template
        NULL. That library is meant to be shared by every site.
      * A block referenced by exactly one template is assigned to it.
      * A block referenced by more than one template is CLONED, one row per
        template, and each template's allowlist repointed at its own clone.

    The clone carries whichever html_source survived the last conversion, so it
    is correct for at most one of the colliding templates. This stops the leak
    and makes the damage per-template and repairable. It cannot invent markup
    that was already overwritten, so affected templates are printed and need
    re-importing from source.
    """
    BlockType = apps.get_model("core", "BlockType")
    try:
        from core.management.commands.seed_builder_blocks import BUILDER_BLOCKS

        builder_keys = set(BUILDER_BLOCKS)
    except Exception:
        builder_keys = set()

    needs_reimport = []
    for bt in BlockType.objects.all().iterator():
        if bt.key in builder_keys:
            continue
        templates = list(bt.templates.all())
        if not templates:
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
            needs_reimport.append((other.pk, other.name, bt.key))

    if needs_reimport:
        print(
            "\n  Block collisions split. These templates shared a block with "
            "another template, so their markup may already have been "
            "overwritten. Re-import or re-convert each from source:"
        )
        for pk, name, key in needs_reimport:
            print(f"    template {pk} {name!r}: block {key!r}")


def noop_reverse(apps, schema_editor):
    """Irreversible by design.

    Reversing means merging per-template rows back into one global row, which
    is the collision this migration exists to undo. Restore from a database
    backup instead.
    """


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
        migrations.RunPython(split_shared_blocks, noop_reverse),
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
