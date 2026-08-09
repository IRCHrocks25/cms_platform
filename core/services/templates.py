"""CMS-27 — single write path for Template HTML, ownership, and versions.

Dashboard / MCP call these helpers. ``Template.save()`` still re-derives
schema unconditionally; these guards protect product surfaces, not the DB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Union

from django.db import transaction

from core.models import Page, Template, TemplateVersion, Tenant
from core.parser import build_schema

logger = logging.getLogger(__name__)

EditableTarget = Union[Tenant, Page]


class CrossTenantTemplateError(Exception):
    """Raised when assigning a template owned by a different tenant."""


class FieldLossError(Exception):
    """Published site(s) would lose fields; caller must confirm."""

    def __init__(self, lost_fields: set[str], affected: list[dict[str, Any]]):
        self.lost_fields = set(lost_fields)
        self.affected = affected
        fields = ", ".join(sorted(self.lost_fields))
        sites = ", ".join(
            a.get("label", "?") for a in affected
        ) or "(none)"
        super().__init__(
            f"Saving would drop fields [{fields}] used by published site(s): "
            f"{sites}. Retry with allow_field_loss=True to proceed; content "
            f"is preserved either way."
        )


@dataclass
class SaveTemplateResult:
    template: Template
    version: TemplateVersion
    lost_fields: set[str] = field(default_factory=set)
    affected: list[dict[str, Any]] = field(default_factory=list)


def _dotted_field_ids(schema: Optional[dict]) -> set[str]:
    ids: set[str] = set()
    for section in (schema or {}).get("sections") or []:
        for f in section.get("fields") or []:
            fid = f.get("id")
            if fid:
                ids.add(fid)
    return ids


def _content_value(content: dict, dotted: str) -> Any:
    if not dotted or not isinstance(content, dict):
        return None
    parts = dotted.split(".", 1)
    if len(parts) != 2:
        return content.get(dotted)
    section, key = parts
    nested = content.get(section)
    if isinstance(nested, dict):
        return nested.get(key)
    return None


def _value_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _target_tenant(target: EditableTarget) -> Tenant:
    if isinstance(target, Tenant):
        return target
    return target.tenant


def _sites_using_template(template: Template) -> Iterable[tuple[str, Any, dict, bool]]:
    """Yield (label, row, content, is_published) for tenants/pages on template."""
    for tenant in Tenant.objects.filter(template_id=template.pk):
        yield (
            f"site:{tenant.subdomain}",
            tenant,
            tenant.content or {},
            bool(tenant.is_published),
        )
    for page in Page.objects.filter(template_id=template.pk).select_related("tenant"):
        yield (
            f"page:{page.tenant.subdomain}/{page.slug}",
            page,
            page.content or {},
            bool(page.is_published),
        )


def _affected_published(template: Template, lost: set[str]) -> list[dict[str, Any]]:
    affected: list[dict[str, Any]] = []
    for label, _row, content, published in _sites_using_template(template):
        if not published:
            continue
        held = [fid for fid in lost if _value_nonempty(_content_value(content, fid))]
        if held:
            affected.append({"label": label, "fields": held})
    return affected


def save_template_version(
    template: Template,
    html_source: str,
    *,
    user,
    allow_field_loss: bool = False,
    label: str = "",
) -> SaveTemplateResult:
    """Write HTML, append a TemplateVersion, optionally refuse field loss."""
    old_fields = _dotted_field_ids(template.schema)
    new_schema = build_schema(html_source)
    new_fields = _dotted_field_ids(new_schema)
    lost = old_fields - new_fields

    affected: list[dict[str, Any]] = []
    if lost:
        affected = _affected_published(template, lost)
        if affected and not allow_field_loss:
            raise FieldLossError(lost, affected)

    with transaction.atomic():
        template.html_source = html_source
        template.save()  # re-derives schema
        next_number = (
            template.versions.order_by("-number")
            .values_list("number", flat=True)
            .first()
            or 0
        ) + 1
        version = TemplateVersion.objects.create(
            template=template,
            number=next_number,
            html_source=template.html_source,
            schema=template.schema or {},
            label=label or "",
            saved_by=user if getattr(user, "pk", None) else None,
        )

    return SaveTemplateResult(
        template=template,
        version=version,
        lost_fields=lost,
        affected=affected,
    )


def restore_template_version(
    template: Template,
    version: TemplateVersion,
    *,
    user,
    allow_field_loss: bool = False,
    label: str = "",
) -> SaveTemplateResult:
    """Forward-only restore: append a new version from archived HTML."""
    if version.template_id != template.pk:
        raise ValueError("version does not belong to template")

    fresh = build_schema(version.html_source)
    archived = version.schema or {}
    if fresh != archived:
        logger.warning(
            "core/parser.py has changed since this version was saved "
            "(template_id=%s version=%s); using freshly derived schema",
            template.pk,
            version.number,
        )

    restore_label = label or f"Restored from v{version.number}"
    return save_template_version(
        template,
        version.html_source,
        user=user,
        allow_field_loss=allow_field_loss,
        label=restore_label,
    )


def assign_template(
    target: EditableTarget,
    template: Template,
    *,
    user,
) -> Template:
    """Assign ``template`` to a Tenant or Page, cloning library rows."""
    tenant = _target_tenant(target)
    owner_id = template.tenant_id

    if owner_id is None:
        assigned = template.clone_for(tenant, user=user)
    elif owner_id == tenant.pk:
        assigned = template
    else:
        owner_name = getattr(template.tenant, "name", None) or f"tenant#{owner_id}"
        raise CrossTenantTemplateError(
            f"Template “{template.name}” is owned by {owner_name}. "
            f"Duplicate it into this site first, then assign the copy."
        )

    if isinstance(target, Tenant):
        target.template = assigned
        target.save(update_fields=["template", "updated_at"])
    else:
        target.template = assigned
        target.save(update_fields=["template", "updated_at"])

    return assigned
