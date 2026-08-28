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

from core.ghl_embed import parse_ghl_embed_value
from core.parser import build_schema, effective_field_type, parse_select_options
from core.services.template_sanitizer import (
    canonicalize_fragment,
    sanitize_template_html,
)


PREVIEW_BRIDGE_SCRIPT = """
<script>
(function () {
  function send(type, payload) {
    parent.postMessage({ source: 'cms-preview', type: type, payload: payload }, '*');
  }
  // Answer the editor's readiness ping so a missed initial 'ready' (a handshake
  // race after a reload) always self-heals. Registered FIRST — before any other
  // setup — so it responds even if later init throws.
  window.addEventListener('message', function (e) {
    if (e.source && e.source !== window.parent) return;
    var d = e && e.data;
    if (d && d.source === 'cms-editor' && d.type === 'ping') send('ready', {});
  });
  var CMS_STYLE_PROP = { color: 'color', bgColor: 'backgroundColor', fontSize: 'fontSize',
    fontFamily: 'fontFamily', fontWeight: 'fontWeight', align: 'textAlign',
    lineHeight: 'lineHeight', letterSpacing: 'letterSpacing', textTransform: 'textTransform',
    padding: 'padding', margin: 'margin', width: 'width', maxWidth: 'maxWidth',
    minHeight: 'minHeight', borderRadius: 'borderRadius' };
  function cmsEnsureFont(family) {
    if (!family) return;
    var safe = String(family).replace(/[^A-Za-z0-9 \\-]/g, '').trim();
    if (!safe) return;
    var id = 'cms-font-' + safe.replace(/ /g, '-');
    if (document.getElementById(id)) return;
    var link = document.createElement('link');
    link.id = id;
    link.rel = 'stylesheet';
    link.setAttribute('data-cookieconsent', 'ignore');
    link.href = 'https://fonts.googleapis.com/css2?family=' +
      safe.replace(/ /g, '+') + ':wght@300;400;500;600;700;800&display=swap';
    document.head.appendChild(link);
  }
  function cmsParseGradient(value) {
    var m = String(value || '').trim().match(/^([0-9]{1,3})deg\s*,\s*(#[0-9A-Fa-f]{3,8})\s*,\s*(#[0-9A-Fa-f]{3,8})$/);
    if (!m || parseInt(m[1], 10) > 360) return '';
    return m[1] + 'deg, ' + m[2] + ', ' + m[3];
  }
  function cmsApplyStyle(el, style) {
    Object.keys(CMS_STYLE_PROP).forEach(function (k) {
      if (style[k] !== undefined && style[k] !== null && style[k] !== '') {
        el.style[CMS_STYLE_PROP[k]] = style[k];
      } else {
        el.style[CMS_STYLE_PROP[k]] = '';
      }
    });
    el.style.fontStyle = style.italic ? 'italic' : '';
    if (style.padding) el.style.boxSizing = 'border-box';
    else el.style.boxSizing = '';
    if (style.maxWidth && !style.margin) {
      el.style.marginLeft = 'auto';
      el.style.marginRight = 'auto';
    }
    var mode = String(style.bgMode || '').toLowerCase();
    if (mode !== 'color' && mode !== 'image' && mode !== 'gradient') {
      mode = style.bgImage ? 'image' : (style.bgGradient ? 'gradient' : 'color');
    }
    var layers = [];
    var overlay = parseInt(style.bgOverlay, 10);
    if (mode === 'image' && overlay > 0 && overlay <= 80) {
      var a = (overlay / 100).toFixed(2);
      layers.push('linear-gradient(rgba(0,0,0,' + a + '),rgba(0,0,0,' + a + '))');
    }
    if (mode === 'gradient') {
      var g = cmsParseGradient(style.bgGradient);
      if (g) layers.push('linear-gradient(' + g + ')');
    }
    if (mode === 'image') {
      var u = cmsSafeUrl(style.bgImage, { dataImage: true });
      if (u) layers.push('url("' + String(u).replace(/["')(]/g, '') + '")');
    }
    var opacity = parseInt(style.bgOpacity, 10);
    if (!(opacity >= 1 && opacity <= 100)) opacity = 100;
    var blur = parseInt(style.bgBlur, 10);
    if (!(blur >= 1 && blur <= 20)) blur = 0;
    var useFx = mode === 'image' && layers.length && (opacity < 100 || blur > 0);
    var fx = el.querySelector(':scope > .cms-bg-fx');
    if (!useFx) {
      if (fx) fx.parentNode.removeChild(fx);
      el.classList.remove('cms-bg-fx-host', 'cms-bg-fx-clip');
      el.style.backgroundImage = layers.join(',');
      el.style.backgroundSize = (mode === 'image' && style.bgImage) ? (style.bgSize || 'cover') : '';
      el.style.backgroundPosition = (mode === 'image' && style.bgImage) ? (style.bgPosition || 'center') : '';
      el.style.backgroundRepeat = (mode === 'image' && style.bgImage) ? 'no-repeat' : '';
    } else {
      if (!fx) {
        fx = document.createElement('div');
        fx.className = 'cms-bg-fx';
        fx.setAttribute('aria-hidden', 'true');
        el.insertBefore(fx, el.firstChild);
      }
      el.classList.add('cms-bg-fx-host');
      el.classList.toggle('cms-bg-fx-clip', blur > 0);
      el.style.backgroundImage = '';
      el.style.backgroundSize = '';
      el.style.backgroundPosition = '';
      el.style.backgroundRepeat = '';
      fx.style.backgroundImage = layers.join(',');
      fx.style.backgroundSize = style.bgSize || 'cover';
      fx.style.backgroundPosition = style.bgPosition || 'center';
      fx.style.backgroundRepeat = 'no-repeat';
      fx.style.opacity = String(opacity / 100);
      fx.style.filter = blur ? ('blur(' + blur + 'px)') : '';
    }
    var box = {DIV:1,SECTION:1,ARTICLE:1,HEADER:1,FOOTER:1,ASIDE:1,MAIN:1,LI:1};
    if (mode === 'image' && style.bgImage && box[el.tagName]) {
      if (!style.minHeight) el.style.minHeight = '220px';
      if (!style.padding) { el.style.padding = '32px 20px'; el.style.boxSizing = 'border-box'; }
      el.style.width = '100%';
    }
    if (style.fontFamily) cmsEnsureFont(style.fontFamily);
    // Text color must also override styled descendants (<em>/<span>/<strong>/
    // <cite> with their own color rule), which a parent color can't do.
    var kids = el.querySelectorAll('*');
    for (var i = 0; i < kids.length; i++) {
      // Leave selection-styled spans (and their contents) alone so a per-part
      // colour survives a whole-element colour on the same element.
      if (kids[i].closest && kids[i].closest('.cms-tspan')) continue;
      if (style.color) { kids[i].style.setProperty('color', style.color, 'important'); }
      else { kids[i].style.removeProperty('color'); }
    }
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
  // Mirror the server sanitizers so live-typed values can't inject CSS or a
  // scriptable URL between saves (server re-validates on the next render).
  function cmsSafeUrl(value, opts) {
    opts = opts || {};
    var v = String(value == null ? '' : value).trim();
    if (!v) return '';
    if (/[\\x00-\\x1f]/.test(v)) return null;
    var collapsed = v.replace(/\\s/g, '').toLowerCase();
    if (collapsed.indexOf('javascript:') === 0 || collapsed.indexOf('vbscript:') === 0 ||
        collapsed.indexOf('data:text/html') === 0) return null;
    var low = v.toLowerCase();
    if (low.indexOf('http://') === 0 || low.indexOf('https://') === 0 ||
        low.indexOf('mailto:') === 0 || low.indexOf('tel:') === 0) return v;
    if (opts.dataImage && low.indexOf('data:image/') === 0) return v;
    if (v.charAt(0) === '/') return v;
    if (opts.anchor && v.charAt(0) === '#') return v;
    if (v.split('/')[0].indexOf(':') === -1) return v;
    return null;
  }
  var CMS_SAFE_TOKEN = /^[A-Za-z0-9.%\\-\\s]+$/;
  var CMS_SAFE_CSS = /^#[0-9A-Fa-f]{3,8}$|^[a-zA-Z]+$|^(?:rgb|rgba|hsl|hsla)\\([0-9.,%\\s\\/]+\\)$/;
  function cmsSafeCssValue(value) { var v = String(value == null ? '' : value).trim(); return CMS_SAFE_CSS.test(v) ? v : ''; }
  function cmsSafeToken(value) { var v = String(value == null ? '' : value).trim(); return CMS_SAFE_TOKEN.test(v) ? v : ''; }
  function cmsSafeFont(value) { return String(value == null ? '' : value).replace(/[^A-Za-z0-9 \\-]/g, '').trim(); }
  // Phrasing hosts (<p>, <h2>, <cite>, ...) can't legally contain a block
  // element. A contenteditable often wraps a typed line in <p>, so setting
  // pHost.innerHTML = "<p>text</p>" makes the browser split the node into an
  // empty editable host + a stray, un-clickable <p>. Flatten block children
  // back to inline (mirrors _flatten_for_phrasing_host on the server).
  var CMS_PHRASING = {p:1,h1:1,h2:1,h3:1,h4:1,h5:1,h6:1,span:1,a:1,cite:1,em:1,
    strong:1,b:1,i:1,u:1,small:1,label:1,summary:1,figcaption:1,dt:1,caption:1,legend:1};
  var CMS_BLOCK = {p:1,div:1,section:1,article:1,header:1,footer:1,aside:1,main:1,
    ul:1,ol:1,li:1,blockquote:1,pre:1,table:1,figure:1,address:1};
  function cmsIsBlankHtml(value) {
    var s = String(value == null ? '' : value);
    s = s.replace(/&nbsp;|&#160;|\\u00a0/gi, ' ');
    s = s.replace(/<br\\s*\\/?>/gi, '');
    s = s.replace(/<[^>]+>/g, '');
    return !s.replace(/\\s+/g, '');
  }
  function cmsRichtextHTML(host, html) {
    var clean = cmsScrub(html);
    if (!CMS_PHRASING[host.tagName.toLowerCase()]) return clean;
    var tpl = document.createElement('template');
    tpl.innerHTML = clean;
    for (var pass = 0; pass < 4; pass++) {
      var blocks = [];
      for (var i = 0; i < tpl.content.children.length; i++) {
        if (CMS_BLOCK[tpl.content.children[i].tagName.toLowerCase()]) blocks.push(tpl.content.children[i]);
      }
      if (!blocks.length) break;
      for (var b = 0; b < blocks.length; b++) {
        var block = blocks[b];
        if (b > 0) block.parentNode.insertBefore(document.createElement('br'), block);
        while (block.firstChild) block.parentNode.insertBefore(block.firstChild, block);
        block.parentNode.removeChild(block);
      }
    }
    return tpl.innerHTML;
  }
  document.querySelectorAll('[data-edit]').forEach(function (el) {
    if (el.getAttribute('data-header-name') === 'off' || el.hasAttribute('hidden')) return;
    el.classList.add('cms-editable');
    el.addEventListener('click', function (e) {
      if (cmsEditingEl === el) return;   // clicking to move the caret while editing
      e.preventDefault();
      e.stopPropagation();
      var blk = el.closest ? el.closest('[data-instance-id]') : null;
      if (blk) cmsSelectBlock(blk);
      send('focus-field', { id: el.getAttribute('data-edit') });
      var inNav = !!(el.closest && el.closest(
        'header, [data-region^="header"], .site-nav, .site-header-actions, .site-brand'
      ));
      var kind = el.getAttribute('data-type') || 'text';
      if (el.querySelector && el.querySelector('.site-header-logo')) {
        var chrome = el.closest('[data-section]');
        send('select-block', { id: chrome ? chrome.getAttribute('data-section') : 'header' });
        return;
      }
      if (inNav && (kind === 'text' || kind === 'richtext')) cmsBeginEdit(el);
    });
    // Double-click a text/richtext element to edit it right on the canvas.
    var dt = el.getAttribute('data-type') || 'text';
    if (dt === 'image') {
      el.classList.add('cms-image-editable');
      el.addEventListener('dblclick', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var blk = el.closest ? el.closest('[data-instance-id]') : null;
        if (blk) cmsSelectBlock(blk);
        send('replace-image', { id: el.getAttribute('data-edit') });
      });
    }
    if (dt === 'text' || dt === 'richtext') {
      el.classList.add('cms-inline-editable');
      el.addEventListener('dblclick', function (e) {
        e.preventDefault();
        e.stopPropagation();
        cmsBeginEdit(el);
      });
    }
  });

  // ---- inline (in-canvas) text editing ---------------------------------
  // Double-click makes a text/richtext element contentEditable; blur or Enter
  // (plain text) commits and echoes text-update so the editor persists it and
  // syncs the matching form field. Escape reverts. Selection styling (the
  // floating bubble) keeps working because it manipulates the DOM directly.
  var cmsEditingEl = null, cmsEditOrig = null;
  function cmsBeginEdit(el) {
    if (cmsEditingEl === el) return;
    if (cmsEditingEl) cmsCommitEdit();
    var dt = el.getAttribute('data-type') || (el.hasAttribute('data-header-link') ? 'text' : '');
    if (dt !== 'text' && dt !== 'richtext') return;
    cmsEditingEl = el;
    cmsEditOrig = el.innerHTML;
    el.setAttribute('contenteditable', 'true');
    el.classList.add('cms-inline-editing');
    el.focus();
    // Select the whole element's text so typing replaces it (feels like a
    // single-line rename); the user can click to place a caret instead.
    try {
      var sel = window.getSelection(); sel.removeAllRanges();
      var r = document.createRange(); r.selectNodeContents(el); sel.addRange(r);
    } catch (e) {}
    send('inline-edit', {
      id: el.getAttribute('data-edit') || el.getAttribute('data-header-link'),
      editing: true
    });
  }
  function cmsReadEditable(el) {
    var dt = el.getAttribute('data-type') || 'text';
    // Plain text stays plain unless it carries inline markup (styled spans,
    // links, <br>) — then store HTML, mirroring apply-content's text branch.
    if (dt === 'richtext') return el.innerHTML;
    return el.querySelector('*') ? el.innerHTML : el.textContent;
  }
  function cmsEndEdit(el) {
    el.removeAttribute('contenteditable');
    el.classList.remove('cms-inline-editing');
    send('inline-edit', {
      id: el.getAttribute('data-edit') || el.getAttribute('data-header-link'),
      editing: false
    });
  }
  function cmsCommitEdit() {
    if (!cmsEditingEl) return;
    var el = cmsEditingEl; cmsEditingEl = null; cmsEditOrig = null;
    var val = cmsReadEditable(el);
    cmsEndEdit(el);
    var hid = el.getAttribute('data-header-link');
    if (hid) send('header-menu-label', { id: hid, html: val });
    else send('text-update', { id: el.getAttribute('data-edit'), html: val });
  }
  function cmsCancelEdit() {
    if (!cmsEditingEl) return;
    var el = cmsEditingEl; cmsEditingEl = null;
    if (cmsEditOrig != null) el.innerHTML = cmsEditOrig;
    cmsEditOrig = null;
    cmsEndEdit(el);
  }
  // Commit when focus leaves the edited element (e.g. clicking the canvas or a
  // toolbar control in the parent frame). The stored selection range keeps the
  // floating bubble targeting the right text afterwards.
  document.addEventListener('focusout', function (e) {
    if (cmsEditingEl && e.target === cmsEditingEl) {
      window.setTimeout(function () { if (cmsEditingEl === e.target) cmsCommitEdit(); }, 0);
    }
  });
  document.addEventListener('keydown', function (e) {
    if (!cmsEditingEl) return;
    if (e.key === 'Escape') { e.preventDefault(); cmsCancelEdit(); return; }
    if (e.key === 'Enter' && !e.shiftKey) {
      var dt = cmsEditingEl.getAttribute('data-type') || 'text';
      if (dt === 'text') { e.preventDefault(); cmsCommitEdit(); }
    }
  });

  // Block-instance selection: clicking a block's chrome (anywhere that isn't an
  // editable field, whose handler stops propagation above) selects that block
  // in the editor so its fields + layer entry light up. Only present on
  // block-shell pages; a no-op elsewhere.
  document.querySelectorAll('[data-instance-id]').forEach(function (el) {
    el.classList.add('cms-block-instance');
    el.addEventListener('click', function (e) {
      e.stopPropagation();   // innermost block wins; don't reselect ancestors
      cmsSelectBlock(el);
      send('select-block', { id: el.getAttribute('data-instance-id') });
    });
    var home = el.closest('[data-region]');
    var rname = home && (home.getAttribute('data-region') || '');
    if (rname && /^footer/.test(rname) && !el.querySelector(':scope > .cms-chrome-drag')) {
      var handle = document.createElement('button');
      handle.type = 'button';
      handle.className = 'cms-chrome-drag';
      handle.setAttribute('draggable', 'true');
      handle.setAttribute('title', 'Drag to another header/footer column');
      handle.setAttribute('aria-label', 'Drag to move');
      handle.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';
      handle.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); });
      handle.addEventListener('dragstart', function (e) {
        var id = el.getAttribute('data-instance-id');
        cmsDragMoveId = id;
        e.dataTransfer.effectAllowed = 'move';
        try {
          e.dataTransfer.setData('application/x-cms-moveblock', id);
          e.dataTransfer.setData('text/plain', id);
        } catch (_) {}
        el.classList.add('cms-dragging');
        if (cmsSelFrame) cmsSelFrame.style.visibility = 'hidden';
        e.stopPropagation();
      });
      handle.addEventListener('dragend', function () {
        el.classList.remove('cms-dragging');
        if (cmsSelFrame) cmsSelFrame.style.visibility = '';
        cmsDragMoveId = null;
        cmsDragClear();
      });
      el.classList.add('cms-chrome-drag-host');
      el.insertBefore(handle, el.firstChild);
    }
  });
  // Fixed chrome (nav / footer) is not a block instance. Clicking the bar
  // itself — not an annotated field — still opens Header/Footer properties.
  document.querySelectorAll('[data-section]').forEach(function (el) {
    if (el.hasAttribute('data-instance-id')) return;
    el.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('[data-edit], [data-header-add-link], [data-header-add-button]')) return;
      e.preventDefault();
      e.stopPropagation();
      send('select-block', { id: el.getAttribute('data-section') });
    });
  });
  document.querySelectorAll('[data-header-link]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var chrome = el.closest('[data-section]');
      send('select-block', { id: chrome ? chrome.getAttribute('data-section') : 'header' });
      cmsBeginEdit(el);
    });
  });
  document.querySelectorAll('[data-header-add-link]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      send('header-add-link', {});
    });
  });
  document.querySelectorAll('[data-header-add-button]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      send('header-add-button', {});
    });
  });
  // A photo in the block may not be the annotated node (broken <img> wrappers).
  // If the block has exactly one image field, clicking any <img> replaces that.
  document.querySelectorAll('[data-instance-id] img').forEach(function (img) {
    if (img.getAttribute('data-edit') && (img.getAttribute('data-type') || '') === 'image') return;
    img.addEventListener('click', function (e) {
      var blk = img.closest('[data-instance-id]');
      if (!blk) return;
      var slots = blk.querySelectorAll('[data-edit][data-type="image"]');
      if (slots.length !== 1) return;
      e.preventDefault();
      e.stopPropagation();
      cmsSelectBlock(blk);
      send('focus-field', { id: slots[0].getAttribute('data-edit') });
    });
    img.addEventListener('dblclick', function (e) {
      var blk = img.closest('[data-instance-id]');
      if (!blk) return;
      var slots = blk.querySelectorAll('[data-edit][data-type="image"]');
      if (slots.length !== 1) return;
      e.preventDefault();
      e.stopPropagation();
      cmsSelectBlock(blk);
      send('replace-image', { id: slots[0].getAttribute('data-edit') });
    });
  });

  // Empty-column "+" adders: clicking one opens the Quick Add drawer in the
  // editor with that column preselected as the destination (GHL-style).
  document.querySelectorAll('[data-cms-add-here]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var dest = el.getAttribute('data-cms-dest') || '';
      var key = el.getAttribute('data-cms-add-type') || '';
      if (key) send('canvas-add', { key: key, dest: dest });
      else send('add-block', { dest: dest });
    });
  });

  // ---- selection-based text styling ------------------------------------
  // Highlight text inside a richtext field on the preview and the editor's
  // side panel restyles just that selection: we wrap it in a styled <span>,
  // stored in the field's own richtext HTML (so a two-colour heading is just
  // a span, not a separate style layer). The live range is remembered so it
  // survives focus moving to the editor's controls in the parent frame.
  var cmsSelRange = null, cmsSelField = null;
  function cmsRichHost(node) {
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    return el && el.closest ? el.closest('[data-edit]') : null;
  }
  function cmsIsStyleable(host) {
    if (!host) return false;
    // Any text-bearing field, including headings, paragraphs, list items, etc. (like
    // lp-cms, which lets you style every text element, not just "rich" ones).
    // Only non-text fields are excluded. Styling a plain text field just turns
    // its value into inline HTML (a styled span).
    var t = host.getAttribute('data-type') || 'text';
    return t !== 'image' && t !== 'video' && t !== 'color' && t !== 'link' &&
      t !== 'ghl-embed' && t !== 'select' && t !== 'embed' && t !== 'code';
  }
  function cmsApplySelect(el, value) {
    value = value == null ? '' : String(value);
    var raw = (el.getAttribute('data-options') || '').trim();
    var allowed = [];
    raw.split(';').forEach(function (chunk) {
      chunk = chunk.trim(); if (!chunk) return;
      var v = chunk.indexOf('=') >= 0 ? chunk.slice(chunk.indexOf('=') + 1) : chunk;
      v = v.trim(); if (v && allowed.indexOf(v) < 0) allowed.push(v);
    });
    var apply = (el.getAttribute('data-apply') || 'class').trim();
    if (apply.indexOf('style:') === 0) {
      var prop = apply.slice(6).trim();
      if (!/^[a-zA-Z-]{1,40}$/.test(prop)) return;
      el.style.removeProperty(prop);
      if (allowed.indexOf(value) >= 0 && value) el.style.setProperty(prop, value);
      return;
    }
    allowed.forEach(function (c) { el.classList.remove(c); });
    if (allowed.indexOf(value) >= 0 && value) el.classList.add(value);
  }
  document.addEventListener('selectionchange', function () {
    var s = window.getSelection();
    if (!s || !s.rangeCount || s.isCollapsed) { send('text-selection', { present: false }); return; }
    var r = s.getRangeAt(0);
    var host = cmsRichHost(r.commonAncestorContainer);
    if (!cmsIsStyleable(host)) { send('text-selection', { present: false }); return; }
    // Remember this selection; keep it across later focus loss so the parent's
    // colour/size controls still target it.
    cmsSelRange = r.cloneRange();
    cmsSelField = host.getAttribute('data-edit');
    // Send the selection's rect (iframe-viewport coords) so the editor can
    // float the style bubble right below the highlighted text.
    var rc = r.getBoundingClientRect();
    send('text-selection', { present: true, id: cmsSelField,
      rect: { left: rc.left, top: rc.top, bottom: rc.bottom, width: rc.width, height: rc.height } });
  });
  function cmsStyleSelection(prop, value) {
    if (!cmsSelRange) return null;
    var host = cmsRichHost(cmsSelRange.commonAncestorContainer);
    if (!cmsIsStyleable(host)) return null;
    var node = cmsSelRange.commonAncestorContainer;
    if (node && node.nodeType === 3) node = node.parentNode;
    var existing = node && node.closest ? node.closest('.cms-tspan') : null;
    // Restyle the current wrap instead of nesting another span. A colour
    // slider otherwise stacked 10+ cms-tspan wraps and baked over the
    // template's designed colours.
    if (existing && host.contains(existing)) {
      existing.style.setProperty(prop, value);
      return host;
    }
    var sp = document.createElement('span');
    sp.className = 'cms-tspan'; // marks a selection-styled span so the
                                // whole-element recolor rule leaves it alone
    sp.style.setProperty(prop, value);
    var r = cmsSelRange.cloneRange();
    try { r.surroundContents(sp); }
    catch (e) { try { sp.appendChild(r.extractContents()); r.insertNode(sp); } catch (_) { return null; } }
    if (prop === 'font-family') cmsEnsureFont(value);
    // Re-select the wrapped text so successive tweaks stack on the same span.
    var sel = window.getSelection(); sel.removeAllRanges();
    var nr = document.createRange(); nr.selectNodeContents(sp); sel.addRange(nr);
    cmsSelRange = nr.cloneRange();
    return host;
  }
  function cmsClearSelection() {
    if (!cmsSelRange) return null;
    var host = cmsRichHost(cmsSelRange.commonAncestorContainer);
    if (!cmsIsStyleable(host)) return null;
    var r = cmsSelRange;
    Array.prototype.slice.call(host.querySelectorAll('span[style]')).forEach(function (sp) {
      if (r.intersectsNode(sp)) {
        while (sp.firstChild) sp.parentNode.insertBefore(sp.firstChild, sp);
        sp.parentNode.removeChild(sp);
      }
    });
    host.normalize();
    return host;
  }
  // Wrap the current selection in an <a href> (or unwrap it). Stored like the
  // styled spans above — inside the field's own HTML — so links survive save.
  function cmsLinkSelection(href) {
    if (!cmsSelRange || !href) return null;
    var host = cmsRichHost(cmsSelRange.commonAncestorContainer);
    if (!cmsIsStyleable(host)) return null;
    var a = document.createElement('a');
    a.setAttribute('href', href);
    var r = cmsSelRange.cloneRange();
    try { r.surroundContents(a); }
    catch (e) { try { a.appendChild(r.extractContents()); r.insertNode(a); } catch (_) { return null; } }
    var sel = window.getSelection(); sel.removeAllRanges();
    var nr = document.createRange(); nr.selectNodeContents(a); sel.addRange(nr);
    cmsSelRange = nr.cloneRange();
    return host;
  }
  function cmsUnlinkSelection() {
    if (!cmsSelRange) return null;
    var host = cmsRichHost(cmsSelRange.commonAncestorContainer);
    if (!cmsIsStyleable(host)) return null;
    var r = cmsSelRange;
    Array.prototype.slice.call(host.querySelectorAll('a')).forEach(function (a) {
      if (r.intersectsNode(a)) {
        while (a.firstChild) a.parentNode.insertBefore(a.firstChild, a);
        a.parentNode.removeChild(a);
      }
    });
    host.normalize();
    return host;
  }
  window.addEventListener('message', function (e) {
    // Only trust messages from the dashboard window that framed us. An
    // attacker window can spoof the `source` string but not `e.source`.
    if (e.source && e.source !== window.parent) return;
    var data = e.data || {};
    if (data.source !== 'cms-editor') return;
    if (data.type === 'apply-content') {
      Object.entries(data.payload || {}).forEach(function (entry) {
        var fid = entry[0];
        var value = entry[1];
        if (fid.slice(-5) === '_href') {
          var hrefHost = fid.slice(0, -5);
          var lurlH = cmsSafeUrl(value, { anchor: true });
          if (lurlH === null) return;
          document.querySelectorAll('[data-edit="' + hrefHost + '"]').forEach(function (host) {
            host.setAttribute('href', lurlH);
          });
          return;
        }
        document.querySelectorAll('[data-edit="' + fid + '"]').forEach(function (el) {
          var t = el.getAttribute('data-type') || 'text';
          if (t === 'ghl-embed') { return; }
          if (t === 'select') { cmsApplySelect(el, value); return; }
          if (t === 'embed') { var esrc = cmsSafeUrl(value); if (esrc === null) return; el.setAttribute('src', esrc); return; }
          // Code fields are inert in preview (same-origin dashboard iframe):
          // show the raw HTML as escaped text, never execute it. The public
          // render (server-side, preview=false) still emits it raw by design.
          if (t === 'code') { el.textContent = value == null ? '' : value; return; }
          if (t === 'image') {
            var isrc = cmsSafeUrl(value, { dataImage: true });
            if (isrc === null) return;
            value = isrc;
            el.setAttribute('src', value);
            // Mirror _apply_image: clear responsive/lazy attrs so the new src wins.
            if (el.hasAttribute('srcset')) el.removeAttribute('srcset');
            if (el.hasAttribute('data-src')) el.setAttribute('data-src', value);
            if (el.hasAttribute('data-srcset')) el.removeAttribute('data-srcset');
            var pic = el.parentElement;
            if (pic && pic.tagName && pic.tagName.toLowerCase() === 'picture') {
              pic.querySelectorAll('source').forEach(function (s) {
                if (s.hasAttribute('srcset')) s.removeAttribute('srcset');
                if (s.hasAttribute('data-srcset')) s.removeAttribute('data-srcset');
              });
            }
          }
          else if (t === 'video') {
            var vurl = cmsSafeUrl(value);
            if (vurl === null) return;
            if (el.tagName.toLowerCase() === 'video') {
              var vsrc = el.querySelector('source');
              if (vsrc) { vsrc.setAttribute('src', vurl); } else { el.setAttribute('src', vurl); }
              if (el.load) { el.load(); }
            } else { el.setAttribute('src', vurl); }
          }
          else if (t === 'link') { var lurl = cmsSafeUrl(value, { anchor: true }); if (lurl === null) return; el.setAttribute('href', lurl); }
          else if (t === 'color') {
            var prop = (el.tagName.toLowerCase() === 'span') ? 'color' : 'background-color';
            var cval = cmsSafeCssValue(value);
            if (cval) el.style[prop] = cval;
          }
          else if (t === 'richtext') {
            if (el.querySelector && el.querySelector('[data-edit]')) return;
            if (cmsIsBlankHtml(value) && (el.innerHTML || '').replace(/<br\\s*\\/?>/gi,'').trim()) return;
            el.innerHTML = cmsRichtextHTML(el, value);
          }
          // Plain text field: normally textContent, but once it carries a
          // selection-styled span (or any inline markup) render it as HTML.
          else if (/<[a-z]/i.test(value)) { el.innerHTML = cmsRichtextHTML(el, value); }
          else {
            if (el.querySelector && el.querySelector('[data-edit]')) return;
            if (cmsIsBlankHtml(value) && (el.textContent || '').trim()) return;
            el.textContent = value;
          }
        });
      });
    }
    if (data.type === 'header-logo-size') {
      var px = parseInt((data.payload && data.payload.size), 10);
      if (!(px >= 24 && px <= 80)) px = 40;
      document.querySelectorAll('.site-header-logo').forEach(function (img) {
        img.style.height = px + 'px';
        img.style.width = 'auto';
        img.style.maxWidth = 'min(40vw,' + (px * 5) + 'px)';
      });
    }
    if (data.type === 'apply-styles') {
      Object.entries(data.payload || {}).forEach(function (entry) {
        var fid = entry[0];
        var style = entry[1] || {};
        var suffix = '.__block';
        var regionMark = '.__region.';
        if (fid && fid.slice(-suffix.length) === suffix) {
          var bid = fid.slice(0, -suffix.length);
          document.querySelectorAll('[data-instance-id="' + bid + '"]').forEach(function (el) {
            cmsApplyStyle(el, style);
          });
          return;
        }
        if (fid && fid.indexOf(regionMark) !== -1) {
          var rparts = fid.split(regionMark);
          var rid = rparts[0];
          var rname = rparts.slice(1).join(regionMark);
          document.querySelectorAll('[data-instance-id="' + rid + '"]').forEach(function (wrap) {
            var cell = wrap.querySelector(':scope > [data-region="' + rname + '"]');
            if (cell) cmsApplyStyle(cell, style);
          });
          return;
        }
        document.querySelectorAll('[data-edit="' + fid + '"]').forEach(function (el) {
          cmsApplyStyle(el, style);
        });
      });
    }
    if (data.type === 'apply-global') {
      var g = data.payload || {};
      var css = '';
      var bodyDecls = '';
      var gFam = cmsSafeFont(g.fontFamily), gSize = cmsSafeToken(g.baseSize),
          gText = cmsSafeCssValue(g.textColor), gBg = cmsSafeCssValue(g.pageBg),
          gHead = cmsSafeFont(g.headingFamily);
      if (gFam) { bodyDecls += 'font-family:' + gFam + ';'; cmsEnsureFont(gFam); }
      if (gSize) bodyDecls += 'font-size:' + gSize + ';';
      if (gText) bodyDecls += 'color:' + gText + ';';
      if (gBg) bodyDecls += 'background-color:' + gBg + ';';
      if (bodyDecls) css += 'body{' + bodyDecls + '}';
      if (gHead) { css += 'h1,h2,h3,h4,h5,h6{font-family:' + gHead + ';}'; cmsEnsureFont(gHead); }
      var gtag = document.getElementById('cms-global-style');
      if (!gtag) { gtag = document.createElement('style'); gtag.id = 'cms-global-style'; document.head.appendChild(gtag); }
      gtag.textContent = css;
    }
    if (data.type === 'apply-tokens') {
      var tk = data.payload || {};
      var tcss = '';
      Object.keys(tk).forEach(function (n) {
        var sn = String(n).replace(/[^a-zA-Z0-9_-]/g, '');
        var sv = cmsSafeCssValue(tk[n]) || cmsSafeToken(tk[n]);
        if (sn && sv) { tcss += '--' + sn + ':' + sv + ';'; }
      });
      var toktag = document.getElementById('cms-tokens');
      if (!toktag) { toktag = document.createElement('style'); toktag.id = 'cms-tokens'; document.head.appendChild(toktag); }
      toktag.textContent = ':root{' + tcss + '}';
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
    if (data.type === 'style-selection') {
      var sd = data.payload || {};
      var host = sd.clear ? cmsClearSelection() : cmsStyleSelection(sd.prop, sd.value);
      if (host) send('text-update', { id: host.getAttribute('data-edit'), html: host.innerHTML });
    }
    if (data.type === 'link-selection') {
      var ld = data.payload || {};
      var lhost = ld.clear ? cmsUnlinkSelection() : cmsLinkSelection(ld.href);
      if (lhost) send('text-update', { id: lhost.getAttribute('data-edit'), html: lhost.innerHTML });
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
    if (data.type === 'toggle-visibility') {
      // Live show/hide. A bare id (no dot) targets a whole section wrapper;
      // a dotted id (section.field) targets one editable element. In preview
      // 'cms-hidden' only dims (see style below) so the client can still see
      // and un-hide it; on the PUBLIC render it is display:none (server-side).
      var vid = data.payload.id, vhide = !!data.payload.hidden;
      var vsel = vid.indexOf('.') === -1
        ? '[data-section="' + vid + '"]'
        : '[data-edit="' + vid + '"]';
      document.querySelectorAll(vsel).forEach(function (el) {
        el.classList.toggle('cms-hidden', vhide);
      });
    }
    if (data.type === 'frame-block') {
      var fbEl = document.querySelector('[data-instance-id="' + data.payload.id + '"]');
      if (fbEl) { cmsSelectBlock(fbEl); fbEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
    }
    if (data.type === 'restore-scroll') {
      // After a structural reload, put the canvas back where the user was
      // (instant, no smooth-scroll, so it never looks like a jump to top).
      var ry = (data.payload && data.payload.y) || 0;
      window.scrollTo(0, ry);
    }
    if (data.type === 'clear-selection') { cmsClearSelFrame(); }
    if (data.type === 'peek-field') {
      cmsClearPeek();
      var pk = document.querySelectorAll('[data-edit="' + data.payload.id + '"]');
      pk.forEach(function (el) { el.classList.add('cms-peek'); });
      if (pk[0]) pk[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    if (data.type === 'peek-clear') { cmsClearPeek(); }
    if (data.type === 'config') {
      if (data.payload && data.payload.maxDepth != null) CMS_MAX_DEPTH = data.payload.maxDepth;
    }
    if (data.type === 'canvas-drag-clear') { cmsDragClear(); cmsDragMoveId = null; }
    if (data.type === 'canvas-drag-move') {
      cmsDragMoveId = (data.payload && data.payload.id) || null;
    }
    cmsPositionFrame();
  });

  // ---- on-canvas selection frame + block mini-toolbar ------------------
  // A floating frame drawn over the selected block: accent border + ring, a
  // block-name label, and a toolbar (move up/down, duplicate, delete) that
  // mirrors the drawer's actions. pointer-events pass through the frame so
  // inner fields stay clickable; only the toolbar captures clicks.
  var cmsSelEl = null, cmsSelFrame = null;
  function cmsClearPeek() {
    document.querySelectorAll('.cms-peek').forEach(function (el) { el.classList.remove('cms-peek'); });
  }
  function cmsTitleCase(s) {
    return String(s || '').replace(/[-_]+/g, ' ')
      .replace(/\\b\\w/g, function (c) { return c.toUpperCase(); }).trim();
  }
  var CMS_ICO = {
    up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    dup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    del: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6"/></svg>',
    grip: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>'
  };
  function cmsBuildFrame() {
    var f = document.createElement('div');
    f.id = 'cms-sel-frame';
    f.innerHTML =
      '<span class="cms-sel-label"></span>' +
      '<span class="cms-sel-tools">' +
        '<button type="button" class="cms-sel-drag" draggable="true" title="Drag to move" aria-label="Drag to move">' + CMS_ICO.grip + '</button>' +
        '<span class="cms-sel-div" aria-hidden="true"></span>' +
        '<button type="button" data-act="move-up" title="Move up" aria-label="Move up">' + CMS_ICO.up + '</button>' +
        '<button type="button" data-act="move-down" title="Move down" aria-label="Move down">' + CMS_ICO.down + '</button>' +
        '<button type="button" data-act="duplicate" title="Duplicate (Ctrl/Cmd+D)" aria-label="Duplicate">' + CMS_ICO.dup + '</button>' +
        '<button type="button" data-act="delete" title="Delete (Del)" aria-label="Delete">' + CMS_ICO.del + '</button>' +
      '</span>';
    document.body.appendChild(f);
    // Keep any text selection intact when a tool is pressed — but NOT the drag
    // grip, whose native drag must be allowed to start.
    f.querySelector('.cms-sel-tools').addEventListener('mousedown', function (e) {
      if (e.target.closest && e.target.closest('.cms-sel-drag')) return;
      e.preventDefault();
    });
    f.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-act]') : null;
      if (!b || !cmsSelEl) return;
      e.preventDefault(); e.stopPropagation();
      send('block-action', { id: cmsSelEl.getAttribute('data-instance-id'), action: b.getAttribute('data-act') });
    });
    // Drag the grip to reorder this block on the canvas (Stage C).
    var grip = f.querySelector('.cms-sel-drag');
    grip.addEventListener('dragstart', function (e) {
      if (!cmsSelEl) return;
      var id = cmsSelEl.getAttribute('data-instance-id');
      cmsDragMoveId = id;
      e.dataTransfer.effectAllowed = 'move';
      try {
        e.dataTransfer.setData('application/x-cms-moveblock', id);
        e.dataTransfer.setData('text/plain', id);
        if (cmsSelEl.querySelector('[data-region]')) {
          e.dataTransfer.setData('application/x-cms-layout', '1');
        }
      } catch (_) {}
      cmsSelEl.classList.add('cms-dragging');
      if (cmsSelFrame) cmsSelFrame.style.visibility = 'hidden';
    });
    grip.addEventListener('dragend', function () {
      if (cmsSelEl) cmsSelEl.classList.remove('cms-dragging');
      if (cmsSelFrame) cmsSelFrame.style.visibility = '';
      cmsDragMoveId = null;
      cmsDragClear();
    });
    return f;
  }
  function cmsPositionFrame() {
    if (!cmsSelEl || !cmsSelFrame) return;
    if (!document.body.contains(cmsSelEl)) { cmsClearSelFrame(); return; }
    var r = cmsSelEl.getBoundingClientRect();
    var top = r.top + (window.scrollY || window.pageYOffset || 0);
    var left = r.left + (window.scrollX || window.pageXOffset || 0);
    cmsSelFrame.style.transform = 'translate(' + left + 'px,' + top + 'px)';
    cmsSelFrame.style.width = r.width + 'px';
    cmsSelFrame.style.height = r.height + 'px';
    var inHeader = !!(cmsSelEl.closest && cmsSelEl.closest(
      'header, [data-region^="header"], .site-nav, .site-header-actions'
    ));
    var compact = inHeader || r.height < 48 || r.width < 88;
    cmsSelFrame.classList.toggle('cms-sel-frame--compact', compact);
    // Never cover the words. Short header items get the toolbar UNDER the
    // link. Inset is only for tall blocks clipped by the top of the viewport.
    cmsSelFrame.classList.toggle('cms-sel-frame--below', compact);
    cmsSelFrame.classList.toggle('cms-sel-frame--inset', !compact && r.top < 44);
  }
  function cmsSelectBlock(el) {
    if (!el) return;
    cmsSelEl = el;
    if (!cmsSelFrame) cmsSelFrame = cmsBuildFrame();
    var label = el.getAttribute('data-cms-label') ||
      cmsTitleCase(el.getAttribute('data-block-type') || 'Block');
    cmsSelFrame.querySelector('.cms-sel-label').textContent = label;
    cmsSelFrame.classList.add('is-active');
    cmsPositionFrame();
  }
  function cmsClearSelFrame() {
    cmsSelEl = null;
    if (cmsSelFrame) cmsSelFrame.classList.remove('is-active');
  }
  window.addEventListener('scroll', cmsPositionFrame, true);
  window.addEventListener('resize', cmsPositionFrame);
  // Reflows (font loads, image loads, richtext edits) can move the element
  // without a scroll/resize event; keep the frame glued.
  setInterval(function () { if (cmsSelEl) cmsPositionFrame(); }, 250);

  // ---- canvas drag & drop (Stage C) ------------------------------------
  // Same-origin preview, so native drag events fire in here. We draw a 2px
  // accent insertion line + region outline (valid) or muted (invalid), then
  // hand the resolved {dest, beforeId} back to the editor to mutate content.
  var CMS_MAX_DEPTH = 99;        // overwritten by the editor's 'config' message
  var cmsDragMoveId = null;      // id of a block being reordered on the canvas
  var cmsDropCtx = null;         // last resolved drop target
  var cmsDropLine = null;
  function cmsEnsureDropLine() {
    if (!cmsDropLine) { cmsDropLine = document.createElement('div'); cmsDropLine.id = 'cms-drop-line'; document.body.appendChild(cmsDropLine); }
    return cmsDropLine;
  }
  function cmsDragClear() {
    if (cmsDropLine) cmsDropLine.style.display = 'none';
    document.querySelectorAll('.cms-drop-region, .cms-drop-region--invalid').forEach(function (el) {
      el.classList.remove('cms-drop-region', 'cms-drop-region--invalid');
    });
  }
  function cmsDragKind(dt) {
    if (!dt || !dt.types) return null;
    var t = Array.prototype.slice.call(dt.types);
    if (t.indexOf('application/x-cms-newblock') !== -1) return 'new';
    if (t.indexOf('application/x-cms-moveblock') !== -1) return 'move';
    return null;
  }
  // Column depth of a region: 0 for the shell's "main", +1 per nested column.
  function cmsColDepth(region) {
    var d = 0, el = region;
    while (el) {
      var name = el.getAttribute && el.getAttribute('data-region');
      if (name != null && name !== 'main') d++;
      el = el.parentElement ? el.parentElement.closest('[data-region]') : null;
    }
    return d;
  }
  function cmsOwnSlots(inst) {
    if (!inst || !inst.querySelectorAll) return [];
    var slots = [];
    inst.querySelectorAll('[data-region]').forEach(function (slot) {
      if (slot.closest('[data-instance-id]') === inst) slots.push(slot);
    });
    return slots;
  }
  function cmsNearestSlot(slots, x, y) {
    if (!slots.length) return null;
    var best = slots[0], bestD = Infinity;
    slots.forEach(function (slot) {
      var r = slot.getBoundingClientRect();
      var cx = Math.max(r.left, Math.min(x, r.right));
      var cy = Math.max(r.top, Math.min(y, r.bottom));
      var d = (x - cx) * (x - cx) + (y - cy) * (y - cy);
      if (d < bestD) { bestD = d; best = slot; }
    });
    return best;
  }
  function cmsShellSlots(root) {
    var slots = [];
    if (!root || !root.querySelectorAll) return slots;
    root.querySelectorAll('[data-region]').forEach(function (slot) {
      if (!slot.parentElement || !slot.parentElement.closest('[data-instance-id]')) slots.push(slot);
    });
    return slots;
  }
  function cmsRegionAt(x, y) {
    var pt = document.elementFromPoint(x, y);
    if (!pt) return null;
    var region = pt.closest ? pt.closest('[data-region]') : null;
    var inst = pt.closest ? pt.closest('[data-instance-id]') : null;
    var slots = cmsOwnSlots(inst);
    if (slots.length) {
      if (slots.indexOf(region) !== -1) return region;
      return cmsNearestSlot(slots, x, y);
    }
    if (region) return region;
    var chrome = pt.closest ? pt.closest('header, footer, [data-section="header"], [data-section="footer"], [data-section="nav"]') : null;
    if (chrome) return cmsNearestSlot(cmsShellSlots(chrome), x, y);
    return null;
  }
  function cmsDropDest(region) {
    var rname = (region.getAttribute('data-region') || 'main').trim() || 'main';
    var owner = region.parentElement && region.parentElement.closest
      ? region.parentElement.closest('[data-instance-id]') : null;
    return owner ? (owner.getAttribute('data-instance-id') + '/' + rname) : rname;
  }
  function cmsResolveDrop(x, y, kind, dt) {
    var region = cmsRegionAt(x, y);
    if (!region) return null;
    var invalid = false;
    if (kind === 'move' && cmsDragMoveId) {
      var mv = document.querySelector('[data-instance-id="' + cmsDragMoveId + '"]');
      if (mv && (mv === region || mv.contains(region))) invalid = true;
    }
    var isLayout = dt && dt.types &&
      Array.prototype.indexOf.call(dt.types, 'application/x-cms-layout') !== -1;
    if (isLayout && cmsColDepth(region) >= CMS_MAX_DEPTH) invalid = true;
    var kids = Array.prototype.slice.call(region.querySelectorAll(':scope > [data-instance-id]'));
    if (kind === 'move' && cmsDragMoveId) {
      kids = kids.filter(function (k) { return k.getAttribute('data-instance-id') !== cmsDragMoveId; });
    }
    var rrect = region.getBoundingClientRect();
    var rname = (region.getAttribute('data-region') || 'main').trim() || 'main';
    var rowLike = /^(header|footer)/.test(rname);
    var beforeId = null, lineY = null;
    for (var i = 0; i < kids.length; i++) {
      var kr = kids[i].getBoundingClientRect();
      var before = rowLike ? (x < kr.left + kr.width / 2) : (y < kr.top + kr.height / 2);
      if (before) { beforeId = kids[i].getAttribute('data-instance-id'); lineY = kr.top; break; }
    }
    if (lineY === null) {
      if (kids.length) { lineY = kids[kids.length - 1].getBoundingClientRect().bottom; }
      else { lineY = rrect.top + 6; }
    }
    var dest = cmsDropDest(region);
    return { region: region, rect: rrect, lineY: lineY, beforeId: beforeId, dest: dest, invalid: invalid };
  }
  document.addEventListener('dragover', function (e) {
    var kind = cmsDragKind(e.dataTransfer);
    if (!kind) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = kind === 'new' ? 'copy' : 'move';
    var ctx = cmsResolveDrop(e.clientX, e.clientY, kind, e.dataTransfer);
    cmsDragClear();
    if (!ctx) { cmsDropCtx = null; return; }
    cmsDropCtx = ctx;
    ctx.region.classList.add(ctx.invalid ? 'cms-drop-region--invalid' : 'cms-drop-region');
    var line = cmsEnsureDropLine();
    line.className = ctx.invalid ? 'cms-invalid' : '';
    line.style.display = 'block';
    var top = ctx.lineY + (window.scrollY || window.pageYOffset || 0);
    var left = ctx.rect.left + (window.scrollX || window.pageXOffset || 0);
    line.style.transform = 'translate(' + left + 'px,' + top + 'px)';
    line.style.width = ctx.rect.width + 'px';
  });
  document.addEventListener('dragleave', function (e) {
    // Only clear when the pointer actually leaves the document.
    if (e.relatedTarget === null && (e.clientX <= 0 || e.clientY <= 0)) cmsDragClear();
  });
  document.addEventListener('drop', function (e) {
    var kind = cmsDragKind(e.dataTransfer);
    if (!kind) return;
    e.preventDefault();
    var ctx = cmsDropCtx; cmsDropCtx = null; cmsDragClear();
    if (!ctx || ctx.invalid) { cmsDragMoveId = null; return; }
    if (kind === 'new') {
      var key = '';
      try { key = e.dataTransfer.getData('application/x-cms-newblock') || e.dataTransfer.getData('text/plain'); } catch (_) {}
      if (key) send('canvas-add', { key: key, dest: ctx.dest, beforeId: ctx.beforeId });
    } else {
      var id = cmsDragMoveId;
      try { id = e.dataTransfer.getData('application/x-cms-moveblock') || id; } catch (_) {}
      if (id) send('canvas-move', { id: id, dest: ctx.dest, beforeId: ctx.beforeId });
    }
    cmsDragMoveId = null;
  });

  // Click on empty canvas (not a block/field/adder/frame) deselects.
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t && t.closest && (t.closest('[data-instance-id]') || t.closest('[data-edit]') ||
        t.closest('#cms-sel-frame') || t.closest('[data-cms-add-here]'))) return;
    if (cmsSelEl) { cmsClearSelFrame(); send('deselect', {}); }
  });

  send('ready', {});
})();
</script>
<style>
  .cms-bg-fx { position: absolute; inset: 0; z-index: 0; pointer-events: none; background-repeat: no-repeat; }
  .cms-bg-fx-host { position: relative; }
  .cms-bg-fx-host.cms-bg-fx-clip { overflow: hidden; }
  .cms-bg-fx-host > *:not(.cms-bg-fx) { position: relative; z-index: 1; }
  [data-header-name=off], .site-brand.hide-name,
  .site-header-brand.hide-name .site-brand,
  .site-header-brand.hide-name [data-edit$=".brand"] { display: none !important; }
  .cms-editable { outline: 1px dashed transparent; outline-offset: 4px;
                  transition: outline-color 0.15s ease, background 0.15s ease; cursor: pointer; }
  .cms-editable:hover { outline-color: #2457d6; background: rgba(36, 87, 214, 0.06); }
  .cms-image-editable { cursor: pointer; }
  .cms-image-editable:hover { outline: 2px solid #2457d6; outline-offset: 3px; }
  .cms-inline-editable:hover { cursor: text; }
  .cms-inline-editing, .cms-inline-editing:hover {
    outline: 2px solid #2457d6 !important; outline-offset: 3px;
    background: #fff !important; cursor: text;
    box-shadow: 0 0 0 6px rgba(36, 87, 214, 0.12);
  }
  .cms-highlight { outline: 2px solid #2457d6 !important;
                   box-shadow: 0 0 0 6px rgba(36, 87, 214, 0.15); }
  /* peek: a light, non-committal outline shown while hovering a field row */
  .cms-peek { outline: 2px dashed #2457d6 !important; outline-offset: 3px;
              background: rgba(36, 87, 214, 0.05); }
  .cms-section-flash { animation: cms-section-flash 1.2s ease; }
  @keyframes cms-section-flash {
    0%   { outline: 2px solid rgba(36, 87, 214, 0); outline-offset: -2px; }
    25%  { outline: 2px solid rgba(36, 87, 214, 0.85); outline-offset: -2px; }
    100% { outline: 2px solid rgba(36, 87, 214, 0); outline-offset: -2px; }
  }
  /* ---- on-canvas selection frame + block mini-toolbar ---- */
  #cms-sel-frame {
    position: absolute; top: 0; left: 0; z-index: 2147483000;
    pointer-events: none; display: none; box-sizing: border-box;
    border: 2px solid #2457d6; border-radius: 3px;
    box-shadow: 0 0 0 4px rgba(36, 87, 214, 0.14);
  }
  #cms-sel-frame.is-active { display: block; }
  #cms-sel-frame .cms-sel-label {
    position: absolute; left: -2px; bottom: 100%; margin-bottom: 5px;
    pointer-events: none; background: #2457d6; color: #fff;
    font: 600 11px/1 "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: 0.01em; padding: 4px 7px; border-radius: 5px; white-space: nowrap;
  }
  #cms-sel-frame .cms-sel-tools {
    position: absolute; right: -2px; bottom: 100%; margin-bottom: 5px;
    display: inline-flex; gap: 2px; pointer-events: auto;
    background: #101828; border-radius: 8px; padding: 3px;
    box-shadow: 0 8px 22px rgba(16, 24, 40, 0.26);
  }
  #cms-sel-frame.cms-sel-frame--inset .cms-sel-label,
  #cms-sel-frame.cms-sel-frame--inset .cms-sel-tools {
    bottom: auto; top: 5px; margin: 0;
  }
  #cms-sel-frame.cms-sel-frame--below .cms-sel-label,
  #cms-sel-frame.cms-sel-frame--below .cms-sel-tools {
    bottom: auto; top: 100%; margin: 6px 0 0;
  }
  #cms-sel-frame.cms-sel-frame--compact .cms-sel-label { display: none; }
  #cms-sel-frame .cms-sel-tools button {
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; padding: 0; border: 0; border-radius: 6px;
    background: transparent; color: #fff; cursor: pointer;
    transition: background 120ms ease;
  }
  #cms-sel-frame .cms-sel-tools button:hover { background: rgba(255, 255, 255, 0.16); }
  #cms-sel-frame .cms-sel-tools button:active { background: rgba(255, 255, 255, 0.24); }
  #cms-sel-frame .cms-sel-tools button:focus-visible {
    outline: 2px solid #7aa2ff; outline-offset: 1px;
  }
  #cms-sel-frame .cms-sel-tools button[data-act="delete"]:hover { background: #b42318; }
  #cms-sel-frame .cms-sel-tools svg { width: 15px; height: 15px; display: block; }
  #cms-sel-frame .cms-sel-drag {
    cursor: grab;
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; padding: 0; border: 0; border-radius: 6px;
    background: transparent; color: #fff;
  }
  #cms-sel-frame .cms-sel-drag:hover { background: rgba(255, 255, 255, 0.16); }
  #cms-sel-frame .cms-sel-drag:active { cursor: grabbing; }
  #cms-sel-frame .cms-sel-div { width: 1px; height: 16px; background: rgba(255, 255, 255, 0.2); align-self: center; margin: 0 2px; }
  /* ---- canvas drag & drop ---- */
  .cms-dragging { opacity: 0.55 !important; }
  #cms-drop-line {
    position: absolute; top: 0; left: 0; z-index: 2147483001;
    height: 0; border-top: 2px solid #2457d6; pointer-events: none; display: none;
    box-shadow: 0 0 0 1px rgba(36, 87, 214, 0.25);
  }
  #cms-drop-line::before {
    content: ""; position: absolute; left: -4px; top: -5px;
    width: 8px; height: 8px; border-radius: 50%; background: #2457d6;
  }
  #cms-drop-line.cms-invalid { border-top-color: #98a2b3; box-shadow: none; }
  #cms-drop-line.cms-invalid::before { background: #98a2b3; }
  .cms-chrome-drag {
    position: absolute; top: 4px; left: 4px; z-index: 4;
    display: inline-flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; padding: 0; border: 0; border-radius: 6px;
    background: #101828; color: #fff; cursor: grab;
    box-shadow: 0 2px 8px rgba(16, 24, 40, 0.22);
  }
  .cms-chrome-drag:active { cursor: grabbing; }
  .cms-chrome-drag svg { width: 13px; height: 13px; display: block; }
  .cms-chrome-drag-host { position: relative; }
  .site-header .cms-chrome-drag { opacity: 0; }
  .site-header [data-instance-id]:hover > .cms-chrome-drag,
  .site-header [data-instance-id]:focus-within > .cms-chrome-drag { opacity: 1; }
  .cms-chrome-add {
    display: inline-flex; align-items: center; gap: 4px;
    border: 0; background: transparent; color: #667085;
    font: 600 13px/1.2 system-ui, sans-serif;
    padding: 4px 8px; border-radius: 6px; cursor: pointer;
    white-space: nowrap;
  }
  .cms-chrome-add:hover { background: rgba(36, 87, 214, 0.08); color: #2457d6; }
  .cms-drop-region { outline: 2px dashed #2457d6 !important; outline-offset: -2px; background: rgba(36, 87, 214, 0.05); }
  .cms-drop-region--invalid { outline: 2px dashed #98a2b3 !important; outline-offset: -2px; background: rgba(152, 162, 179, 0.09); }
  /* Preview-only: hidden items are dimmed + marked, NOT removed, so the client
     can still see and toggle them. The public site uses display:none instead. */
  .cms-hidden { opacity: 0.4 !important; outline: 2px dashed #f59e0b !important;
                outline-offset: 2px; }
  [data-cms-ghl-preview-slot] {
    position: relative !important;
    isolation: isolate;
    min-height: 500px;
  }
  [data-cms-ghl-preview-note] {
    position: absolute !important;
    inset: 0 !important;
    z-index: 2;
    display: flex !important;
    align-items: flex-start;
    justify-content: center;
    box-sizing: border-box;
    padding: 16px;
    pointer-events: auto;
    overflow-wrap: anywhere;
    background: rgba(16, 24, 40, 0.78);
    color: #ffffff !important;
    font-family: Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
    font-weight: 600;
    line-height: 1.5;
    text-align: center;
  }
</style>
"""

