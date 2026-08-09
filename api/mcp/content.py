"""Field addressing, merge-with-defaults reads, and etag computation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from core.models import Page, Tenant
from core.renderer import merge_with_defaults


def content_etag(stored: dict) -> str:
    """SHA-256 over the stored content blob (not merged output)."""
    payload = json.dumps(
        stored or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_editable(
    tenant: Tenant, page_slug: Optional[str]
) -> tuple[Any, dict, dict]:
    """Return (template_or_page_meta, schema, stored_content).

    ``page_slug`` None/empty → home (Tenant.content).
    Raises LookupError when the page slug is missing.
    """
    if page_slug is None or page_slug == "":
        return tenant, tenant.template.schema or {}, tenant.content or {}

    page = Page.objects.filter(tenant=tenant, slug=page_slug).select_related(
        "template"
    ).first()
    if page is None:
        raise LookupError(page_slug)
    return page, page.template.schema or {}, page.content or {}


def _field_present_in_stored(stored: dict, field_id: str) -> bool:
    if "." not in field_id:
        return False
    section, field = field_id.split(".", 1)
    section_data = (stored or {}).get(section)
    if not isinstance(section_data, dict):
        return False
    return field in section_data


def read_fields(schema: dict, stored: dict) -> dict[str, dict[str, Any]]:
    """All dotted fields with merged value + is_default provenance."""
    merged = merge_with_defaults(schema, stored)
    defaults = schema.get("defaults") or {}
    out: dict[str, dict[str, Any]] = {}
    for section_id, fields in defaults.items():
        if not isinstance(fields, dict):
            continue
        for field_name in fields:
            field_id = f"{section_id}.{field_name}"
            section_merged = merged.get(section_id) or {}
            value = section_merged.get(field_name)
            out[field_id] = {
                "value": value,
                "is_default": not _field_present_in_stored(stored, field_id),
            }
    # Also surface brand/section fields listed in schema.sections if defaults omitted.
    for section in schema.get("sections") or []:
        for field in section.get("fields") or []:
            field_id = field.get("id")
            if not field_id or field_id in out:
                continue
            if "." not in field_id:
                continue
            section_id, field_name = field_id.split(".", 1)
            section_merged = merged.get(section_id) or {}
            out[field_id] = {
                "value": section_merged.get(field_name),
                "is_default": not _field_present_in_stored(stored, field_id),
            }
    return out


def read_field(
    schema: dict, stored: dict, field_id: str
) -> tuple[Any, bool]:
    fields = read_fields(schema, stored)
    if field_id not in fields:
        raise KeyError(field_id)
    entry = fields[field_id]
    return entry["value"], entry["is_default"]


def split_field_id(field_id: str) -> tuple[str, str]:
    """Require a dotted ``section.field`` id; never a flat top-level key."""
    if not isinstance(field_id, str) or "." not in field_id:
        raise ValueError("field must be a dotted section.field id")
    section, field = field_id.split(".", 1)
    if not section or not field or section.startswith("_"):
        raise ValueError("field must be a dotted section.field id")
    return section, field


def write_field(stored: dict, field_id: str, value: Any) -> dict:
    """Return a new nested content blob with ``field_id`` set.

    Always writes ``content[section][field]``. Never stores a flat dotted key
    at the top level — that shape makes ``merge_with_defaults`` raise on
    render (CMS outage 2026-08-06).
    """
    from copy import deepcopy

    section, field = split_field_id(field_id)
    new_content = deepcopy(stored) if stored else {}
    # Drop a corrupt flat key if one somehow exists.
    new_content.pop(field_id, None)
    section_data = new_content.get(section)
    if not isinstance(section_data, dict):
        section_data = {}
    section_data = dict(section_data)
    section_data[field] = value
    new_content[section] = section_data
    return new_content
