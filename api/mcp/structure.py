"""Bounded, copy-on-write block structure operations for MCP."""

from __future__ import annotations

from copy import deepcopy

from bs4 import BeautifulSoup, Comment, Tag

from api.mcp import content as content_mod
from core.services import blocks
from core.services.template_sanitizer import sanitize_template_html


class BlockStructureError(ValueError):
    """A requested block edit cannot be applied safely."""


def _body_region(region: str) -> bool:
    return bool(region) and region != "nav" and not region.startswith(("header", "footer"))


def _single_region_slot(html: str, region: str, *, shell: bool) -> None:
    soup = BeautifulSoup(html or "", "html.parser")
    matches = soup.find_all(
        attrs={"data-region": region}
    )
    if not matches and region.startswith("body-"):
        anchors = [comment for comment in soup.find_all(
            string=lambda value: isinstance(value, Comment))
            if str(comment).strip() in {f"cms-slot:{region}:start", f"cms-slot:{region}:end"}]
        if len(anchors) == 2 and str(anchors[0]).strip().endswith(":start") \
                and str(anchors[1]).strip().endswith(":end") \
                and anchors[0].parent is anchors[1].parent:
            if shell and any(parent.name in {"header", "footer"} or
                             parent.get("data-section") in {"header", "footer", "nav"}
                             for parent in anchors[0].parents if isinstance(parent, Tag)):
                raise BlockStructureError("Header and footer regions use the visual editor.")
            return
    if len(matches) != 1:
        raise BlockStructureError("Destination region must appear exactly once in its template.")
    if shell and (matches[0].name in {"header", "footer"} or
                  matches[0].get("data-section") in {"header", "footer", "nav"}):
        raise BlockStructureError("Header and footer regions use the visual editor.")
    for ancestor in matches[0].parents:
        if ancestor.has_attr("data-region"):
            raise BlockStructureError("Destination region must be a direct template slot.")
        if shell and (ancestor.name in {"header", "footer"} or
                      ancestor.get("data-section") in {"header", "footer", "nav"}):
            raise BlockStructureError("Header and footer regions use the visual editor.")


def _rows(stored: dict, catalog: dict) -> list[dict]:
    rows = content_mod.list_block_instances(stored, catalog)
    if len(rows) > blocks.MAX_BLOCKS_PER_PAGE:
        raise BlockStructureError(
            f"This page can have at most {blocks.MAX_BLOCKS_PER_PAGE} blocks."
        )
    if any(row["depth"] > blocks.MAX_BLOCK_DEPTH for row in rows):
        raise BlockStructureError("Blocks are nested too deeply.")
    return rows


def _editable_row(rows: list[dict], instance_id: str, shell_html: str) -> dict:
    by_id = {row["id"]: row for row in rows}
    row = by_id.get(instance_id)
    if row is None or not row["available"]:
        raise BlockStructureError("Block instance is unavailable on this page.")
    root = row
    while root["parent_id"] is not None:
        root = by_id[root["parent_id"]]
        if not root["available"]:
            raise BlockStructureError("Block is inside an unavailable layout.")
    if not _body_region(root["region"]):
        raise BlockStructureError("Header and footer blocks use the visual editor.")
    _single_region_slot(shell_html, root["region"], shell=True)
    return row


def _location(stored: dict, instance_id: str):
    """Find the containing list and index in an already validated tree."""
    def walk(instances):
        for index, instance in enumerate(instances):
            if instance["id"] == instance_id:
                return instances, index
            for children in (instance.get("children") or {}).values():
                found = walk(children)
                if found is not None:
                    return found
        return None

    for instances in (stored.get("regions") or {}).values():
        found = walk(instances)
        if found is not None:
            return found
    raise BlockStructureError("Block instance is unavailable on this page.")


def _slot(stored: dict, catalog: dict, shell_html: str,
          parent_id: str | None, region: str):
    if not isinstance(region, str) or not region:
        raise BlockStructureError("A destination region is required.")
    if parent_id is None:
        names = blocks.shell_region_names(shell_html)
        if region not in names or not _body_region(region):
            raise BlockStructureError("Destination must be a body shell region.")
        _single_region_slot(shell_html, region, shell=True)
        regions = stored.get("regions")
        if regions is None:
            regions = stored["regions"] = {}
        return regions.setdefault(region, []), 0
    if not isinstance(parent_id, str):
        raise BlockStructureError("Invalid parent block ID.")
    rows = _rows(stored, catalog)
    parent_row = _editable_row(rows, parent_id, shell_html)
    parent, parent_block = content_mod.block_instances(stored, catalog)[parent_id]
    if region not in parent_block.get("regions", []):
        raise BlockStructureError("Destination is not a declared layout slot.")
    _single_region_slot(parent_block.get("html") or "", region, shell=False)
    children = parent.get("children")
    if children is None:
        children = parent["children"] = {}
    return children.setdefault(region, []), parent_row["depth"] + 1


def _position(position: int, size: int) -> None:
    if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= size:
        raise BlockStructureError("Position must be within the destination region.")


def _max_relative_depth(instance: dict) -> int:
    children = [child for values in (instance.get("children") or {}).values()
                for child in values]
    return 1 + max((_max_relative_depth(child) for child in children), default=-1)


def _has_retired_descendant(instance: dict, catalog: dict) -> bool:
    return any(
        child["type"] not in catalog or _has_retired_descendant(child, catalog)
        for values in (instance.get("children") or {}).values()
        for child in values
    )