GHL_FORM_EMBED_SCRIPT = "https://link.msgsndr.com/js/form_embed.js"
GHL_FORM_EMBED_BASE = "https://msgsndr.com/widget/form/"


# Elements that may NOT legally contain block-level children (phrasing-content
# hosts). Rich-text bound to one of these must be flattened: a contenteditable
# that auto-wraps a typed line in <p> yields
#   <p data-edit="..."><p>text</p></p>
# and the browser then splits that into an *empty* editable host plus a second,
# un-editable <p> holding the text, creating a visible duplicate that can't be clicked.
_PHRASING_HOSTS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "span", "a", "cite", "em",
    "strong", "b", "i", "u", "small", "label", "summary", "figcaption",
    "dt", "caption", "legend",
}
_BLOCK_CHILD_TAGS = {
    "p", "div", "section", "article", "header", "footer", "aside", "main",
    "ul", "ol", "li", "blockquote", "pre", "table", "figure", "address",
}


def _flatten_for_phrasing_host(fragment) -> None:
    """In place: lift inline content out of top-level block children so a
    phrasing host (<p>, <h2>, ...) never ends up wrapping a block element.
    Multiple blocks are separated with <br/> so line breaks survive."""
    for _ in range(4):  # cap depth; real-world cases are a single <p> wrapper
        blocks = [
            c for c in fragment.find_all(recursive=False)
            if getattr(c, "name", None) in _BLOCK_CHILD_TAGS
        ]
        if not blocks:
            return
        for i, block in enumerate(blocks):
            if i > 0:
                block.insert_before(BeautifulSoup("<br/>", "lxml").br)
            block.unwrap()


