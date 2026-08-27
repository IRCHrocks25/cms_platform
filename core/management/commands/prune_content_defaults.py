"""Drop stored values that are the template's copy, not the client's.

Every site created before sparse content carries a full copy of its template's
defaults in ``Tenant.content`` / ``Page.content``. Those copies win over the
template forever, so a re-annotation that renumbers generated ``p_N`` ids
silently moves a whole site's copy onto the wrong elements.

Three modes, in increasing order of how much they assume:

``(default)``
    Drop values equal to the *current* default. Safe and inferrable: what goes
    is exactly what ``merge_with_defaults`` puts straight back, so nothing
    visible changes. **This cannot repair an already-displaced row**. After a
    renumbering the stored value matches nothing at its own id, so it stays.

``--across-versions``
    Also drop a value that was the default for that same id in any archived
    ``TemplateVersion``. That is knowable authorship: the client never typed a
    value the template itself supplied at some point in its history. Use this
    first when version history exists.

``--drop-flattened``
    Drop a value that is exactly the *flattened* form of the current default,
    the markup stripped the way the old parser stripped it
    (``get_text(strip=True)``). That is provably template-owned: no client
    types a headline with the space missing between two spans. Run this after
    ``rederive_template_schemas``, which is what makes the current default the
    correct one to compare against.

``--clear-generated``
    Drop every stored value under a generated id (``p_1``, ``li_7``, ``h3_2``)
    regardless of what it holds. Positional ids name a slot, not a thing, so a
    displaced row's values cannot be attributed. This is the operator saying
    "none of this is client-authored, let the template own it again". Semantic
    ids the operator named are never touched. Read the dry run before applying.

Dry-run by default. Scope with ``--site`` before touching everything.

    python manage.py prune_content_defaults --site kieran-haughey
    python manage.py prune_content_defaults --site kieran-haughey --clear-generated
    python manage.py prune_content_defaults --site kieran-haughey --clear-generated --apply
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Page, Tenant
from core.renderer import strip_defaults

#: Positional ids the annotator generates. Mirrors
#: ``core.services.templates._GENERATED_ID_RE``.
GENERATED_ID_RE = re.compile(r"^[a-z][a-z0-9]*_\d+$")


def _flattened_form(default: str) -> str:
    """The value the pre-fix parser would have stored for this default.

    ``get_text(strip=True)`` strips each text node and joins with nothing, so
    ``Navigate menopause. <span>Naturally, powerfully.</span>`` collapsed to
    ``Navigate menopause.Naturally, powerfully.``: space gone, span gone. A
    stored value equal to that is the damage, not an edit.
    """
    return BeautifulSoup(default or "", "lxml").get_text(strip=True)


def _count_fields(content: dict) -> int:
    return sum(
        len(v)
        for k, v in (content or {}).items()
        if isinstance(v, dict) and not (isinstance(k, str) and k.startswith("_"))
    )


def _archived_defaults(template) -> dict[str, dict[str, set]]:
    """Every value each id has ever held as a template default."""
    seen: dict[str, dict[str, set]] = {}
    versions = getattr(template, "versions", None)
    if versions is None:
        return seen
    for version in versions.all():
        for section, fields in ((version.schema or {}).get("defaults") or {}).items():
            if not isinstance(fields, dict):
                continue
            for key, value in fields.items():
                if isinstance(value, str):
                    seen.setdefault(section, {}).setdefault(key, set()).add(value.strip())
    return seen


class Command(BaseCommand):
    help = "Strip template-owned values out of stored tenant/page content."

    def add_arguments(self, parser):
        parser.add_argument(
            "--site",
            default="",
            help="Limit to one tenant subdomain (default: every site).",
        )
        parser.add_argument(
            "--across-versions",
            action="store_true",
            help="Also drop values that were this id's default in any archived version.",
        )
        parser.add_argument(
            "--drop-flattened",
            action="store_true",
            help="Drop values that are the flattened form of the current default.",
        )
        parser.add_argument(
            "--clear-generated",
            action="store_true",
            help="Drop every stored value under a generated id (p_1, li_7, …).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        site = (options.get("site") or "").strip()
        apply_changes = bool(options.get("apply"))
        across_versions = bool(options.get("across_versions"))
        clear_generated = bool(options.get("clear_generated"))
        drop_flattened = bool(options.get("drop_flattened"))

        tenants = Tenant.objects.select_related("template")
        pages = Page.objects.select_related("template", "tenant")
        if site:
            tenants = tenants.filter(subdomain=site)
            pages = pages.filter(tenant__subdomain=site)
            if not tenants.exists():
                self.stderr.write(self.style.ERROR(f"No site with subdomain {site!r}."))
                return

        rows: list[tuple[str, object, dict]] = []
        for tenant in tenants:
            rows.append((f"site:{tenant.subdomain}", tenant, tenant.content or {}))
        for page in pages:
            rows.append(
                (f"page:{page.tenant.subdomain}/{page.slug}", page, page.content or {})
            )

        total_before = total_after = changed_rows = 0
        for label, row, content in rows:
            schema = getattr(row.template, "schema", None)
            if not isinstance(schema, dict):
                continue

            pruned = strip_defaults(schema, content)
            dropped: list[str] = []

            if across_versions or clear_generated or drop_flattened:
                archived = _archived_defaults(row.template) if across_versions else {}
                current_defaults = (schema or {}).get("defaults") or {}
                kept: dict = {}
                for section_id, fields in pruned.items():
                    if isinstance(section_id, str) and section_id.startswith("_"):
                        kept[section_id] = fields
                        continue
                    if not isinstance(fields, dict):
                        kept[section_id] = fields
                        continue
                    section_kept = {}
                    for key, value in fields.items():
                        was_default = (
                            across_versions
                            and isinstance(value, str)
                            and value.strip()
                            in archived.get(section_id, {}).get(key, set())
                        )
                        is_generated = clear_generated and bool(
                            GENERATED_ID_RE.match(key)
                        )
                        current = (current_defaults.get(section_id) or {}).get(key)
                        is_flattened = (
                            drop_flattened
                            and isinstance(value, str)
                            and isinstance(current, str)
                            and value != current
                            and value.strip() == _flattened_form(current)
                        )
                        if was_default or is_generated or is_flattened:
                            dropped.append(f"{section_id}.{key} = {value!r}")
                            continue
                        section_kept[key] = value
                    if section_kept:
                        kept[section_id] = section_kept
                pruned = kept

            before, after = _count_fields(content), _count_fields(pruned)
            total_before += before
            total_after += after
            if pruned == content:
                continue
            changed_rows += 1
            self.stdout.write(f"{label}: {before} → {after} stored fields")
            for line in dropped:
                self.stdout.write(f"    drop {line}")
            if apply_changes:
                with transaction.atomic():
                    row.content = pruned
                    row.save(update_fields=["content", "updated_at"])

        verb = "Pruned" if apply_changes else "Would prune"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {total_before - total_after} stored field(s) across "
                f"{changed_rows} row(s) ({total_before} → {total_after})."
            )
        )
        if not apply_changes and changed_rows:
            self.stdout.write("Re-run with --apply to write.")
