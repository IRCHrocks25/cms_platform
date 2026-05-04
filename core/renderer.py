"""
Renderer: takes annotated HTML + tenant content and produces final output.

In preview mode, each editable element is wrapped/marked so the dashboard's
JavaScript can wire click-to-edit and live updates via postMessage.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from django.utils.html import escape


PREVIEW_BRIDGE_SCRIPT = """
<script>
(function () {
  function send(type, payload) {
    parent.postMessage({ source: 'cms-preview', type: type, payload: payload }, '*');
  }
  document.querySelectorAll('[data-edit]').forEach(function (el) {
    el.classList.add('cms-editable');
    el.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      send('focus-field', { id: el.getAttribute('data-edit') });
    });
  });
  window.addEventListener('message', function (e) {
    var data = e.data || {};
    if (data.source !== 'cms-editor') return;
    if (data.type === 'apply-content') {
      Object.entries(data.payload || {}).forEach(function (entry) {
        var fid = entry[0];
        var value = entry[1];
        document.querySelectorAll('[data-edit="' + fid + '"]').forEach(function (el) {
          var t = el.getAttribute('data-type') || 'text';
          if (t === 'image') { el.setAttribute('src', value); }
          else if (t === 'link') { el.setAttribute('href', value); }
          else if (t === 'color') {
            var prop = (el.tagName.toLowerCase() === 'span') ? 'color' : 'background-color';
            el.style[prop] = value;
          }
          else if (t === 'richtext') { el.innerHTML = value; }
          else { el.textContent = value; }
        });
      });
    }
    if (data.type === 'highlight-field') {
      document.querySelectorAll('.cms-highlight').forEach(function (el) {
        el.classList.remove('cms-highlight');
      });
      document.querySelectorAll('[data-edit="' + data.payload.id + '"]').forEach(function (el) {
        el.classList.add('cms-highlight');
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    }
  });
  send('ready', {});
})();
</script>
<style>
  .cms-editable { outline: 1px dashed transparent; outline-offset: 4px;
                  transition: outline-color 0.15s ease, background 0.15s ease; cursor: pointer; }
  .cms-editable:hover { outline-color: #6366f1; background: rgba(99, 102, 241, 0.06); }
  .cms-highlight { outline: 2px solid #7c3aed !important;
                   box-shadow: 0 0 0 6px rgba(124, 58, 237, 0.15); }
</style>
"""


def _apply_field(el, value: str, ftype: str) -> None:
    if ftype == "image":
        el["src"] = value
        return
    if ftype == "link":
        el["href"] = value
        return
    if ftype == "color":
        prop = "color" if el.name == "span" else "background-color"
        existing = el.get("style", "")
        cleaned = re.sub(rf"{prop}\s*:[^;]*;?", "", existing).strip()
        el["style"] = (cleaned + f" {prop}: {value};").strip()
        return
    if ftype == "richtext":
        el.clear()
        fragment = BeautifulSoup(value, "lxml").body
        if fragment:
            for child in list(fragment.children):
                el.append(child)
        else:
            el.append(value)
        return
    el.string = value


def _apply_brand_tokens(soup: BeautifulSoup, brand_content: dict[str, str]) -> None:
    style = soup.find("style", attrs={"data-tokens": True})
    if not style or not brand_content:
        return

    css = style.string or ""

    def replace(match):
        var_name = match.group(1)
        if var_name in brand_content:
            return f"--{var_name}: {brand_content[var_name]};"
        return match.group(0)

    style.string = re.sub(r"--([a-zA-Z0-9_-]+)\s*:\s*[^;]+;", replace, css)


def render_site(template_html: str, content: dict[str, Any], *, preview: bool = False) -> str:
    """Render the final HTML for a tenant."""
    if not template_html:
        return ""

    soup = BeautifulSoup(template_html, "lxml")

    if "brand" in content:
        _apply_brand_tokens(soup, content["brand"] or {})

    for el in soup.find_all(attrs={"data-edit": True}):
        full_id = el.get("data-edit", "").strip()
        if "." not in full_id:
            continue
        section, field = full_id.split(".", 1)
        if section == "brand":
            continue

        section_data = content.get(section) or {}
        if field not in section_data:
            continue

        ftype = el.get("data-type", "text").strip() or "text"
        _apply_field(el, section_data[field], ftype)

    if preview:
        body = soup.find("body") or soup
        bridge = BeautifulSoup(PREVIEW_BRIDGE_SCRIPT, "lxml")
        for node in list(bridge.body.children if bridge.body else bridge.children):
            body.append(node)

    return str(soup)


def merge_with_defaults(schema: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """Fill missing fields with template defaults."""
    merged: dict[str, dict[str, str]] = {}
    defaults = schema.get("defaults", {}) or {}
    for section_id, fields in defaults.items():
        merged[section_id] = dict(fields)
    for section_id, fields in (content or {}).items():
        merged.setdefault(section_id, {}).update(fields or {})
    return merged