def _apply_image(el, value: str) -> None:
    """Replace a content image. Naive `src=` fails on real-world markup because
    responsive `srcset` candidates win, lazy-load libraries copy `data-src`
    over `src` after mount, and `<picture><source srcset>` siblings outrank
    the fallback `<img>`. Reconcile all of those to the new value so the
    swap is visible regardless of the surrounding markup."""
    el["src"] = value
    if "srcset" in el.attrs:
        del el["srcset"]
    if "data-src" in el.attrs:
        el["data-src"] = value
    if "data-srcset" in el.attrs:
        del el["data-srcset"]
    # lxml treats <source> as a non-void wrapper, so the <img>'s *direct*
    # parent is often the innermost <source>, not <picture>. Walk ancestors
    # to be robust regardless of parser quirks.
    picture = el.find_parent("picture")
    if picture is not None:
        for source in picture.find_all("source"):
            if "srcset" in source.attrs:
                del source["srcset"]
            if "data-srcset" in source.attrs:
                del source["data-srcset"]


def _insert_sanitized_html(el, html: str) -> None:
    """Replace ``el``'s contents with sanitized inline HTML (used by richtext
    fields and by plain text fields once they carry a selection-styled span)."""
    el.clear()
    cleaned = sanitize_template_html(html)
    fragment = BeautifulSoup(cleaned, "lxml").body
    if fragment:
        if el.name in _PHRASING_HOSTS:
            _flatten_for_phrasing_host(fragment)
        for child in list(fragment.children):
            el.append(child)
    else:
        el.append(cleaned)


