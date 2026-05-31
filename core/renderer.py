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

from core.services.sanitizer import sanitize_html


PREVIEW_BRIDGE_SCRIPT = """
<script>
(function () {
  function send(type, payload) {
    parent.postMessage({ source: 'cms-preview', type: type, payload: payload }, '*');
  }
  // Minimal in-browser HTML scrub for live richtext apply (same-origin preview).
  // <template> content is inert, so onerror/onload don't fire while we clean.
  function cmsScrub(html) {
    var tpl = document.createElement('template');
    tpl.innerHTML = html || '';
    var bad = tpl.content.querySelectorAll(
      'script,style,iframe,object,embed,form,input,button,link,meta,base,svg,math,noscript'
    );
    for (var i = 0; i < bad.length; i++) { bad[i].remove(); }
    var els = tpl.content.querySelectorAll('*');
    for (var j = 0; j < els.length; j++) {
      var el = els[j];
      for (var k = el.attributes.length - 1; k >= 0; k--) {
        var name = el.attributes[k].name.toLowerCase();
        var val = (el.attributes[k].value || '').replace(/\\s/g, '').toLowerCase();
        if (name.indexOf('on') === 0) { el.removeAttribute(el.attributes[k].name); }
        else if ((name === 'href' || name === 'src' || name === 'xlink:href') &&
                 val.indexOf('javascript:') === 0) { el.removeAttribute(el.attributes[k].name); }
      }
    }
    return tpl.innerHTML;
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
          else if (t === 'video') {
            if (el.tagName.toLowerCase() === 'video') {
              var vsrc = el.querySelector('source');
              if (vsrc) { vsrc.setAttribute('src', value); } else { el.setAttribute('src', value); }
              if (el.load) { el.load(); }
            } else { el.setAttribute('src', value); }
          }
          else if (t === 'link') { el.setAttribute('href', value); }
          else if (t === 'color') {
            var prop = (el.tagName.toLowerCase() === 'span') ? 'color' : 'background-color';
            el.style[prop] = value;
          }
          else if (t === 'richtext') { el.innerHTML = cmsScrub(value); }
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
    if (data.type === 'scroll-to-section') {
      var sec = document.querySelector('[data-section="' + data.payload.id + '"]');
      if (sec) {
        sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
        sec.classList.remove('cms-section-flash');
        void sec.offsetWidth; // restart the animation if re-clicked
        sec.classList.add('cms-section-flash');
      }
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
  .cms-section-flash { animation: cms-section-flash 1.2s ease; }
  @keyframes cms-section-flash {
    0%   { outline: 2px solid rgba(124, 58, 237, 0); outline-offset: -2px; }
    25%  { outline: 2px solid rgba(124, 58, 237, 0.85); outline-offset: -2px; }
    100% { outline: 2px solid rgba(124, 58, 237, 0); outline-offset: -2px; }
  }
</style>
"""


def _apply_field(el, value: str, ftype: str) -> None:
    if ftype == "image":
        el["src"] = value
        return
    if ftype == "video":
        # Prefer updating an inner <source> if present, else set src on the element.
        source = el.find("source") if el.name == "video" else None
        if source is not None:
            source["src"] = value
        else:
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
        # Client-authored HTML — strip scripts / event handlers / unsafe URLs
        # before it ever lands in the rendered (and same-origin preview) DOM.
        value = sanitize_html(value)
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


GA_SCRIPT_TEMPLATE = """<script async src="https://www.googletagmanager.com/gtag/js?id={mid}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','{mid}');</script>"""


def _inject_site_settings(soup: BeautifulSoup, site_settings: dict[str, Any]) -> None:
    if not site_settings:
        return

    head = soup.find("head")
    if not head:
        return

    page_title = (site_settings.get("page_title") or "").strip()
    if page_title:
        existing_title = head.find("title")
        if existing_title:
            existing_title.string = page_title
        else:
            tag = soup.new_tag("title")
            tag.string = page_title
            head.append(tag)

    meta_desc = (site_settings.get("meta_description") or "").strip()
    if meta_desc:
        existing = head.find("meta", attrs={"name": "description"})
        if existing:
            existing["content"] = meta_desc
        else:
            tag = soup.new_tag("meta", attrs={"name": "description", "content": meta_desc})
            head.append(tag)

    og_image = (site_settings.get("og_image_url") or "").strip()
    if og_image:
        for prop in ("og:image", "twitter:image"):
            existing = head.find("meta", attrs={"property": prop}) or head.find("meta", attrs={"name": prop})
            if existing:
                existing["content"] = og_image
            else:
                tag = soup.new_tag("meta", attrs={"property": prop, "content": og_image})
                head.append(tag)

    if page_title:
        for prop in ("og:title", "twitter:title"):
            existing = head.find("meta", attrs={"property": prop}) or head.find("meta", attrs={"name": prop})
            if existing:
                existing["content"] = page_title
            else:
                tag = soup.new_tag("meta", attrs={"property": prop, "content": page_title})
                head.append(tag)

    if meta_desc:
        for prop in ("og:description", "twitter:description"):
            existing = head.find("meta", attrs={"property": prop}) or head.find("meta", attrs={"name": prop})
            if existing:
                existing["content"] = meta_desc
            else:
                tag = soup.new_tag("meta", attrs={"property": prop, "content": meta_desc})
                head.append(tag)

    ga_id = (site_settings.get("ga_measurement_id") or "").strip()
    if ga_id and re.match(r"^(G-[A-Za-z0-9]+|UA-\d+-\d+)$", ga_id):
        snippet = BeautifulSoup(GA_SCRIPT_TEMPLATE.format(mid=escape(ga_id)), "html.parser")
        for node in list(snippet.children):
            head.append(node)

    custom_script = (site_settings.get("custom_head_script") or "").strip()
    if custom_script:
        fragment = BeautifulSoup(custom_script, "html.parser")
        for node in list(fragment.children):
            head.append(node)


def apply_head_settings(html: str, head_settings: dict[str, Any] | None) -> str:
    """Inject SEO/analytics head tags into a standalone HTML page.

    Reuses the Site-Settings head-injection so blog pages (which are plain
    Django templates, not annotated templates) get the same ``<title>``,
    meta, OG/Twitter, GA snippet and custom head script behavior as the
    main site — with per-page overrides layered in by the caller.
    """
    if not html or not head_settings:
        return html
    soup = BeautifulSoup(html, "lxml")
    _inject_site_settings(soup, head_settings)
    return str(soup)


def render_site(
    template_html: str,
    content: dict[str, Any],
    *,
    preview: bool = False,
    site_settings: dict[str, Any] | None = None,
) -> str:
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

    if not preview and site_settings:
        _inject_site_settings(soup, site_settings)

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
