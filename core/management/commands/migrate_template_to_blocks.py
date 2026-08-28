"""Convert a classic annotated Template into a curated-block *shell*.

A classic template is one annotated document: fixed chrome (nav/footer) plus a
run of body sections. The block-instance model splits that into

* a **shell** — the same chrome with a single ``<div data-region="main">`` slot
  where client-inserted blocks render, and
* a **block catalog** — one :class:`~core.models.BlockType` per former body
  section, allowlisted on the template.

Every tenant home and inner page that uses the template has its
``{section: {field}}`` content rewritten into ``regions.main`` instances. The
command is **dry-run by default** and gated on a byte-identical render: for each
affected page it renders the classic output and the new block output and refuses
to apply if any pair differs (unless ``--force``). The original content is
preserved under a ``_classic`` backup key for rollback (dual-write).

    python manage.py migrate_template_to_blocks <template>          # preview
    python manage.py migrate_template_to_blocks <template> --apply   # commit
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from core.models import Page, Template, Tenant
from core.parser import build_schema
from core.renderer import (
    merge_with_defaults,
    render_page_from_blocks,
    render_site,
)
from core.services import blocks


def _resolve_template(ident: str) -> Template:
    if ident.isdigit():
        try:
            return Template.objects.get(pk=int(ident))
        except Template.DoesNotExist:
            pass
    qs = Template.objects.filter(slug=ident) or Template.objects.filter(name=ident)
    obj = qs.first()
    if obj is None:
        raise CommandError(f"No template matches {ident!r} (pk, slug, or name).")
        return obj


class Command(BaseCommand):
    help = "Convert a classic Template into a block shell + BlockType catalog."

    def add_arguments(self, parser):
        parser.add_argument("template", help="Template pk, slug, or name.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Commit the migration (default is a dry-run preview).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Apply even if a page's block render differs from the classic render.",
        )
        parser.add_argument("--region", default="main", help="Region slot name.")

    def handle(self, *args, **opts):
        template = _resolve_template(opts["template"])
        region = opts["region"]
        apply = opts["apply"]
        force = opts["force"]

        if template.is_block_shell:
            raise CommandError(
                f"Template '{template.name}' is already a block shell — nothing to do."
            )

        old_html = template.html_source
        old_schema = build_schema(old_html)
        shell_html, fragments = blocks.split_shell_and_blocks(old_html, region=region)
        if not fragments:
            raise CommandError(
                "No body sections found to convert — every section is chrome."
            )

        block_keys = [k for k, _ in fragments]
        catalog = blocks._catalog_from_fragments(fragments)

        self.stdout.write(self.style.MIGRATE_HEADING(f"Template: {template.name} (pk={template.pk})"))
        self.stdout.write(f"  Chrome kept + region '{region}' inserted.")
        self.stdout.write(f"  Blocks: {', '.join(block_keys)}")

        editables: list = list(Tenant.objects.filter(template=template))
        editables += list(Page.objects.filter(template=template))

        # ---- diff gate --------------------------------------------------- #
        failures = []
        conversions: list[tuple[object, dict]] = []
        for ed in editables:
            content = ed.content or {}
            classic = render_site(old_html, merge_with_defaults(old_schema, content))
            new_content = blocks.convert_content_to_regions(content, block_keys, region=region)
            block_out = render_page_from_blocks(shell_html, new_content, catalog)
            label = self._label(ed)
            if blocks.normalize_for_diff(classic) == blocks.normalize_for_diff(block_out):
                self.stdout.write(self.style.SUCCESS(f"  [match]    {label}"))
            else:
                self.stdout.write(self.style.ERROR(f"  [MISMATCH] {label}"))
                failures.append(label)
            conversions.append((ed, new_content))

        if not editables:
            self.stdout.write("  (no tenants or pages use this template yet)")

        if failures and not force:
            raise CommandError(
                f"{len(failures)} page(s) render differently after conversion; "
                "aborting. Re-run with --force to migrate anyway."
            )

        if not apply:
            self.stdout.write(self.style.WARNING("\nDry-run only. Re-run with --apply to commit."))
            return

        blocks.apply_classic_upgrade(template, region=region)
        template.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"\nMigrated template '{template.name}': "
                f"{template.allowed_block_types.count()} block type(s), "
                f"{len(conversions)} page(s) converted."
            )
        )

    @staticmethod
    def _label(editable) -> str:
        if isinstance(editable, Tenant):
            return f"Tenant home: {editable.subdomain}"
        return f"Page: {editable.tenant.subdomain}/{editable.slug}"