_SAFE_CSS_PROP_RE = re.compile(r"^[a-zA-Z-]{1,40}$")


def _apply_select(el, value) -> None:
    """Apply a ``select`` field's chosen value to its element.

    The element declares how to apply it via ``data-apply``:
      * ``class``            — toggle the value into the class list (removing the
                               other option values so presets never stack).
      * ``style:<prop>``     — set an inline CSS declaration.

    Only values present in the element's own ``data-options`` are honoured, so a
    tampered content value cannot inject an arbitrary class or CSS declaration.
    """
    value = "" if value is None else str(value)
    allowed = {o["value"] for o in parse_select_options(el)}
    apply = (el.get("data-apply") or "class").strip()

    if apply.startswith("style:"):
        prop = apply.split(":", 1)[1].strip()
        if not _SAFE_CSS_PROP_RE.match(prop):
            return
        existing = re.sub(rf"{re.escape(prop)}\s*:[^;]*;?", "", el.get("style", "")).strip()
        if value in allowed and value:
            el["style"] = (existing + f" {prop}: {value};").strip()
        elif existing:
            el["style"] = existing
        else:
            del el["style"]
        return

    # default: class toggle
    classes = [c for c in (el.get("class") or []) if c not in allowed]
    if value in allowed and value:
        classes.append(value)
    if classes:
        el["class"] = classes
    elif el.has_attr("class"):
        del el["class"]


