"""CMS-27 §7 — claim Template.tenant, set editing_mode, seed TemplateVersion v1."""

from __future__ import annotations

import logging

from django.db import migrations

logger = logging.getLogger("core.migrations.0022_backfill_template_ownership")


def backfill_template_ownership(apps, schema_editor):
    Template = apps.get_model("core", "Template")
    Tenant = apps.get_model("core", "Tenant")
    Page = apps.get_model("core", "Page")
    TemplateVersion = apps.get_model("core", "TemplateVersion")

    # template_id -> set of tenant ids that reference it (home or page).
    referrers: dict[int, set[int]] = {}

    for tenant_id, template_id in Tenant.objects.values_list("id", "template_id"):
        if template_id is None:
            continue
        referrers.setdefault(template_id, set()).add(tenant_id)

    for tenant_id, template_id in Page.objects.values_list("tenant_id", "template_id"):
        if template_id is None:
            continue
        referrers.setdefault(template_id, set()).add(tenant_id)

    claimed = 0
    library = 0
    sites_with_sections_before = 0
    for ten in Tenant.objects.select_related("template").iterator():
        schema = ten.template.schema or {}
        if schema.get("sections"):
            sites_with_sections_before += 1

    for tpl in Template.objects.all().iterator():
        owners = referrers.get(tpl.pk, set())
        if len(owners) == 1:
            tpl.tenant_id = next(iter(owners))
            claimed += 1
        else:
            # 0 referrers → library; 2+ → shared library (e.g. pk=4).
            tpl.tenant_id = None
            library += 1

        has_sections = bool((tpl.schema or {}).get("sections"))
        tpl.editing_mode = "editable" if has_sections else "raw"
        tpl.save(update_fields=["tenant_id", "editing_mode"])

        if not TemplateVersion.objects.filter(template_id=tpl.pk, number=1).exists():
            TemplateVersion.objects.create(
                template_id=tpl.pk,
                number=1,
                html_source=tpl.html_source,
                schema=tpl.schema or {},
                label="Backfill v1",
            )

    sites_with_sections_after = 0
    for ten in Tenant.objects.select_related("template").iterator():
        schema = ten.template.schema or {}
        if schema.get("sections"):
            sites_with_sections_after += 1

    logger.info(
        "CMS-27 backfill: claimed=%s library=%s "
        "sites_with_sections_before=%s sites_with_sections_after=%s "
        "templates_total=%s",
        claimed,
        library,
        sites_with_sections_before,
        sites_with_sections_after,
        Template.objects.count(),
    )
    if sites_with_sections_before != sites_with_sections_after:
        raise RuntimeError(
            "CMS-27 backfill changed which sites have a non-empty schema: "
            f"before={sites_with_sections_before} after={sites_with_sections_after}"
        )


def noop_reverse(apps, schema_editor):
    """Ownership / versions are not safely reversible; leave rows as-is."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_template_ownership_and_versions"),
    ]

    operations = [
        migrations.RunPython(backfill_template_ownership, noop_reverse),
    ]