def _check_depth(instance: dict, catalog: dict, depth: int) -> None:
    if depth + _max_relative_depth(instance) > blocks.MAX_BLOCK_DEPTH:
        raise BlockStructureError("Blocks are nested too deeply.")
    if catalog[instance["type"]].get("regions") and depth >= blocks.MAX_BLOCK_DEPTH:
        raise BlockStructureError("A layout block cannot be placed at the depth limit.")


def add_block(stored: dict, catalog: dict, shell_html: str, *, block_type: str,
              parent_id: str | None, region: str, position: int) -> tuple[dict, str]:
    existing = _rows(stored, catalog)
    if not isinstance(block_type, str) or block_type not in catalog:
        raise BlockStructureError("That block type is not available on this site.")
    if len(existing) >= blocks.MAX_BLOCKS_PER_PAGE:
        raise BlockStructureError(
            f"This page can have at most {blocks.MAX_BLOCKS_PER_PAGE} blocks."
        )
    proposed = deepcopy(stored)
    destination, depth = _slot(proposed, catalog, shell_html, parent_id, region)
    _position(position, len(destination))
    instance = blocks.seed_instance(block_type, catalog)
    seen = {row["id"] for row in existing}
    while instance["id"] in seen:
        instance["id"] = blocks.new_instance_id()
    _check_depth(instance, catalog, depth)
    destination.insert(position, instance)
    _rows(proposed, catalog)
    return proposed, instance["id"]


def remove_block(stored: dict, catalog: dict, shell_html: str, *, instance_id: str) -> dict:
    rows = _rows(stored, catalog)
    _editable_row(rows, instance_id, shell_html)
    proposed = deepcopy(stored)
    source, index = _location(proposed, instance_id)
    if _has_retired_descendant(source[index], catalog):
        raise BlockStructureError("Cannot remove a block containing unavailable children.")
    source.pop(index)
    _rows(proposed, catalog)
    return proposed


def move_block(stored: dict, catalog: dict, shell_html: str, *, instance_id: str,
               parent_id: str | None, region: str, position: int) -> dict:
    rows = _rows(stored, catalog)
    _editable_row(rows, instance_id, shell_html)
    source_row = next(row for row in rows if row["id"] == instance_id)
    by_id = {row["id"]: row for row in rows}

    def root_region(row):
        while row["parent_id"] is not None:
            row = by_id[row["parent_id"]]
        return row["region"]

    source_root = root_region(source_row)
    destination_root = root_region(by_id[parent_id]) if parent_id in by_id else region
    if source_root.startswith("body-") and destination_root != source_root:
        raise BlockStructureError("Blocks cannot move between fixed layout slots.")
    if parent_id == instance_id:
        raise BlockStructureError("A block cannot move inside itself.")
    destination_parent = parent_id
    by_id = {item["id"]: item for item in rows}
    while parent_id in by_id:
        parent_id = by_id[parent_id]["parent_id"]
        if parent_id == instance_id:
            raise BlockStructureError("A block cannot move inside its descendant.")
    proposed = deepcopy(stored)
    source, index = _location(proposed, instance_id)
    instance = source.pop(index)
    destination, depth = _slot(proposed, catalog, shell_html, destination_parent, region)
    _position(position, len(destination))
    _check_depth(instance, catalog, depth)
    destination.insert(position, instance)
    _rows(proposed, catalog)
    return proposed


def append_block_richtext(stored: dict, catalog: dict, shell_html: str, *, field: str,
                          operation: str, text: str) -> dict:
    rows = _rows(stored, catalog)
    if operation not in {"paragraph", "list_item"}:
        raise BlockStructureError("Only paragraph and list_item are supported.")
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise BlockStructureError("Text must contain 1 to 500 characters.")
    _editable_row(rows, field.split(".", 1)[0], shell_html)
    proposed = deepcopy(stored)
    try:
        instance, field_schema, merged = content_mod.block_field(proposed, catalog, field)
    except KeyError as exc:
        raise BlockStructureError("Richtext block field is unavailable.") from exc
    if field_schema.get("type") != "richtext":
        raise BlockStructureError("Field must be richtext.")
    name = field.split(".", 1)[1]
    block_html = catalog[instance["type"]].get("html") or ""
    hosts = BeautifulSoup(block_html, "html.parser").find_all(
        attrs={"data-edit": f"{instance['type']}.{name}"}
    )
    if len(hosts) != 1 or hosts[0].name not in {"div", "section", "article", "aside", "main", "li", "blockquote"}:
        raise BlockStructureError("This richtext field cannot contain paragraph or list blocks.")
    current = merged.get(name) or ""
    if not isinstance(current, str) or len(current) > 16000:
        raise BlockStructureError("Richtext field is too large to edit.")
    soup = BeautifulSoup(current, "html.parser")
    if len(soup.find_all(["p", "li"])) >= 80:
        raise BlockStructureError("Richtext field has too many paragraphs or list items.")
    if operation == "paragraph":
        element = soup.new_tag("p")
        element.string = text.strip()
        soup.append(element)
    else:
        last = next((node for node in reversed(soup.contents)
                     if isinstance(node, Tag) or str(node).strip()), None)
        if not isinstance(last, Tag) or last.name != "ul":
            last = soup.new_tag("ul")
            soup.append(last)
        item = soup.new_tag("li")
        item.string = text.strip()
        last.append(item)
    value = sanitize_template_html(str(soup))
    if len(value) > 16000:
        raise BlockStructureError("Richtext field is too large to edit.")
    instance.setdefault("fields", {})[name] = value
    return proposed