def _apply_field(el, value: str, ftype: str, *, preview: bool = False) -> None:
    # No-op short-circuit. Skip the write when the value already equals what's
    # in the element, typically every render where the tenant hasn't actually
    # edited that field (merge_with_defaults pre-fills every field with its
    # default, extracted from this same element). The richtext path falls back
    # to ``sanitize_template_html`` on a real edit, which preserves classes,
    # structural tags, and design-bearing attributes (unlike the blog-body
    # ``sanitize_html``, which is built for untrusted contenteditable input
    # and would strip the agency's design on every render).
    if ftype == "image":
        safe = _safe_url_value(value, allow_data_image=True)
        if safe is None:
            return
        if el.get("src", "") == safe:
            return
        _apply_image(el, safe)
        return
    if ftype == "video":
        safe = _safe_url_value(value)
        if safe is None:
            return
        source = el.find("source") if el.name == "video" else None
        current_src = source.get("src", "") if source is not None else el.get("src", "")
        if current_src == safe:
            return
        if source is not None:
            source["src"] = safe
        else:
            el["src"] = safe
        return
    if ftype == "link":
        safe = _safe_url_value(value, allow_anchor=True)
        if safe is None:
            return
        if el.get("href", "") == safe:
            return
        el["href"] = safe
        return
    if ftype == "color":
        safe = _safe_css_value(value)
        if not safe:
            return
        prop = "color" if el.name == "span" else "background-color"
        existing = el.get("style", "")
        cleaned = re.sub(rf"{prop}\s*:[^;]*;?", "", existing).strip()
        el["style"] = (cleaned + f" {prop}: {safe};").strip()
        return
    if ftype == "select":
        _apply_select(el, value)
        return
    if ftype == "embed":
        safe = _safe_url_value(value)
        if safe is None:
            return
        if el.get("src", "") == safe:
            return
        el["src"] = safe
        return
    if ftype == "code":
        # Client-controlled raw HTML. On the PUBLIC render it is intentionally
        # unsanitized (opt-in raw HTML on the client's own site). In PREVIEW the
        # iframe is same-origin with the dashboard, so executing it would let a
        # client's markup run in an agency operator's authenticated session —
        # render it as inert, escaped text instead.
        el.clear()
        if value:
            if preview:
                el.append(value)  # NavigableString -> escaped on output, inert
            else:
                el.append(BeautifulSoup(value, "html.parser"))
        return
    if ftype == "richtext":
        # A wrapper that already has child fields (blockquote > p + cite)
        # must not be rewritten. Replacing its inner HTML deletes those
        # fields and flattens the designed quote.
        if el.find(attrs={"data-edit": True}) is not None:
            return
        # First pass: byte-for-byte equality. Most no-edit renders hit
        # this and we're done, which saves a re-parse.
        current = (el.decode_contents() or "").strip()
        value_stripped = (value or "").strip()
        if current == value_stripped:
            return
        # Second pass: normalize both sides through the same parser so
        # cosmetic round-trip drift (attribute order, entity encoding,
        # whitespace inside tags) doesn't push us into the destructive
        # path on a render that *should* be a no-op. The parser pulls
        # defaults via decode_contents(); BS4 + lxml are not idempotent
        # on every input, so the stored default and the renderer's
        # second pass can disagree byte-for-byte while representing the
        # same fragment. Canonicalize both, then compare.
        if canonicalize_fragment(current) == canonicalize_fragment(value_stripped):
            return
        # Real edit. Use the template-aware sanitizer (preserves classes,
        # styles, structural tags) rather than the blog-body sanitizer.
        # See ``core/services/template_sanitizer.py`` for the trust model.
        _insert_sanitized_html(el, value_stripped)
        return
    # text type
    # Stripped on both sides so this agrees with parser._extract_default. When
    # they disagreed, an unedited field looked edited and fell through to the
    # write below.
    if el.get_text().strip() == (value or "").strip():
        return
    # A plain text field that now carries inline markup (a selection-styled
    # span) renders as HTML, like richtext; otherwise it's literal text.
    if re.search(r"<[a-zA-Z]", value or ""):
        _insert_sanitized_html(el, value.strip())
        return
    el.string = value


def _render_ghl_form_slot(soup, el, value: object, *, preview: bool) -> bool:
    """Render one allowlisted GHL form slot; return whether script is needed."""
    try:
        parsed = parse_ghl_embed_value(value, expected_kind="form")
    except ValueError:
        parsed = None

    if parsed is None:
        if not preview:
            el.decompose()
            return False
        el.clear()
        placeholder = soup.new_tag("div")
        placeholder["data-cms-ghl-empty"] = ""
        placeholder.string = "No GHL form selected"
        el.append(placeholder)
        return False

    _kind, form_id = parsed
    label = (el.get("data-label") or "GHL form").strip()
    el.clear()

    iframe = soup.new_tag("iframe")
    iframe["src"] = f"{GHL_FORM_EMBED_BASE}{form_id}"
    iframe["id"] = f"inline-{form_id}"
    iframe["data-ghl-form-id"] = form_id
    iframe["title"] = label
    iframe["style"] = (
        "width: 100%; height: 100%; min-height: 500px; border: none;"
    )
    iframe["loading"] = "lazy"

    if preview:
        el["data-cms-ghl-preview-slot"] = ""
        iframe["sandbox"] = "allow-scripts"
        iframe["aria-hidden"] = "true"
        iframe["tabindex"] = "-1"
        iframe["inert"] = ""
        iframe["style"] += " pointer-events: none;"
        note = soup.new_tag("div")
        note["data-cms-ghl-preview-note"] = ""
        note["role"] = "note"
        note.string = "This is a preview, nothing is sent."
        el.append(note)

    el.append(iframe)
    return True


def _inject_ghl_form_script(soup) -> None:
    if soup.find("script", src=GHL_FORM_EMBED_SCRIPT):
        return
    script = soup.new_tag("script", src=GHL_FORM_EMBED_SCRIPT)
    (soup.find("body") or soup).append(script)


def _apply_brand_tokens(soup: BeautifulSoup, brand_content: dict[str, str]) -> None:
    style = soup.find("style", attrs={"data-tokens": True})
    if not style or not brand_content:
        return

    css = style.string or ""

    def replace(match):
        var_name = match.group(1)
        if var_name in brand_content:
            # Brand tokens land inside a stylesheet rule, so an unvalidated value
            # ("red; } body { ...") would break out and inject arbitrary CSS.
            # Accept plain colors (the usual case) or bare lengths/keywords;
            # keep the template's default for anything with braces/semicolons.
            raw = str(brand_content[var_name] or "").strip()
            if raw and (_safe_css_value(raw) or _SAFE_STYLE_TOKEN_RE.match(raw)):
                return f"--{var_name}: {raw};"
        return match.group(0)

    style.string = re.sub(r"--([a-zA-Z0-9_-]+)\s*:\s*[^;]+;", replace, css)


