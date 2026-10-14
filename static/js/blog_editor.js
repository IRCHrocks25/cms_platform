/* Locked CMS — blog post editor
 * A small contenteditable rich-text editor (no external library) wired to a
 * hidden <textarea name="body">. Inline + cover image uploads reuse the
 * existing tenant media upload endpoint.
 */
(function () {
  "use strict";

  var cfg = window.CMS_BLOG || {};
  var editor = document.getElementById("blog-body-editor");
  var bodyInput = document.getElementById("blog-body-input");
  var toolbar = document.getElementById("rt-toolbar");
  var imageInput = document.getElementById("rt-image-input");
  var form = document.getElementById("blog-form");
  if (!editor || !bodyInput) return;

  // ---- init body --------------------------------------------------------
  editor.innerHTML = bodyInput.value || "";
  prepareImages();
  syncPlaceholder();

  // Editor-only state on images (tabindex for keyboard access) must never
  // reach the saved body or the preview, so serialize from a clean clone.
  // The server sanitizer would drop it anyway; this keeps the round-trip exact.
  function serialize() {
    var clone = editor.cloneNode(true);
    clone.querySelectorAll("img").forEach(function (img) {
      img.removeAttribute("tabindex");
    });
    return clone.innerHTML;
  }

  function prepareImages() {
    editor.querySelectorAll("img").forEach(function (img) {
      if (!img.hasAttribute("tabindex")) img.setAttribute("tabindex", "0");
    });
  }

  function sync() {
    prepareImages();
    bodyInput.value = serialize();
    syncPlaceholder();
    scheduleBodyPreview();
  }
  function syncPlaceholder() {
    var empty = editor.textContent.trim() === "" && !editor.querySelector("img");
    editor.classList.toggle("is-empty", empty);
  }

  editor.addEventListener("input", sync);
  editor.addEventListener("blur", sync);

  // ---- toolbar ----------------------------------------------------------
  function exec(cmd, arg) {
    editor.focus();
    document.execCommand(cmd, false, arg);
    sync();
  }

  if (toolbar) {
    toolbar.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      e.preventDefault();

      var action = btn.dataset.action;
      if (action === "image") {
        if (imageInput) imageInput.click();
        return;
      }

      var cmd = btn.dataset.cmd;
      if (!cmd) return;

      if (cmd === "createLink") {
        var url = window.prompt("Link URL (https://…)");
        if (url) exec("createLink", url);
        return;
      }
      if (cmd === "formatBlock") {
        // Toggle off if the block is already applied.
        exec("formatBlock", btn.dataset.arg);
        return;
      }
      exec(cmd);
    });

    function refreshToolbarState() {
      toolbar.querySelectorAll("button[data-cmd]").forEach(function (btn) {
        var cmd = btn.dataset.cmd;
        if (["bold", "italic", "insertUnorderedList", "insertOrderedList"].indexOf(cmd) === -1) return;
        var active = false;
        try { active = document.queryCommandState(cmd); } catch (err) {}
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }
    document.addEventListener("selectionchange", function () {
      var selection = window.getSelection();
      if (selection && selection.anchorNode && editor.contains(selection.anchorNode)) refreshToolbarState();
    });
  }

  // ---- uploads ----------------------------------------------------------
  function upload(file) {
    var fd = new FormData();
    fd.append("file", file);
    return fetch(cfg.uploadUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": cfg.csrfToken },
      body: fd,
    }).then(function (r) {
      return r.ok ? r.json() : Promise.reject(r);
    });
  }

  if (imageInput) {
    imageInput.addEventListener("change", function () {
      var file = imageInput.files[0];
      if (!file) return;
      editor.focus();
      upload(file)
        .then(function (data) {
          insertImage(data.url);
        })
        .catch(function () {
          window.alert("Image upload failed. Please try again.");
        })
        .finally(function () { imageInput.value = ""; });
    });
  }

  // ---- article images: position, drag-resize, alt text (CMS-78) ---------
  // Placement and width live on the <img> as data-align (full|left|right)
  // and data-width (10-100, step 5 = % of the article column). The server
  // sanitizer allowlists exactly these values and the public blog CSS maps
  // each to a fixed rule, so no client CSS is stored. Selection UI (frame,
  // handle, panel) lives OUTSIDE the contenteditable so it is never saved.
  var wrap = document.getElementById("rt-editor-wrap");
  var frame = document.getElementById("rt-image-frame");
  var handle = document.getElementById("rt-image-handle");
  var panel = document.getElementById("rt-image-panel");
  var alignGroup = document.getElementById("rt-image-align");
  var widthRange = document.getElementById("rt-image-width-range");
  var widthNum = document.getElementById("rt-image-width");
  var widthAuto = document.getElementById("rt-image-width-auto");
  var altInput = document.getElementById("rt-image-alt");
  var removeBtn = document.getElementById("rt-image-remove");
  var doneBtn = document.getElementById("rt-image-done");
  var WIDTH_MIN = 10, WIDTH_MAX = 100, FLOAT_MAX = 70, STEP = 5;
  var selectedImg = null;

  // Browsers' own image resize handles would write inline styles that the
  // sanitizer strips; turn them off so the only resize is ours.
  try { document.execCommand("enableObjectResizing", false, "false"); } catch (err) {}

  function escapeAttr(v) {
    return String(v).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }

  function insertImage(url) {
    editor.focus();
    document.execCommand(
      "insertHTML", false,
      "<img src=\"" + escapeAttr(url) + "\" alt=\"\" data-align=\"full\" data-rt-new=\"1\">"
    );
    var img = editor.querySelector("img[data-rt-new]");
    if (!img) { sync(); return; }
    img.removeAttribute("data-rt-new");
    // Record the intrinsic size so the browser reserves the right aspect
    // ratio before the image loads (no layout jump, no distortion).
    function stampSize() {
      if (img.naturalWidth && img.naturalHeight) {
        img.setAttribute("width", img.naturalWidth);
        img.setAttribute("height", img.naturalHeight);
        sync();
        positionFrame();
      }
    }
    if (img.complete) stampSize(); else img.addEventListener("load", stampSize, { once: true });
    sync();
    selectImage(img);
    if (altInput) altInput.focus();
  }

  function maxWidthFor(img) {
    var align = img.getAttribute("data-align");
    return align === "left" || align === "right" ? FLOAT_MAX : WIDTH_MAX;
  }

  function clampWidth(n, img) {
    n = Math.round(n / STEP) * STEP;
    return Math.max(WIDTH_MIN, Math.min(maxWidthFor(img), n));
  }

  // Width of the editor's content column. Image widths are a % of it, the
  // same way the public page sizes them against the article column.
  function contentWidth() {
    var cs = window.getComputedStyle(editor);
    return editor.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  }

  // Displayed width as a %, so the controls start where the image looks
  // even before it has an explicit data-width.
  function currentWidth(img) {
    var explicit = parseInt(img.getAttribute("data-width"), 10);
    if (explicit) return explicit;
    var cw = contentWidth();
    return cw > 0 ? clampWidth((img.getBoundingClientRect().width / cw) * 100, img) : WIDTH_MAX;
  }

  function refreshPanel() {
    if (!selectedImg || !panel) return;
    var align = selectedImg.getAttribute("data-align") || "full";
    if (alignGroup) {
      alignGroup.querySelectorAll("button").forEach(function (b) {
        var on = b.dataset.align === align;
        b.setAttribute("aria-checked", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
      });
    }
    var max = maxWidthFor(selectedImg);
    var w = currentWidth(selectedImg);
    var explicit = selectedImg.hasAttribute("data-width");
    if (widthRange) { widthRange.max = max; widthRange.value = w; }
    if (widthNum) {
      widthNum.max = max;
      widthNum.value = explicit ? w : "";
      widthNum.placeholder = String(w);
    }
    if (widthAuto) widthAuto.disabled = !explicit;
    if (altInput && document.activeElement !== altInput) {
      altInput.value = selectedImg.getAttribute("alt") || "";
    }
  }

  function positionFrame() {
    if (!selectedImg || !frame || !wrap || !editor.contains(selectedImg)) {
      if (frame) frame.hidden = true;
      return;
    }
    var w = wrap.getBoundingClientRect();
    var r = selectedImg.getBoundingClientRect();
    frame.style.left = (r.left - w.left) + "px";
    frame.style.top = (r.top - w.top) + "px";
    frame.style.width = r.width + "px";
    frame.style.height = r.height + "px";
    frame.classList.toggle("is-right", selectedImg.getAttribute("data-align") === "right");
    frame.hidden = false;
  }

  function selectImage(img) {
    selectedImg = img;
    if (panel) panel.hidden = !img;
    if (!img) { if (frame) frame.hidden = true; return; }
    refreshPanel();
    positionFrame();
  }

  function setWidth(n, opts) {
    if (!selectedImg) return;
    selectedImg.setAttribute("data-width", String(clampWidth(n, selectedImg)));
    positionFrame();
    refreshPanel();
    if (!opts || !opts.silent) sync();
  }

  function setAlign(align) {
    if (!selectedImg) return;
    selectedImg.setAttribute("data-align", align);
    // Beside-text images need room for the text: start them at 40% and
    // never wider than 70%, so switching position is always predictable.
    if (align === "left" || align === "right") {
      var w = parseInt(selectedImg.getAttribute("data-width"), 10);
      if (!w) selectedImg.setAttribute("data-width", "40");
      else if (w > FLOAT_MAX) selectedImg.setAttribute("data-width", String(FLOAT_MAX));
    }
    sync();
    positionFrame();
    refreshPanel();
  }

  function removeSelected() {
    if (!selectedImg) return;
    var img = selectedImg;
    selectImage(null);
    img.remove();
    sync();
    editor.focus();
  }

  editor.addEventListener("click", function (e) {
    var img = e.target.closest && e.target.closest("img");
    selectImage(img && editor.contains(img) ? img : null);
  });
  editor.addEventListener("focusin", function (e) {
    if (e.target.tagName === "IMG") selectImage(e.target);
  });
  editor.addEventListener("keydown", function (e) {
    if (e.target.tagName !== "IMG") return;
    if (e.key === "Enter") {
      e.preventDefault();
      var current = alignGroup && alignGroup.querySelector("[aria-checked=\"true\"]");
      if (current) current.focus();
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeSelected();
    }
  });
  editor.addEventListener("input", positionFrame);
  window.addEventListener("resize", positionFrame);
  window.addEventListener("scroll", positionFrame, true);

  // Pressing anywhere outside the editor, frame, and panel ends the selection.
  document.addEventListener("mousedown", function (e) {
    if (!selectedImg) return;
    if (editor.contains(e.target) || (panel && panel.contains(e.target)) ||
        (frame && frame.contains(e.target))) return;
    selectImage(null);
  });

  if (alignGroup) {
    alignGroup.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-align]");
      if (btn) setAlign(btn.dataset.align);
    });
    // Radio-group arrow keys.
    alignGroup.addEventListener("keydown", function (e) {
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].indexOf(e.key) === -1) return;
      e.preventDefault();
      var btns = Array.prototype.slice.call(alignGroup.querySelectorAll("button"));
      var back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      var i = btns.indexOf(document.activeElement);
      var next = btns[(i + (back ? -1 : 1) + btns.length) % btns.length];
      setAlign(next.dataset.align);
      next.focus();
    });
  }
  if (widthRange) {
    widthRange.addEventListener("input", function () {
      setWidth(parseInt(widthRange.value, 10), { silent: true });
    });
    widthRange.addEventListener("change", function () {
      setWidth(parseInt(widthRange.value, 10));
    });
  }
  if (widthNum) {
    widthNum.addEventListener("change", function () {
      var n = parseInt(widthNum.value, 10);
      if (n) setWidth(n); else refreshPanel();
    });
  }
  if (widthAuto) {
    widthAuto.addEventListener("click", function () {
      if (!selectedImg) return;
      selectedImg.removeAttribute("data-width");
      sync();
      positionFrame();
      refreshPanel();
    });
  }
  if (altInput) {
    altInput.addEventListener("input", function () {
      if (!selectedImg) return;
      selectedImg.setAttribute("alt", altInput.value);
      sync();
    });
  }
  if (removeBtn) removeBtn.addEventListener("click", removeSelected);
  if (doneBtn) {
    doneBtn.addEventListener("click", function () {
      var img = selectedImg;
      selectImage(null);
      if (img) img.focus();
    });
  }
  if (panel) {
    panel.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !selectedImg) return;
      e.preventDefault();
      selectedImg.focus();
    });
  }

  // Drag handle: width follows the pointer, snapped to 5% steps and clamped
  // to the article column (10-100%, or 70% beside text). A centered image
  // grows from its middle, so the pointer distance from center counts twice.
  if (handle) {
    var drag = null;
    handle.addEventListener("pointerdown", function (e) {
      if (!selectedImg) return;
      e.preventDefault();
      var r = selectedImg.getBoundingClientRect();
      var centered = (selectedImg.getAttribute("data-align") || "full") === "full" &&
        parseFloat(window.getComputedStyle(selectedImg).marginLeft) > 0;
      drag = {
        id: e.pointerId,
        rect: r,
        right: selectedImg.getAttribute("data-align") === "right",
        centered: centered,
      };
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id || !selectedImg) return;
      var r = drag.rect;
      var px;
      if (drag.right) px = r.right - e.clientX;
      else if (drag.centered) px = 2 * (e.clientX - (r.left + r.width / 2));
      else px = e.clientX - r.left;
      var cw = contentWidth();
      if (cw > 0) setWidth((px / cw) * 100, { silent: true });
    });
    var endDrag = function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      drag = null;
      sync();
    };
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);
  }

  // ---- cover image ------------------------------------------------------
  var coverFile = document.getElementById("bf-cover-file");
  var coverInput = document.getElementById("cover-input");
  var coverPreview = document.getElementById("cover-preview");
  var coverName = document.getElementById("cover-name");

  if (coverFile) {
    coverFile.addEventListener("change", function () {
      var file = coverFile.files[0];
      if (!file) return;
      if (coverName) coverName.textContent = "Uploading…";
      upload(file)
        .then(function (data) {
          coverInput.value = data.url;
          if (coverPreview) { coverPreview.src = data.url; coverPreview.hidden = false; }
          if (coverName) coverName.textContent = file.name;
          scheduleFieldPreview();
        })
        .catch(function () {
          if (coverName) coverName.textContent = "Upload failed";
        });
    });
  }

  // ---- auto-slug from title (only while slug is untouched) --------------
  var titleEl = document.getElementById("bf-title");
  var slugEl = document.getElementById("bf-slug");
  if (titleEl && slugEl) {
    var slugTouched = slugEl.value.trim() !== "";
    slugEl.addEventListener("input", function () { slugTouched = true; });
    titleEl.addEventListener("input", function () {
      if (slugTouched) return;
      slugEl.value = titleEl.value
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, "")
        .trim()
        .replace(/\s+/g, "-")
        .replace(/-+/g, "-")
        .slice(0, 200);
    });
  }

  // ---- ensure body is synced on submit ----------------------------------
  if (form) form.addEventListener("submit", sync);

  // ---- live preview bridge ----------------------------------------------
  // Mirrors the main site editor: the iframe is server-rendered, then we
  // patch fields in place via postMessage as the user edits. The post BODY
  // is round-tripped through the server sanitizer first, so the preview body
  // is byte-identical to the public render (security + fidelity, one source
  // of truth — no second sanitizer in JS). Plain text/attribute fields
  // (title, author, date, cover src) are safe to patch directly and instant.
  var FROM_SELF = "cms-blog-editor";
  var FROM_FRAME = "cms-blog-preview";
  var iframe = document.getElementById("blog-preview-frame");
  var styleEl = document.getElementById("bf-template");
  var authorEl = document.getElementById("bf-author");
  var dateEl = document.getElementById("bf-date");
  var statusEl = document.getElementById("blog-preview-status");
  var previewLoading = document.getElementById("blog-preview-loading");
  var previewReady = false;
  var fieldTimer = null;
  var bodyTimer = null;
  var sanitizedBody = bodyInput.value || ""; // server-clean HTML to render

  var MONTHS = ["January","February","March","April","May","June","July",
    "August","September","October","November","December"];

  // Format a datetime-local value ("2026-05-29T14:30") to match Django's
  // "F j, Y" ("May 29, 2026"). Empty / unparseable → "".
  function formatDate(val) {
    if (!val) return "";
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(val);
    if (!m) return "";
    var month = MONTHS[parseInt(m[2], 10) - 1];
    if (!month) return "";
    return month + " " + parseInt(m[3], 10) + ", " + m[1];
  }

  function collectContent() {
    return {
      title: titleEl ? titleEl.value : "",
      body: sanitizedBody,
      cover_image: coverInput ? coverInput.value : "",
      author: authorEl ? authorEl.value.trim() : "",
      publish_date: formatDate(dateEl ? dateEl.value : ""),
    };
  }

  function sendContent() {
    if (!previewReady || !iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage(
      { source: FROM_SELF, type: "apply-content", content: collectContent() },
      "*"
    );
  }

  function setUpdating(on) {
    if (!statusEl) return;
    statusEl.textContent = on ? "Updating…" : "Live preview";
  }

  // Cheap fields (title/author/date/cover) — push immediately, lightly debounced.
  function scheduleFieldPreview() {
    if (!iframe) return;
    if (fieldTimer) clearTimeout(fieldTimer);
    fieldTimer = setTimeout(sendContent, 150);
  }

  // Body — debounce, then sanitize on the server before pushing.
  function scheduleBodyPreview() {
    if (!iframe) return;
    setUpdating(true);
    if (bodyTimer) clearTimeout(bodyTimer);
    bodyTimer = setTimeout(sanitizeThenPush, 300);
  }

  function sanitizeThenPush() {
    var raw = serialize();
    if (!cfg.sanitizeUrl) { sanitizedBody = raw; setUpdating(false); sendContent(); return; }
    fetch(cfg.sanitizeUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "X-CSRFToken": cfg.csrfToken,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: "body=" + encodeURIComponent(raw),
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function (data) { sanitizedBody = data.html || ""; })
      .catch(function () { /* keep last good sanitized body */ })
      .finally(function () { setUpdating(false); sendContent(); });
  }

  if (iframe) {
    // The iframe announces itself once its bridge script runs.
    window.addEventListener("message", function (e) {
      var data = e.data || {};
      if (data.source !== FROM_FRAME) return;
      if (data.type === "ready") {
        previewReady = true;
        if (previewLoading) previewLoading.hidden = true;
        sendContent();
      } else if (data.type === "focus-field") {
        if (data.field === "body") editor.focus();
        else if (data.field === "title" && titleEl) titleEl.focus();
        else if (data.field === "author" && authorEl) authorEl.focus();
        else if (data.field === "publish_date" && dateEl) dateEl.focus();
        else if (data.field === "cover_image" && coverFile) coverFile.click();
      }
    });

    if (titleEl) titleEl.addEventListener("input", scheduleFieldPreview);
    if (authorEl) authorEl.addEventListener("input", scheduleFieldPreview);
    if (dateEl) dateEl.addEventListener("input", scheduleFieldPreview);

    // Style switch needs a full reload — a different template renders. Re-push
    // the live (unsaved) content once the new doc's bridge announces ready.
    if (styleEl) {
      styleEl.addEventListener("change", function () {
        previewReady = false;
        if (previewLoading) previewLoading.hidden = false;
        var style = styleEl.value || (cfg.defaultStyle || "");
        var base = cfg.previewUrl || iframe.src.split("?")[0];
        iframe.src = style ? base + "?style=" + encodeURIComponent(style) : base;
      });
    }
  }

  // ---- viewport toggle (desktop / tablet / mobile) ----------------------
  var viewportToggle = document.getElementById("blog-viewport-toggle");
  if (viewportToggle && iframe) {
    viewportToggle.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      var mode = btn.dataset.viewport;
      iframe.classList.remove("viewport-tablet", "viewport-mobile");
      if (mode === "tablet") iframe.classList.add("viewport-tablet");
      else if (mode === "mobile") iframe.classList.add("viewport-mobile");
      viewportToggle.querySelectorAll("button").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
    });
  }

  // ---- layout toggle (Write / Split / Preview) + responsive tabs --------
  // Doubles as side-by-side controls on wide screens and Write/Preview tabs
  // on narrow ones (the "Split" option hides under 900px via CSS).
  var shell = document.getElementById("editor-shell");
  var layoutToggle = document.getElementById("blog-layout-toggle");
  var LAYOUTS = ["editor", "split", "preview"];
  var LS_KEY = "cms-blog-layout";

  function isNarrow() {
    return window.matchMedia && window.matchMedia("(max-width: 900px)").matches;
  }

  function applyLayout(mode) {
    if (LAYOUTS.indexOf(mode) === -1) mode = "split";
    if (!shell) return;
    // On narrow screens there is no real "split" — fall back to Write.
    var effective = (mode === "split" && isNarrow()) ? "editor" : mode;
    shell.classList.remove("layout-editor", "layout-split", "layout-preview");
    shell.classList.add("layout-" + mode);
    if (layoutToggle) {
      layoutToggle.querySelectorAll("button").forEach(function (b) {
        var active = b.dataset.layout === effective;
        b.classList.toggle("active", active);
        b.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }
  }

  if (shell && layoutToggle) {
    var saved = null;
    try { saved = window.localStorage.getItem(LS_KEY); } catch (err) {}
    applyLayout(saved || "split");

    layoutToggle.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      var mode = btn.dataset.layout;
      applyLayout(mode);
      try { window.localStorage.setItem(LS_KEY, mode); } catch (err) {}
    });

    // Re-evaluate the effective layout when crossing the breakpoint.
    if (window.matchMedia) {
      var mq = window.matchMedia("(max-width: 900px)");
      var onChange = function () {
        var cur = "split";
        try { cur = window.localStorage.getItem(LS_KEY) || "split"; } catch (err) {}
        applyLayout(cur);
      };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }
})();
