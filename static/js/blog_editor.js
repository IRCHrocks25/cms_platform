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
  syncPlaceholder();

  function sync() {
    bodyInput.value = editor.innerHTML;
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
          document.execCommand("insertImage", false, data.url);
          sync();
        })
        .catch(function () {
          window.alert("Image upload failed. Please try again.");
        })
        .finally(function () { imageInput.value = ""; });
    });
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
          if (coverPreview) { coverPreview.src = data.url; coverPreview.style.display = ""; }
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
    var raw = editor.innerHTML;
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
        b.classList.toggle("active", b.dataset.layout === effective);
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