# Per-element editable styles. Keys are the client-facing style names stored in
# content["_styles"][<data-edit id>]; values map to CSS declarations. `italic`
# is handled separately (boolean -> font-style: italic).
_STYLE_PROPERTIES = {
    "color": "color",
    "bgColor": "background-color",
    "fontSize": "font-size",
    "fontFamily": "font-family",
    "fontWeight": "font-weight",
    "align": "text-align",
    "lineHeight": "line-height",
    "letterSpacing": "letter-spacing",
    "textTransform": "text-transform",
    "padding": "padding",
    "margin": "margin",
    "width": "width",
    "maxWidth": "max-width",
    "minHeight": "min-height",
    "borderRadius": "border-radius",
}

# Keys whose values are simple lengths/numbers/keywords (no colors, no fonts).
# Validated against a conservative charset so a crafted value can't smuggle a
# second declaration into the inline style attribute.
_SIMPLE_STYLE_KEYS = {
    "lineHeight", "letterSpacing", "textTransform",
    "padding", "margin", "width", "maxWidth", "minHeight", "borderRadius",
    "bgSize", "bgPosition", "bgMode", "bgOverlay", "bgOpacity", "bgBlur",
}
_SAFE_STYLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9.%\-\s]+$")
_SAFE_GRADIENT_RE = re.compile(
    r"^(?P<angle>[0-9]{1,3})deg\s*,\s*"
    r"(?P<from>#[0-9A-Fa-f]{3,8})\s*,\s*"
    r"(?P<to>#[0-9A-Fa-f]{3,8})$"
)
_BLOCK_STYLE_SUFFIX = ".__block"
_REGION_STYLE_MARK = ".__region."


def _set_css_prop(el, prop: str, value: str) -> None:
    """Set one CSS declaration on an element's inline style, replacing any
    existing declaration of the same property (mirrors the `color` field type
    in _apply_field so re-renders don't stack duplicates)."""
    existing = el.get("style", "")
    cleaned = re.sub(rf"{re.escape(prop)}\s*:[^;]*;?", "", existing).strip()
    el["style"] = (cleaned + f" {prop}: {value};").strip()


def _safe_gradient_value(value: str) -> str | None:
    """Accept ``180deg,#111111,#ffffff`` only — no freeform CSS gradients."""
    raw = str(value or "").strip()
    match = _SAFE_GRADIENT_RE.match(raw)
    if not match:
        return None
    angle = int(match.group("angle"))
    if angle > 360:
        return None
    return f"{angle}deg,{match.group('from')},{match.group('to')}"


def _safe_overlay(value) -> int | None:
    try:
        amount = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return amount if 0 < amount <= 80 else None


def _safe_opacity(value) -> int | None:
    """Background-image opacity 1–100. 100 means fully opaque."""
    try:
        amount = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return amount if 1 <= amount <= 100 else None


def _safe_blur(value) -> int | None:
    """Background blur radius in px, 1–20."""
    try:
        amount = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return amount if 0 < amount <= 20 else None


def _find_bg_fx(el):
    for kid in el.find_all("div", recursive=False):
        classes = kid.get("class") or []
        if "cms-bg-fx" in classes:
            return kid
    return None


def _remove_bg_fx(el) -> None:
    fx = _find_bg_fx(el)
    if fx is not None:
        fx.decompose()
    classes = [c for c in (el.get("class") or []) if c not in ("cms-bg-fx-host", "cms-bg-fx-clip")]
    if classes:
        el["class"] = classes
    elif el.has_attr("class"):
        del el["class"]


def _clear_css_prop(el, prop: str) -> None:
    existing = el.get("style", "")
    cleaned = re.sub(rf"{re.escape(prop)}\s*:[^;]*;?", "", existing).strip().strip(";")
    if cleaned:
        el["style"] = cleaned
    elif el.has_attr("style"):
        del el["style"]


def _ensure_bg_fx_css(el) -> None:
    root = el
    while getattr(root, "parent", None) is not None:
        root = root.parent
    if not hasattr(root, "find"):
        return
    if root.find("style", attrs={"data-cms-bg-fx": True}):
        return
    tag = BeautifulSoup(
        '<style data-cms-bg-fx="1">'
        ".cms-bg-fx{position:absolute;inset:0;z-index:0;pointer-events:none;"
        "background-repeat:no-repeat}"
        ".cms-bg-fx-host{position:relative}"
        ".cms-bg-fx-host.cms-bg-fx-clip{overflow:hidden}"
        ".cms-bg-fx-host>:not(.cms-bg-fx){position:relative;z-index:1}"
        "</style>",
        "lxml",
    ).find("style")
    head = root.find("head") if hasattr(root, "find") else None
    if head is not None:
        head.append(tag)
    else:
        root.insert(0, tag)


_BG_BOX_TAGS = frozenset({
    "div", "section", "article", "header", "footer", "aside", "main", "li",
})


def _grow_bg_image_box(el, style: dict) -> None:
    """A background image on a text line collapses to one line of height.
    GHL-style columns need a real cell: cover the box and give it room."""
    if el.name not in _BG_BOX_TAGS:
        return
    if not style.get("minHeight"):
        _set_css_prop(el, "min-height", "220px")
    if not style.get("padding"):
        _set_css_prop(el, "padding", "32px 20px")
        _set_css_prop(el, "box-sizing", "border-box")
    _set_css_prop(el, "width", "100%")


def _css_bg_url(value: str) -> str | None:
    safe = _safe_url_value(value, allow_data_image=True)
    if not safe:
        return None
    escaped = (
        safe.replace("\\", "%5C")
        .replace('"', "%22")
        .replace("'", "%27")
        .replace("(", "%28")
        .replace(")", "%29")
    )
    return f'url("{escaped}")'


def _apply_background(el, style: dict) -> None:
    mode = str(style.get("bgMode") or "").strip().lower()
    if mode not in ("color", "image", "gradient"):
        if style.get("bgImage"):
            mode = "image"
        elif style.get("bgGradient"):
            mode = "gradient"
        else:
            mode = "color"
    layers: list[str] = []
    overlay = _safe_overlay(style.get("bgOverlay"))
    if overlay and mode == "image":
        alpha = overlay / 100
        layers.append(f"linear-gradient(rgba(0,0,0,{alpha:.2f}), rgba(0,0,0,{alpha:.2f}))")
    if mode == "gradient":
        gradient = _safe_gradient_value(style.get("bgGradient") or "")
        if gradient:
            angle, start, end = gradient.split(",", 2)
            layers.append(f"linear-gradient({angle}, {start}, {end})")
    if mode == "image":
        url = _css_bg_url(style.get("bgImage") or "")
        if url:
            layers.append(url)
    if not layers:
        _remove_bg_fx(el)
        return

    opacity = _safe_opacity(style.get("bgOpacity"))
    if opacity is None:
        opacity = 100
    blur = _safe_blur(style.get("bgBlur")) or 0
    use_fx = mode == "image" and (opacity < 100 or blur > 0)
    size = str(style.get("bgSize") or "cover")
    position = str(style.get("bgPosition") or "center")

    if not use_fx:
        _remove_bg_fx(el)
        _set_css_prop(el, "background-image", ", ".join(layers))
        if mode == "image":
            if _SAFE_STYLE_TOKEN_RE.match(size):
                _set_css_prop(el, "background-size", size)
            if _SAFE_STYLE_TOKEN_RE.match(position):
                _set_css_prop(el, "background-position", position)
            _set_css_prop(el, "background-repeat", "no-repeat")
            _grow_bg_image_box(el, style)
        return

    _ensure_bg_fx_css(el)
    fx = _find_bg_fx(el)
    if fx is None:
        fx = BeautifulSoup(
            '<div class="cms-bg-fx" aria-hidden="true"></div>', "lxml"
        ).div
        el.insert(0, fx)

    classes = list(el.get("class") or [])
    if "cms-bg-fx-host" not in classes:
        classes.append("cms-bg-fx-host")
    if blur:
        if "cms-bg-fx-clip" not in classes:
            classes.append("cms-bg-fx-clip")
    else:
        classes = [c for c in classes if c != "cms-bg-fx-clip"]
    el["class"] = classes

    for prop in (
        "background-image",
        "background-size",
        "background-position",
        "background-repeat",
    ):
        _clear_css_prop(el, prop)

    _set_css_prop(fx, "background-image", ", ".join(layers))
    if _SAFE_STYLE_TOKEN_RE.match(size):
        _set_css_prop(fx, "background-size", size)
    if _SAFE_STYLE_TOKEN_RE.match(position):
        _set_css_prop(fx, "background-position", position)
    _set_css_prop(fx, "background-repeat", "no-repeat")
    _set_css_prop(fx, "opacity", f"{opacity / 100:.2f}")
    if blur:
        _set_css_prop(fx, "filter", f"blur({blur}px)")
    else:
        _clear_css_prop(fx, "filter")
    _grow_bg_image_box(el, style)


def _apply_element_styles(el, style: dict) -> None:
    if not isinstance(style, dict):
        return
    for key, css_prop in _STYLE_PROPERTIES.items():
        value = style.get(key)
        if value is None or value == "":
            continue
        # Every value is validated so a malformed one can't smuggle a second
        # declaration (";") or close the style attribute out of the inline style.
        if key in ("color", "bgColor"):
            value = _safe_css_value(value)
            if not value:
                continue
        elif key == "fontFamily":
            value = _sanitize_font_family(value)
            if not value:
                continue
        else:
            # fontSize / fontWeight / align / lineHeight / letterSpacing /
            # textTransform / layout lengths: numbers and keywords only.
            if not _SAFE_STYLE_TOKEN_RE.match(str(value)):
                continue
        _set_css_prop(el, css_prop, str(value))
    if style.get("italic"):
        _set_css_prop(el, "font-style", "italic")
    if style.get("padding"):
        _set_css_prop(el, "box-sizing", "border-box")
    if style.get("maxWidth") and not style.get("margin"):
        _set_css_prop(el, "margin-left", "auto")
        _set_css_prop(el, "margin-right", "auto")
    _apply_background(el, style)


# A CSS color/value safe enough to interpolate into a stylesheet rule: hex,
# rgb()/rgba()/hsl(), or a plain keyword. Anything with braces/semicolons that
# could break out of the rule is rejected.
_SAFE_CSS_VALUE_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$|^[a-zA-Z]+$|^(?:rgb|rgba|hsl|hsla)\([0-9.,%\s/]+\)$")


def _safe_css_value(value: str) -> str | None:
    v = str(value or "").strip()
    return v if _SAFE_CSS_VALUE_RE.match(v) else None


# URL schemes that execute in a browser — never allowed in a client-set
# href/src. ``data:text/html`` is included because it runs script when navigated
# to or framed; ``data:image/`` (images only) is allowed separately.
def _is_executable_url(value: str) -> bool:
    v = re.sub(r"\s", "", str(value or "")).lower()
    return v.startswith(("javascript:", "vbscript:", "data:text/html"))


# Safe hosted/relative schemes for link hrefs and media srcs.
_SAFE_URL_SCHEMES_LINK = ("http://", "https://", "mailto:", "tel:")


def _safe_url_value(
    value: str, *, allow_anchor: bool = False, allow_data_image: bool = False
) -> str | None:
    """Return ``value`` if it is safe to write into an ``href``/``src``, else None.

    Accepts http(s), mailto:/tel: (links), absolute (``/``) and relative paths,
    in-page anchors (``#``, links only), and — images only — ``data:image/``
    URIs. Rejects ``javascript:``, ``vbscript:``, and ``data:text/html`` (the
    schemes that execute in a browser). Empty string is passed through so a
    field can clear/keep its default. This mirrors the sanitizer's
    ``_is_safe_url`` but is used for the typed field values the richtext/blog
    sanitizers never see."""
    v = str(value or "").strip()
    if not v:
        return v
    if re.search(r"[\x00-\x1f]", v):
        return None
    if _is_executable_url(v):
        return None
    lowered = v.lower()
    if lowered.startswith(_SAFE_URL_SCHEMES_LINK):
        return v
    if allow_data_image and lowered.startswith("data:image/"):
        return v
    if v.startswith("/"):
        return v
    if allow_anchor and v.startswith("#"):
        return v
    # No scheme at all (e.g. "page/sub") is treated as a relative path.
    if ":" not in v.split("/", 1)[0]:
        return v
    return None


def _apply_styles(soup: BeautifulSoup, styles: dict) -> None:
    """Apply every per-element style override to its `data-edit` element(s).

    Inline styles on the element win over the template's class rules for that
    element. But a text *color* must also reach styled descendants (an <em> or
    <span> with its own color rule), which inline-on-the-parent can't do. The
    child's own rule wins. So for color we additionally emit a scoped
    ``[data-edit="id"] * { color: ... !important }`` stylesheet rule.
    """
    if not isinstance(styles, dict):
        return
    descendant_rules = []
    for element_id, style in styles.items():
        if not isinstance(element_id, str) or "." not in element_id:
            continue
        if not isinstance(style, dict):
            continue
        if element_id.endswith(_BLOCK_STYLE_SUFFIX):
            inst_id = element_id[: -len(_BLOCK_STYLE_SUFFIX)]
            if inst_id:
                for el in soup.find_all(attrs={"data-instance-id": inst_id}):
                    _apply_element_styles(el, style)
            continue
        if _REGION_STYLE_MARK in element_id:
            inst_id, _, region = element_id.partition(_REGION_STYLE_MARK)
            if inst_id and region:
                for wrap in soup.find_all(attrs={"data-instance-id": inst_id}):
                    for kid in wrap.find_all(True, recursive=False):
                        if kid.get("data-region") == region:
                            _apply_element_styles(kid, style)
            continue
        for el in soup.find_all(attrs={"data-edit": element_id}):
            _apply_element_styles(el, style)
        color = _safe_css_value(style.get("color", ""))
        if color:
            sel_id = element_id.replace('"', "").replace("\\", "")
            # Exclude selection-styled spans (cms-tspan) so a per-part colour
            # inside the element isn't overridden by the whole-element colour.
            descendant_rules.append(
                f'[data-edit="{sel_id}"] *:not(.cms-tspan) {{ color: {color} !important; }}'
            )
    if descendant_rules:
        tag = soup.new_tag("style")
        tag["data-cms-elem"] = "true"
        tag.string = "".join(descendant_rules)
        (soup.find("head") or soup.find("body") or soup).append(tag)


