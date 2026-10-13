"""Shared read/write rules for GHL embed slots."""

from __future__ import annotations

from typing import Any, Iterator

from core.ghl_embed import parse_ghl_embed_value
from core.renderer import merge_with_defaults
from core.services import ghl_forms


class GhlEmbedValidationError(ValueError):
    """A safe, user-facing embed-slot validation failure."""


def iter_embed_fields(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for section in schema.get("sections", []) or []:
        for field in section.get("fields", []) or []:
            if field.get("type") == "ghl-embed":
                yield field


def get_embed_slots(
    schema: dict[str, Any], content: dict[str, Any] | None,
    *, catalog: dict[str, dict] | None = None,
) -> list[dict[str, str]]:
    merged = merge_with_defaults(schema, content or {})
    slots = []
    for field in iter_embed_fields(schema):
        field_id = str(field["id"])
        section_id, value_id = field_id.split(".", 1)
        slots.append(
            {
                "id": field_id,
                "label": str(field.get("label") or field_id),
                "kind": str(field.get("ghl_kind") or ""),
                "value": str((merged.get(section_id) or {}).get(value_id) or ""),
            }
        )
    if catalog:

        def walk(instances):
            for instance in instances or []:
                if not isinstance(instance, dict):
                    continue
                block = catalog.get(str(instance.get("type") or "")) or {}
                values = instance.get("fields") or {}
                if not isinstance(values, dict):
                    values = {}
                for field in (block.get("schema") or {}).get("fields", []) or []:
                    if field.get("type") != "ghl-embed":
                        continue
                    field_id = str(field.get("id") or "")
                    slots.append({
                        "id": f"{instance.get('id')}.{field_id}",
                        "label": str(field.get("label") or field_id),
                        "kind": str(field.get("ghl_kind") or ""),
                        "value": str(values.get(field_id) or ""),
                    })
                children_by_region = instance.get("children") or {}
                if isinstance(children_by_region, dict):
                    for children in children_by_region.values():
                        walk(children)

        for instances in ((content or {}).get("regions") or {}).values():
            walk(instances)
    return slots


def validate_embed_content_update(
    *,
    tenant,
    schema: dict[str, Any],
    current_content: dict[str, Any] | None,
    new_content: dict[str, Any],
    is_published: bool,
    catalog: dict[str, dict] | None = None,
) -> None:
    """Validate changed embed values before any content is persisted.

    The available-form lookup is deliberately tenant-scoped and only happens
    when a form selection actually changes. Ordinary text saves therefore do
    not depend on GHL availability.
    """
    current = {
        slot["id"]: slot
        for slot in get_embed_slots(schema, current_content, catalog=catalog)
    }
    proposed = {
        slot["id"]: slot
        for slot in get_embed_slots(schema, new_content, catalog=catalog)
    }
    changed: list[tuple[str, str]] = []

    if is_published and any(
        slot["value"].strip() and field_id not in proposed
        for field_id, slot in current.items()
    ):
        raise GhlEmbedValidationError(
            "Cannot remove a form from a published page. Unpublish the page first."
        )

    for field_id, slot in proposed.items():
        value = slot["value"].strip()
        if value == current.get(field_id, {}).get("value", "").strip():
            continue
        if not value:
            if is_published:
                raise GhlEmbedValidationError(
                    "Cannot remove a form from a published page. Unpublish the page first."
                )
            continue
        try:
            parsed = parse_ghl_embed_value(value, expected_kind=slot["kind"])
        except ValueError as exc:
            raise GhlEmbedValidationError(str(exc)) from exc
        if parsed is not None:
            changed.append((field_id, parsed[1]))

    if not changed:
        return

    try:
        available_ids = {form["id"] for form in ghl_forms.list_forms_for_tenant(tenant)}
    except ghl_forms.GhlFormsUnavailable as exc:
        raise GhlEmbedValidationError(exc.public_message) from exc

    for field_id, form_id in changed:
        if form_id not in available_ids:
            raise GhlEmbedValidationError(
                f'The selected form for "{field_id}" is not available for this site.'
            )
