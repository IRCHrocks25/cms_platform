"""Field addressing, merge-with-defaults reads, and etag computation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from core.models import Page, Tenant
from core.renderer import merge_with_defaults
from core.renderer import merge_block_defaults


def content_etag(stored: dict) -> str:
    """SHA-256 over the stored content blob (not merged output)."""
    payload = json.dumps(
        stored or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def html_etag(html: str) -> str:
    """SHA-256 over template HTML — concurrent-write guard for push_page."""
    return hashlib.sha256((html or "").encode("utf-8")).hexdigest()


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


def block_instances(stored: dict, catalog: dict) -> dict[str, tuple[dict, dict]]:
    """Index current, allowlisted instances by stable ID, including children."""
    # Validate the entire stored tree before filtering retired types. An ID
    # reused by an unavailable block still makes any addressed write ambiguous.
    available_ids = {row["id"] for row in list_block_instances(stored, catalog)
                     if row["available"]}
    found = {}

    def walk(instances):
        for instance in instances:
            instance_id = instance.get("id")
            if instance_id in available_ids:
                found[instance_id] = (instance, catalog[instance["type"]])
            children_by_region = instance.get("children") or {}
            for children in children_by_region.values():
                walk(children)

    for instances in ((stored or {}).get("regions") or {}).values():
        walk(instances)
    return found


def list_block_instances(stored: dict, catalog: dict) -> list[dict[str, Any]]:
    """Preorder inventory of stored regions, including fieldless or retired blocks.

    ``region`` is the top-level region or the slot on ``parent_id``. Positions
    are zero-based within that region. Listing retired types is read-only;
    field and HTML tools still enforce the active template allowlist.
    """
    regions = (stored or {}).get("regions")
    if regions is None:
        regions = {}
    if not isinstance(regions, dict):
        raise ValueError("Invalid block region tree.")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(instances, *, parent_id, region, depth):
        if not isinstance(instances, list):
            raise ValueError("Invalid block instance list.")
        for position, instance in enumerate(instances):
            if not isinstance(instance, dict):
                raise ValueError("Invalid block instance tree.")
            instance_id = instance.get("id")
            block_type = instance.get("type")
            if (
                not isinstance(instance_id, str) or not instance_id
                or instance_id in seen
                or not isinstance(block_type, str) or not block_type
            ):
                raise ValueError("Invalid or ambiguous block instance tree.")
            seen.add(instance_id)
            rows.append({
                "id": instance_id,
                "type": block_type,
                "parent_id": parent_id,
                "region": region,
                "position": position,
                "depth": depth,
                "available": block_type in catalog,
            })
            children = instance.get("children")
            if children is None:
                children = {}
            if not isinstance(children, dict):
                raise ValueError("Invalid nested block region tree.")
            for child_region, child_instances in children.items():
                if not isinstance(child_region, str):
                    raise ValueError("Invalid nested block region tree.")
                walk(child_instances, parent_id=instance_id,
                     region=child_region, depth=depth + 1)

    for region, instances in regions.items():
        if not isinstance(region, str):
            raise ValueError("Invalid block region tree.")
        walk(instances, parent_id=None, region=region, depth=0)
    return rows


def block_field(stored: dict, catalog: dict, field_id: str):
    if not isinstance(field_id, str) or "." not in field_id:
        raise KeyError(field_id)
    instance_id, field_name = field_id.split(".", 1)
    instance, block = block_instances(stored, catalog)[instance_id]
    schema = block.get("schema") or {}
    fields = {str(f.get("id")): f for f in schema.get("fields") or []}
    if field_name not in fields:
        raise KeyError(field_id)
    return instance, fields[field_name], merge_block_defaults(schema, instance.get("fields"))


def read_fields(schema: dict, stored: dict, catalog: dict | None = None) -> dict[str, dict[str, Any]]:
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
    if catalog:
        for instance_id, (instance, block) in block_instances(stored, catalog).items():
            block_schema = block.get("schema") or {}
            merged = merge_block_defaults(block_schema, instance.get("fields"))
            overrides = instance.get("fields") or {}
            for item in block_schema.get("fields") or []:
                name = str(item.get("id"))
                field_id = f"{instance_id}.{name}"
                if field_id in out:
                    raise ValueError("Ambiguous shell and block field IDs.")
                out[field_id] = {
                    "value": merged.get(name),
                    "is_default": name not in overrides,
                }
    return out


def read_field(
    schema: dict, stored: dict, field_id: str, catalog: dict | None = None
) -> tuple[Any, bool]:
    fields = read_fields(schema, stored, catalog)
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


def write_block_field(stored: dict, catalog: dict, field_id: str, value: Any) -> dict:
    from copy import deepcopy

    new_content = deepcopy(stored)
    instance, _field, _merged = block_field(new_content, catalog, field_id)
    name = field_id.split(".", 1)[1]
    instance.setdefault("fields", {})[name] = value
    return new_content