def _apply_global_styles(soup: BeautifulSoup, global_styles: dict) -> None:
    """Write site-wide typography defaults as a low-specificity <style> block.
    Per-element inline styles always win over these; the template's own
    element-specific CSS may still override the body-level defaults."""
    if not isinstance(global_styles, dict):
        return
    # These decls are interpolated into a stylesheet, so each value is validated
    # against the same allowlists as inline styles — an unvalidated value
    # ("#fff} body{...}") would inject arbitrary site-wide CSS.
    body_decls = []
    font_family = _sanitize_font_family(global_styles.get("fontFamily") or "")
    base_size = global_styles.get("baseSize")
    text_color = _safe_css_value(global_styles.get("textColor") or "")
    heading_family = _sanitize_font_family(global_styles.get("headingFamily") or "")
    page_bg = _safe_css_value(global_styles.get("pageBg") or "")
    if font_family:
        body_decls.append(f"font-family: {font_family};")
    if base_size and _SAFE_STYLE_TOKEN_RE.match(str(base_size)):
        body_decls.append(f"font-size: {base_size};")
    if text_color:
        body_decls.append(f"color: {text_color};")
    if page_bg:
        body_decls.append(f"background-color: {page_bg};")

    rules = []
    if body_decls:
        rules.append("body{" + " ".join(body_decls) + "}")
    if heading_family:
        rules.append("h1,h2,h3,h4,h5,h6{font-family: " + heading_family + ";}")
    if not rules:
        return

    style = soup.new_tag("style")
    style["data-cms-global"] = "true"
    style.string = "".join(rules)
    (soup.find("head") or soup.find("body") or soup).append(style)


def _apply_tokens(soup: BeautifulSoup, tokens: dict) -> None:
    """Override template design tokens (CSS custom properties) site-wide.

    Appends a ``<style>:root{ --name: value; }</style>`` block after the
    template's own styles, so ``var(--name)`` everywhere resolves to the
    client's chosen value; buttons, headings, and accents all recolor together
    with no per-element overrides."""
    if not isinstance(tokens, dict):
        return
    decls = []
    for name, value in tokens.items():
        if not isinstance(name, str):
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", name)
        safe_val = _safe_css_value(value)
        if safe_name and safe_val:
            decls.append(f"--{safe_name}: {safe_val};")
    if not decls:
        return
    tag = soup.new_tag("style")
    tag["data-cms-tokens"] = "true"
    tag.string = ":root{" + "".join(decls) + "}"
    (soup.find("head") or soup.find("body") or soup).append(tag)


_FONT_NAME_RE = re.compile(r"[^A-Za-z0-9 \-]")
# Weights we request so the per-element weight control (300-800) always has glyphs.
_FONT_WEIGHTS = "300;400;500;600;700;800"


def _sanitize_font_family(name: str) -> str:
    """Reduce a family name to a Google-Fonts-safe token (letters, digits,
    spaces, hyphens). Prevents URL/HTML injection from free-text font input."""
    return _FONT_NAME_RE.sub("", (name or "")).strip()


def _collect_font_families(content: dict) -> list[str]:
    """Every distinct family used across per-element _styles and _global,
    sanitized and de-duplicated in first-seen order."""
    if not isinstance(content, dict):
        return []
    seen: dict[str, None] = {}
    styles = content.get("_styles")
    if isinstance(styles, dict):
        for style in styles.values():
            if isinstance(style, dict):
                fam = _sanitize_font_family(style.get("fontFamily", ""))
                if fam:
                    seen.setdefault(fam, None)
    glob = content.get("_global")
    if isinstance(glob, dict):
        for key in ("fontFamily", "headingFamily"):
            fam = _sanitize_font_family(glob.get(key, ""))
            if fam:
                seen.setdefault(fam, None)
    return list(seen.keys())


def _inject_font_links(soup: BeautifulSoup, families: list[str]) -> None:
    """Inject one Google Fonts stylesheet <link> (+ preconnects) for the given
    families. All carry data-cookieconsent="ignore" so Cookiebot auto-blocking
    doesn't strip the font CDN."""
    if not families:
        return
    head = soup.find("head") or soup.find("body")
    if head is None:
        return
    params = "&".join(
        f"family={fam.replace(' ', '+')}:wght@{_FONT_WEIGHTS}" for fam in families
    )
    href = f"https://fonts.googleapis.com/css2?{params}&display=swap"

    pre1 = soup.new_tag("link", rel="preconnect", href="https://fonts.googleapis.com")
    pre1["data-cookieconsent"] = "ignore"
    pre2 = soup.new_tag("link", rel="preconnect", href="https://fonts.gstatic.com")
    pre2["crossorigin"] = ""
    pre2["data-cookieconsent"] = "ignore"
    link = soup.new_tag("link", rel="stylesheet", href=href)
    link["data-cookieconsent"] = "ignore"
    head.append(pre1)
    head.append(pre2)
    head.append(link)


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
    main site, with per-page overrides layered in by the caller.
    """
    if not html or not head_settings:
        return html
    soup = BeautifulSoup(html, "lxml")
    _inject_site_settings(soup, head_settings)
    return str(soup)


def _apply_hidden(soup: BeautifulSoup, hidden: Any, *, preview: bool) -> None:
    """Mark client-hidden sections/fields with the `cms-hidden` class.

    `hidden` is a list of ids: a bare id (`"testimonials"`) hides a whole
    `data-section` wrapper; a dotted id (`"hero.cta"`) hides one `data-edit`
    element. On the public render we also inject a `display:none` rule (the
    element stays in the DOM per the product choice); in preview the bridge
    stylesheet dims `.cms-hidden` instead so the client can still toggle it.
    """
    if not isinstance(hidden, (list, tuple)):
        return
    applied = False
    for raw in hidden:
        if not isinstance(raw, str) or not raw.strip():
            continue
        ident = raw.strip()
        attr = "data-edit" if "." in ident else "data-section"
        for el in soup.find_all(attrs={attr: ident}):
            classes = el.get("class", []) or []
            if "cms-hidden" not in classes:
                classes.append("cms-hidden")
                el["class"] = classes
            applied = True

    if applied and not preview:
        style = soup.new_tag("style")
        style.string = ".cms-hidden{display:none !important}"
        (soup.find("head") or soup.find("body") or soup).append(style)


# Auto-annotation: every text-leaf element that the agency didn't annotate gets
# a ``data-edit="auto.nN"`` id at render time, so it becomes editable/styleable
# through the normal pipeline, expressing the lp-cms "everything is editable" idea
# as our own annotations. Purely additive (only adds attributes); ids are assigned
# in document order and applied on BOTH preview and public renders so they line up.
_AUTO_TEXT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote",
                   "figcaption", "td", "th", "dt", "dd", "caption", "cite", "address")
# If a candidate contains one of these, it's a container, not a text leaf; skip it
# so we annotate the innermost text element (mirrors lp-cms's leaf detection).
_AUTO_BLOCK_CHILD = ("div", "ul", "ol", "li", "table", "thead", "tbody", "tfoot",
                     "tr", "figure", "form", "section", "article", "nav", "aside",
                     "header", "footer", "main", "dl", "hr", "h1", "h2", "h3", "h4",
                     "h5", "h6", "p", "blockquote")


def _auto_annotate(soup) -> None:
    body = soup.find("body") or soup
    n = 0
    for el in body.find_all(_AUTO_TEXT_TAGS):
        if el.has_attr("data-edit"):
            continue  # already annotated by the agency
        if el.find_parent(attrs={"data-edit": True}) is not None:
            continue  # inside an existing field; outermost wins
        if not el.get_text(strip=True):
            continue  # no visible text
        if el.find(_AUTO_BLOCK_CHILD):
            continue  # container, not a text leaf
        el["data-edit"] = f"auto.n{n}"
        el["data-type"] = "text"
        el["data-label"] = "Text"
        n += 1


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
    _auto_annotate(soup)

    if "brand" in content:
        _apply_brand_tokens(soup, content["brand"] or {})

    needs_ghl_form_script = False
    for el in soup.find_all(attrs={"data-edit": True}):
        full_id = el.get("data-edit", "").strip()
        if "." not in full_id:
            continue
        section, field = full_id.split(".", 1)
        if section == "brand":
            continue

        ftype = el.get("data-type", "text").strip() or "text"
        section_data = content.get(section) or {}
        if ftype == "ghl-embed":
            kind = el.get("data-ghl-kind", "").strip()
            if kind == "form":
                needs_ghl_form_script = (
                    _render_ghl_form_slot(
                        soup, el, section_data.get(field, ""), preview=preview
                    )
                    or needs_ghl_form_script
                )
            elif not preview:
                el.decompose()
            continue
        if field not in section_data:
            href_only = f"{field}_href"
            if el.name == "a" and href_only in section_data:
                _apply_field(el, section_data[href_only], "link", preview=preview)
            continue
        # Same resolution the parser used to extract the default, so a
        # child-bearing "text" field is never written through el.string.
        # Applied after the ghl-embed branch above, which keys off the
        # declared type.
        _apply_field(
            el, section_data[field], effective_field_type(el, ftype), preview=preview
        )
        href_key = f"{field}_href"
        if el.name == "a" and href_key in section_data:
            _apply_field(el, section_data[href_key], "link", preview=preview)

    if needs_ghl_form_script:
        _inject_ghl_form_script(soup)

    if isinstance(content, dict) and isinstance(content.get("_styles"), dict):
        _apply_styles(soup, content["_styles"])
    if isinstance(content, dict) and isinstance(content.get("_global"), dict):
        _apply_global_styles(soup, content["_global"])
    if isinstance(content, dict) and isinstance(content.get("_tokens"), dict):
        _apply_tokens(soup, content["_tokens"])
    _inject_font_links(soup, _collect_font_families(content if isinstance(content, dict) else {}))

    if isinstance(content, dict) and content.get("_hidden"):
        _apply_hidden(soup, content["_hidden"], preview=preview)

    if not preview and site_settings:
        _inject_site_settings(soup, site_settings)

    if preview:
        _inject_preview_reveal_css(soup)
        body = soup.find("body") or soup
        bridge = BeautifulSoup(PREVIEW_BRIDGE_SCRIPT, "lxml")
        for node in list(bridge.body.children if bridge.body else bridge.children):
            body.append(node)

    return str(soup)


_PREVIEW_REVEAL_CSS = (
    ".rv,.reveal,[class*=\"reveal-\"]{opacity:1!important;transform:none!important;"
    "visibility:visible!important;}"
)


def _inject_preview_reveal_css(soup) -> None:
    """Show scroll-reveal copy in the editor even when page JS never adds `.in`.

    Agency pages often hide ``.rv`` at opacity 0 until IntersectionObserver
    runs. Preview scripts can throw (missing modal nodes after a block split)
    or simply not fire, which leaves a blank white canvas over real content.
    """
    if soup.find("style", attrs={"data-cms-preview-reveal": True}):
        return
    tag = soup.new_tag("style", attrs={"data-cms-preview-reveal": "1"})
    tag.string = _PREVIEW_REVEAL_CSS
    (soup.find("head") or soup).append(tag)


def _field_types(schema: dict[str, Any]) -> dict[str, str]:
    types: dict[str, str] = {}
    for section in schema.get("sections") or []:
        for f in section.get("fields") or []:
            fid = f.get("id")
            if fid:
                types[fid] = f.get("type") or "text"
    return types


def _equals_default(value: Any, default: Any, ftype: str) -> bool:
    if value == default:
        return True
    if not isinstance(value, str) or not isinstance(default, str):
        return False
    if ftype == "richtext":
        # BeautifulSoup is not byte-idempotent (attribute quoting, entity
        # encoding), so an untouched fragment can round-trip to different
        # bytes. Canonicalize before deciding somebody edited it.
        return canonicalize_fragment(value.strip()) == canonicalize_fragment(
            default.strip()
        )
    return value.strip() == default.strip()


def strip_defaults(schema: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """Drop every field whose value still equals the template default.

    The near-inverse of ``merge_with_defaults``: ``merge(strip(x))`` equals
    ``merge(x)`` up to the same normalization the renderer already applies when
    deciding a field is unedited: surrounding whitespace on a text field,
    canonical form on a richtext fragment. It is not byte-exact, and a caller
    that needs the stored bytes back verbatim should not route through here. The editor form is pre-filled from
    the merged content and POSTs all of it back, so without this, one save
    freezes a copy of every default into the tenant row. Those copies then win
    over the template forever, which is how a re-annotation that renumbers
    generated ``p_N`` ids silently displaced a whole site's copy.

    Unknown sections and unknown fields are passed through untouched: content
    for something the current template no longer declares is the client's, and
    a bad template save must not be able to delete it. Meta namespaces
    (``_styles``, ``_hidden``, ``_tokens``, ``_global``) are editor state, not
    fields, and are copied verbatim.
    """
    defaults = schema.get("defaults", {}) or {}
    types = _field_types(schema)
    out: dict[str, Any] = {}
    for section_id, fields in (content or {}).items():
        if isinstance(section_id, str) and section_id.startswith("_"):
            out[section_id] = fields
            continue
        section_defaults = defaults.get(section_id)
        if not isinstance(fields, dict) or not isinstance(section_defaults, dict):
            out[section_id] = fields
            continue
        kept = {
            key: value
            for key, value in fields.items()
            if key not in section_defaults
            or not _equals_default(
                value, section_defaults[key], types.get(f"{section_id}.{key}", "text")
            )
        }
        if kept:
            out[section_id] = kept
    return out


def merge_with_defaults(schema: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """Fill missing fields with template defaults.

    GHL embed choices are tenant content, never portable template defaults.
    Clear legacy stored defaults before applying explicit, tenant-validated
    content so an old template cannot route leads to another tenant.
    """
    merged: dict[str, Any] = {}
    embed_fields: set[tuple[str, str]] = set()
    for section in schema.get("sections", []) or []:
        for field in section.get("fields", []) or []:
            if field.get("type") != "ghl-embed":
                continue
            field_id = str(field.get("id") or "")
            if "." not in field_id:
                continue
            embed_fields.add(tuple(field_id.split(".", 1)))

    defaults = schema.get("defaults", {}) or {}
    for section_id, fields in defaults.items():
        merged[section_id] = dict(fields)
        for embed_section, embed_field in embed_fields:
            if embed_section == section_id:
                merged[section_id][embed_field] = ""
    for section_id, fields in (content or {}).items():
        # Meta keys (e.g. "_hidden") are NOT sections; they hold editor state
        # like the list of hidden section/field ids. Copy them through verbatim;
        # merging them as `{section: {field: value}}` would crash on a list.
        if isinstance(section_id, str) and section_id.startswith("_"):
            merged[section_id] = fields
            continue
        merged.setdefault(section_id, {}).update(fields or {})
    return merged


# --------------------------------------------------------------------------- #
# Block-instance rendering (curated block palette).                            #
#                                                                              #
# A block *shell* template is fixed chrome (data-section nav/footer) plus one  #
# or more `data-region` slots. The tenant/page content carries an ordered list #
# of block *instances* per region; each instance references a BlockType and    #
# carries its own field values. Rendering clones each block fragment, rewrites #
# its `data-edit` ids to `<instanceId>.<field>` (so duplicate block types on   #
# the same page never collide), assembles them into the region, then reuses    #
# `render_site` verbatim so every existing apply rule (image srcset reset,     #
# richtext flatten, color, styles, tokens, hidden, GHL, preview bridge) fires  #
# per instance with zero duplication.                                          #
# --------------------------------------------------------------------------- #


_BLANK_FIELD_TAGS_RE = re.compile(r"<[^>]+>", re.I)
_BLANK_FIELD_BR_RE = re.compile(r"<br\s*/?>", re.I)
_BLANK_FIELD_NBSP_RE = re.compile(r"&nbsp;|&#160;|\u00a0", re.I)


def _is_blank_field_value(value: Any) -> bool:
    """True for missing values and contenteditable leftovers (``<br>``, nbsp)."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = _BLANK_FIELD_NBSP_RE.sub(" ", value)
    text = _BLANK_FIELD_BR_RE.sub("", text)
    text = _BLANK_FIELD_TAGS_RE.sub("", text)
    return not text.strip()


