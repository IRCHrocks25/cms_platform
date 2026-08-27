"""Re-derive every stored ``Template.schema`` from its own HTML.

``Template.schema`` is stored, and public and preview rendering both merge
against the stored copy rather than re-parsing. So a parser fix does not reach
a single existing site until the schemas are re-derived: a schema written
before the text-field fix still holds the flattened, space-less default, and
feeding that back through the fixed renderer looks like a real edit, and the
accent span is destroyed exactly as it was before.

Re-saving the template is not a substitute. ``save_template_version`` treats
byte-identical HTML as unchanged, and the dashboard's metadata save excludes
``schema`` from ``update_fields``.

HTML is never touched, and no TemplateVersion is cut. Dry-run by default.

    python manage.py rederive_template_schemas
    python manage.py rederive_template_schemas --apply
    python manage.py rederive_template_schemas --site kieran-haughey --apply
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.models import Template
from core.parser import build_schema


def _defaults(schema: dict) -> dict:
    return (schema or {}).get("defaults") or {}


def _types(schema: dict) -> dict[str, str]:
    return {
        f["id"]: f.get("type")
        for section in (schema or {}).get("sections") or []
        for f in section.get("fields") or []
        if f.get("id")
    }


class Command(BaseCommand):
    help = "Re-derive stored Template.schema from Template.html_source."

    def add_arguments(self, parser):
        parser.add_argument(
            "--site",
            default="",
            help="Limit to templates used by one tenant subdomain.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the re-derived schemas. Without this, report only.",
        )
        parser.add_argument(
            "--verbose-fields",
            action="store_true",
            help="List every field whose type or default changes.",
        )

    def handle(self, *args, **options):
        site = (options.get("site") or "").strip()
        apply_changes = bool(options.get("apply"))
        verbose = bool(options.get("verbose_fields"))

        templates = Template.objects.all()
        if site:
            # Both the site's home template and any template it owns (inner
            # pages get their own, via Template.tenant).
            templates = templates.filter(
                Q(tenants__subdomain=site) | Q(tenant__subdomain=site)
            ).distinct()
            if not templates.exists():
                self.stderr.write(
                    self.style.ERROR(f"No templates for site {site!r}.")
                )
                return

        stale = 0
        retyped = redefaulted = 0
        for tpl in templates:
            fresh = build_schema(tpl.html_source or "")
            if fresh == (tpl.schema or {}):
                continue
            stale += 1

            old_types, new_types = _types(tpl.schema or {}), _types(fresh)
            old_defaults, new_defaults = _defaults(tpl.schema or {}), _defaults(fresh)
            type_changes = [
                fid
                for fid, t in new_types.items()
                if fid in old_types and old_types[fid] != t
            ]
            default_changes = [
                f"{sec}.{key}"
                for sec, fields in new_defaults.items()
                for key, value in fields.items()
                if key in (old_defaults.get(sec) or {})
                and old_defaults[sec][key] != value
            ]
            retyped += len(type_changes)
            redefaulted += len(default_changes)

            self.stdout.write(
                f"template {tpl.pk} {tpl.name!r}: "
                f"{len(type_changes)} retyped, {len(default_changes)} defaults changed"
            )
            if verbose:
                for fid in type_changes:
                    self.stdout.write(
                        f"    type {fid}: {old_types[fid]} → {new_types[fid]}"
                    )
                for fid in default_changes:
                    sec, key = fid.split(".", 1)
                    self.stdout.write(
                        f"    default {fid}: {old_defaults[sec][key]!r} → "
                        f"{new_defaults[sec][key]!r}"
                    )

            if apply_changes:
                with transaction.atomic():
                    # Direct update, not Template.save(): save() re-derives from
                    # html_source anyway, but going through the field keeps this
                    # command from tripping any save-time side effects.
                    Template.objects.filter(pk=tpl.pk).update(schema=fresh)

        verb = "Re-derived" if apply_changes else "Would re-derive"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {stale} template(s): {retyped} field type(s) and "
                f"{redefaulted} default(s) corrected."
            )
        )
        if not apply_changes and stale:
            self.stdout.write("Re-run with --apply to write.")
