"""
HTML annotation parser.

Reads HTML containing data-* annotations and produces a schema describing
every editable section and field. Annotations the parser understands:

  Section markers (place on the wrapper element):
    data-section="hero"              required, unique id within template
    data-label="Welcome banner"      friendly name shown in dashboard
    data-icon="star"                 lucide icon name (optional)
    data-group="Header"              groups sections in sidebar (optional)

  Field markers (place on the editable element):
    data-edit="hero.title"           required; dotted path: <section>.<field>
    data-type="text|richtext|image|color|link"   default: text
    data-label="Headline"            friendly field name (optional)

  Global brand tokens (place on the <style> element):
    data-tokens                      contents become a "Brand" section with
                                     CSS variable values exposed as fields.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


VALID_FIELD_TYPES = {"text", "richtext", "image", "color", "link", "video"}
TOKEN_PATTERN = re.compile(r"--([a-zA-Z0-9_-]+)\s*:\s*([^;]+);")


def _humanize(token: str) -> str:
    return token.replace("-", " ").replace("_", " ").strip().title()


def _parse_brand_tokens(soup: BeautifulSoup) -> dict[str, Any] | None:
    style = soup.find("style", attrs={"data-tokens": True})
    if not style:
        return None

    fields: list[dict[str, Any]] = []
    for match in TOKEN_PATTERN.finditer(style.text or ""):
        name, value = match.group(1), match.group(2).strip()
        ftype = "color" if value.startswith("#") or value.startswith("rgb") else "text"
        fields.append(
            {
                "id": f"brand.{name}",
                "label": _humanize(name),
                "type": ftype,
                "default": value,
            }
        )

    if not fields:
        return None

    return {
        "id": "brand",
        "label": "Brand",
        "icon": "palette",
        "group": "Global",
        "fields": fields,
    }


def _extract_default(el, ftype: str) -> str:
    if ftype == "image":
        return el.get("src", "")
    if ftype == "video":
        if el.get("src"):
            return el.get("src", "")
        source = el.find("source")
        return source.get("src", "") if source else ""
    if ftype == "link":
        return el.get("href", "")
    if ftype == "color":
        style = el.get("style", "")
        match = re.search(r"(?:background|color)\s*:\s*([^;]+)", style)
        return match.group(1).strip() if match else ""
    if ftype == "richtext":
        return el.decode_contents().strip()
    return el.get_text(strip=True)


def build_schema(html: str) -> dict[str, Any]:
    """Parse annotated HTML and return a structured schema."""
    if not html or not html.strip():
        return {"sections": [], "defaults": {}}

    soup = BeautifulSoup(html, "lxml")
    sections: list[dict[str, Any]] = []
    defaults: dict[str, dict[str, str]] = {}

    brand = _parse_brand_tokens(soup)
    if brand:
        sections.append(brand)
        defaults["brand"] = {f["id"].split(".")[-1]: f["default"] for f in brand["fields"]}

    for sec in soup.find_all(attrs={"data-section": True}):
        sec_id = sec["data-section"].strip()
        if not sec_id:
            continue

        section_entry = {
            "id": sec_id,
            "label": sec.get("data-label", _humanize(sec_id)),
            "icon": sec.get("data-icon", "square"),
            "group": sec.get("data-group", "Sections"),
            "fields": [],
        }

        section_defaults: dict[str, str] = {}

        for field_el in sec.find_all(attrs={"data-edit": True}):
            full_id = field_el["data-edit"].strip()
            if "." not in full_id:
                continue

            section_part, field_part = full_id.split(".", 1)
            if section_part != sec_id:
                continue

            ftype = field_el.get("data-type", "text").strip()
            if ftype not in VALID_FIELD_TYPES:
                ftype = "text"

            default = _extract_default(field_el, ftype)

            style_editable = (
                ftype in ("text", "richtext", "link")
                and field_el.get("data-style", "").strip().lower() != "off"
            )

            section_entry["fields"].append(
                {
                    "id": full_id,
                    "label": field_el.get("data-label", _humanize(field_part)),
                    "type": ftype,
                    "default": default,
                    "style_editable": style_editable,
                }
            )
            section_defaults[field_part] = default

        if section_entry["fields"]:
            sections.append(section_entry)
            defaults[sec_id] = section_defaults

    # In-page anchor destinations the template wires up (e.g. "#programs"), so the
    # editor can offer non-technical clients a friendly dropdown of their own
    # site's sections instead of asking them to type raw anchors. A link field can
    # still hold a custom URL / mailto — this list is just the convenient choices.
    section_labels = {s["id"]: s["label"] for s in sections}
    link_targets: list[dict[str, str]] = []
    seen_anchors: set[str] = set()
    for el in soup.find_all(href=True):
        href = (el.get("href") or "").strip()
        if not href.startswith("#") or len(href) <= 1 or href in seen_anchors:
            continue
        seen_anchors.add(href)
        anchor_id = href[1:]
        label = section_labels.get(anchor_id) or _humanize(anchor_id)
        link_targets.append({"value": href, "label": label})

    return {"sections": sections, "defaults": defaults, "link_targets": link_targets}