def drop_blank_instance_fields(content: dict[str, Any] | None) -> None:
    """Remove empty / ``<br>`` instance fields so they cannot override defaults."""
    if not isinstance(content, dict):
        return
    regions = content.get("regions")
    if not isinstance(regions, dict):
        return

    def _walk(instances: list) -> None:
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            fields = inst.get("fields")
            if isinstance(fields, dict):
                inst["fields"] = {
                    key: value
                    for key, value in fields.items()
                    if not _is_blank_field_value(value)
                }
            children = inst.get("children")
            if isinstance(children, dict):
                for child_list in children.values():
                    if isinstance(child_list, list):
                        _walk(child_list)

    for inst_list in regions.values():
        if isinstance(inst_list, list):
            _walk(inst_list)


def merge_block_defaults(
    block_schema: dict[str, Any], instance_fields: dict[str, Any] | None
) -> dict[str, str]:
    """Fill a single instance's missing fields with the block type's defaults.

    Mirrors ``merge_with_defaults`` at instance scope: GHL embed choices are
    never portable defaults, so they start empty and are only set by explicit,
    tenant-validated instance content.
    """
    merged: dict[str, Any] = dict(block_schema.get("defaults") or {})
    for field in block_schema.get("fields") or []:
        if field.get("type") == "ghl-embed":
            merged[str(field.get("id"))] = ""
    for key, value in (instance_fields or {}).items():
        if _is_blank_field_value(value):
            continue
        merged[str(key)] = value
    return merged


def _build_block_instance(
    instance: dict[str, Any], catalog: dict[str, dict], *, preview: bool = False
):
    """Return (wrapper_element, instance_content) for one block instance, or
    (None, None) when its block type is unknown/inactive. The wrapper is a
    detached BeautifulSoup element ready to append into a region slot."""
    if not isinstance(instance, dict):
        return None, None
    inst_id = str(instance.get("id") or "").strip()
    btype = catalog.get(instance.get("type"))
    if not inst_id or not btype:
        return None, None

    frag = BeautifulSoup(btype.get("html") or "", "lxml")
    wrapper = (
        frag.find(attrs={"data-block": True})
        or frag.find(attrs={"data-section": True})
    )
    if wrapper is None:
        return None, None

    key = (wrapper.get("data-block") or wrapper.get("data-section") or "").strip()
    # Promote the block to an instance "section": downstream section-based
    # behavior (scroll-to-section, hide-whole-block, flash) keys off
    # data-section, so the instance id becomes the section id.
    if wrapper.has_attr("data-block"):
        del wrapper["data-block"]
    wrapper["data-section"] = inst_id
    wrapper["data-instance-id"] = inst_id
    wrapper["data-block-type"] = key
    # Friendly name for the on-canvas selection label — preview-only so the
    # public render stays byte-for-byte identical to the classic render
    # (migration parity). Falls back to a title-cased key client-side.
    if preview:
        _blk_label = str(btype.get("label") or "").strip()
        if _blk_label:
            wrapper["data-cms-label"] = _blk_label

    # Rewrite every field id from the block-relative `key.field` to the
    # per-instance `instanceId.field` so N copies of the same block type never
    # share a value.
    for field_el in wrapper.find_all(attrs={"data-edit": True}):
        fid = (field_el.get("data-edit") or "").strip()
        if "." not in fid:
            continue
        section_part, field_part = fid.split(".", 1)
        if section_part != key:
            continue
        field_el["data-edit"] = f"{inst_id}.{field_part}"

    inst_content = merge_block_defaults(btype.get("schema") or {}, instance.get("fields"))
    return wrapper.extract(), inst_content


_EMPTY_COLUMN_STYLE = (
    "min-height:72px;border:1px dashed #cbd5e1;border-radius:8px;"
    "display:flex;align-items:center;justify-content:center;text-align:center;"
    "color:#94a3b8;font:13px/1.4 system-ui,sans-serif;padding:8px;"
)


def _empty_slot_button(dest: str) -> str:
    """A clickable "+" placeholder for an empty column (preview only). Clicking
    it asks the editor (via the bridge) to open Quick Add with this column as
    the preset destination — GHL-style."""
    return (
        '<button type="button" data-cms-add-here="1" '
        f'data-cms-dest="{dest}" aria-label="Add block to this column" '
        'style="cursor:pointer;display:flex;flex-direction:column;'
        'align-items:center;gap:2px;width:100%;border:0;background:transparent;'
        'color:#94a3b8;font:13px/1.4 system-ui,sans-serif;">'
        '<span style="font-size:26px;font-weight:600;line-height:1;">+</span>'
        '<span>Add block</span></button>'
    )


_HEADER_ADDERS = {
    "header-center": ("nav-link", "Add link"),
    "header-right": ("button", "Add button"),
    "header": ("button", "Add button"),
    "nav": ("nav-link", "Add link"),
}


def _chrome_add_button(dest: str, label: str, add_type: str) -> str:
    """Compact inline + for navbar slots (preview only). Inserts the typed
    block directly instead of opening the full page-builder palette."""
    return (
        '<button type="button" class="cms-chrome-add" data-cms-add-here="1" '
        f'data-cms-dest="{dest}" data-cms-add-type="{add_type}" '
        f'aria-label="{label}">+ {label}</button>'
    )


def _assemble_instance(
    instance: dict[str, Any],
    catalog: dict[str, dict],
    flat_content: dict[str, Any],
    *,
    depth: int = 0,
    max_depth: int = 2,
    preview: bool = False,
):
    """Build one instance wrapper, fill its nested column regions with child
    instances (recursively), and register every instance's content in
    ``flat_content`` keyed by instance id. Returns the wrapper element or
    ``None`` when the block type is unknown/inactive.

    In ``preview`` mode empty column slots get a visible dashed placeholder so
    a just-added (still empty) row is not an invisible band in the editor. The
    placeholder is preview-only — the public render never emits it."""
    wrapper, inst_content = _build_block_instance(instance, catalog, preview=preview)
    if wrapper is None:
        return None
    flat_content[str(instance.get("id"))] = inst_content

    if depth < max_depth:
        children = instance.get("children")
        if isinstance(children, dict):
            for slot in wrapper.find_all(attrs={"data-region": True}):
                name = (slot.get("data-region") or "").strip()
                slot.clear()
                for child in children.get(name) or []:
                    child_wrapper = _assemble_instance(
                        child, catalog, flat_content,
                        depth=depth + 1, max_depth=max_depth, preview=preview,
                    )
                    if child_wrapper is not None:
                        slot.append(child_wrapper)

    if preview:
        for slot in wrapper.find_all(attrs={"data-region": True}):
            if not slot.contents:
                name = (slot.get("data-region") or "").strip()
                dest = f"{instance.get('id')}/{name}"
                existing = (slot.get("style") or "").rstrip(";")
                slot["style"] = (existing + ";" if existing else "") + _EMPTY_COLUMN_STYLE
                slot["data-empty-region"] = "1"
                slot.append(BeautifulSoup(_empty_slot_button(dest), "html.parser"))
    return wrapper


def render_page_from_blocks(
    shell_html: str,
    content: dict[str, Any],
    catalog: dict[str, dict],
    *,
    preview: bool = False,
    site_settings: dict[str, Any] | None = None,
    nav_pages: list[dict[str, Any]] | None = None,
) -> str:
    """Render a block-shell page: fixed chrome + ordered block instances.

    ``content`` is the raw page content: ``regions`` (ordered instance lists
    keyed by region name) plus top-level chrome sections (``nav``, ``footer``,
    ``brand``) and ``_``-prefixed meta (``_styles`` / ``_hidden`` / ``_tokens``
    / ``_global``). ``catalog`` maps a block key to ``{"schema": ..., "html":
    ...}``.
    """
    if not shell_html:
        return ""
    content = content or {}
    from core.services.blocks import (
        MAX_BLOCK_DEPTH,
        _HEADER_SLOTS,
        alias_chrome_regions,
        apply_header_chrome,
        _apply_header_name,
        normalize_header,
        should_paint_header,
    )

    regions = alias_chrome_regions(content.get("regions") or {})
    header_meta = normalize_header(
        content.get("_header"), regions=regions, nav_pages=nav_pages,
    )

    # Chrome + meta get the classic default-merge against the shell's own
    # (fixed-section) schema so empty chrome fields fall back to template
    # defaults. Instance content is added per-instance below.
    chrome_content = {k: v for k, v in content.items() if k != "regions"}
    shell_schema = build_schema(shell_html)
    flat_content = merge_with_defaults(shell_schema, chrome_content)

    soup = BeautifulSoup(shell_html, "lxml")
    paint_header = should_paint_header(soup, header_meta)
    region_slots = soup.find_all(attrs={"data-region": True})
    for region_el in region_slots:
        name = (region_el.get("data-region") or "main").strip() or "main"
        instances = regions.get(name) or []
        if name in _HEADER_SLOTS:
            if paint_header:
                region_el.clear()
            continue
        if instances:
            region_el.clear()
            for instance in instances:
                wrapper = _assemble_instance(
                    instance, catalog, flat_content, depth=0,
                    max_depth=MAX_BLOCK_DEPTH, preview=preview,
                )
                if wrapper is None:
                    continue
                region_el.append(wrapper)
        if preview and not region_el.contents:
            dest = name
            existing = (region_el.get("style") or "").rstrip(";")
            compact = name.startswith("footer")
            extra = (
                "min-height:40px;border:1px dashed #cbd5e1;border-radius:8px;"
                "display:flex;align-items:center;justify-content:center;"
                "padding:6px 10px;"
                if compact
                else _EMPTY_COLUMN_STYLE
            )
            region_el["style"] = (existing + ";" if existing else "") + extra
            region_el["data-empty-region"] = "1"
            region_el.append(BeautifulSoup(_empty_slot_button(dest), "html.parser"))
        elif preview and name.startswith("footer"):
            region_el.append(BeautifulSoup(_empty_slot_button(name), "html.parser"))

    if paint_header:
        apply_header_chrome(soup, header_meta, preview=preview)

    # Opt-in menus outside the header still get published pages. The header
    # navbar is painted from ``_header`` above; don't refill those markers.
    if nav_pages:
        for menu_el in soup.find_all(attrs={"data-nav-pages": True}):
            if menu_el.find_parent("header"):
                continue
            menu_el.clear()
            for entry in nav_pages:
                link = soup.new_tag("a", href=entry.get("url") or "#")
                link.string = entry.get("title") or ""
                menu_el.append(link)

    assembled_html = str(soup)
    # Reuse render_site verbatim so every apply rule + the preview bridge fire
    # exactly as they do for classic pages — the assembled page is now just a
    # normal annotated document whose "sections" are block instances.
    html = render_site(
        assembled_html, flat_content, preview=preview, site_settings=site_settings
    )
    # Field apply writes the brand text after chrome paint; hide it again so
    # "Show site name" off cannot lose to a leftover wordmark.
    if not header_meta.get("show_name", True):
        out = BeautifulSoup(html, "lxml")
        _apply_header_name(out, header_meta)
        return str(out)
    return html
