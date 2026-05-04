/* Locked CMS — editor client
 * Wires:
 *   - form fields (text, richtext, image upload, color, link)
 *   - live preview iframe (postMessage bridge)
 *   - click-on-preview -> focus-and-highlight field
 *   - sidebar nav + search
 *   - debounced autosave with status indicator
 */
(function () {
  "use strict";

  var content = window.CMS.content || {};
  var saveTimer = null;
  var saveDot = document.getElementById("save-dot");
  var saveText = document.getElementById("save-text");
  var previewFrame = document.getElementById("preview-frame");
  var previewReady = false;

  // ---- helpers ---------------------------------------------------------
  function getValue(fieldId) {
    var parts = fieldId.split(".");
    return (content[parts[0]] || {})[parts[1]];
  }
  function setValue(fieldId, value) {
    var parts = fieldId.split(".");
    if (!content[parts[0]]) content[parts[0]] = {};
    content[parts[0]][parts[1]] = value;
  }

  function setStatus(state) {
    saveDot.classList.remove("saving", "saved");
    if (state === "saving") {
      saveDot.classList.add("saving");
      saveText.textContent = "Saving…";
    } else if (state === "saved") {
      saveDot.classList.add("saved");
      saveText.textContent = "All changes saved";
    } else if (state === "dirty") {
      saveText.textContent = "Unsaved changes";
    } else if (state === "error") {
      saveText.textContent = "Save failed — retrying";
    }
  }

  // ---- save ------------------------------------------------------------
  function scheduleSave() {
    setStatus("dirty");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 600);
  }

  function save() {
    setStatus("saving");
    fetch(window.CMS.saveUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.CMS.csrfToken,
      },
      body: JSON.stringify({ content: content }),
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () { setStatus("saved"); })
      .catch(function () { setStatus("error"); setTimeout(save, 2000); });
  }

  // ---- preview bridge --------------------------------------------------
  function pushToPreview(patch) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-content", payload: patch },
      "*"
    );
  }

  function highlightInPreview(fieldId) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "highlight-field", payload: { id: fieldId } },
      "*"
    );
  }

  window.addEventListener("message", function (e) {
    var data = e.data || {};
    if (data.source !== "cms-preview") return;
    if (data.type === "ready") {
      previewReady = true;
      pushAllToPreview();
    } else if (data.type === "focus-field") {
      focusFieldInForm(data.payload.id);
    }
  });

  function pushAllToPreview() {
    var patch = {};
    Object.keys(content).forEach(function (sec) {
      Object.keys(content[sec]).forEach(function (f) {
        patch[sec + "." + f] = content[sec][f];
      });
    });
    pushToPreview(patch);
  }

  // ---- focus on field --------------------------------------------------
  function focusFieldInForm(fieldId) {
    var node = document.querySelector('[data-field-id="' + fieldId + '"]');
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    var input = node.querySelector("[data-bind]");
    if (input) {
      if (input.contentEditable === "true") {
        input.focus();
      } else if (input.type !== "file") {
        input.focus();
        if (input.select) input.select();
      }
    }
    highlightField(node);
    highlightInPreview(fieldId);
  }

  function highlightField(node) {
    document.querySelectorAll(".cms-field-active").forEach(function (n) {
      n.classList.remove("cms-field-active");
    });
    node.classList.add("cms-field-active");
  }

  // ---- bind fields -----------------------------------------------------
  function init() {
    document.querySelectorAll("[data-field-id]").forEach(function (node) {
      var fieldId = node.dataset.fieldId;
      var ftype = node.dataset.fieldType;
      var current = getValue(fieldId) || "";

      if (ftype === "text" || ftype === "link") {
        var input = node.querySelector("[data-bind]");
        input.value = current;
        input.addEventListener("input", function () {
          setValue(fieldId, input.value);
          var p = {}; p[fieldId] = input.value;
          pushToPreview(p);
          scheduleSave();
        });
      }

      if (ftype === "richtext") {
        var rt = node.querySelector("[data-bind]");
        rt.innerHTML = current;
        rt.addEventListener("input", function () {
          setValue(fieldId, rt.innerHTML);
          var p = {}; p[fieldId] = rt.innerHTML;
          pushToPreview(p);
          scheduleSave();
        });
      }

      if (ftype === "color") {
        var picker = node.querySelector("[data-bind-color]");
        var text = node.querySelector("[data-bind]");
        var initial = current || "#000000";
        text.value = initial;
        if (/^#[0-9a-fA-F]{6}$/.test(initial)) picker.value = initial;

        function commit(value) {
          setValue(fieldId, value);
          var p = {}; p[fieldId] = value;
          pushToPreview(p);
          scheduleSave();
        }
        picker.addEventListener("input", function () {
          text.value = picker.value;
          commit(picker.value);
        });
        text.addEventListener("input", function () {
          if (/^#[0-9a-fA-F]{6}$/.test(text.value)) picker.value = text.value;
          commit(text.value);
        });
      }

      if (ftype === "image") {
        var img = node.querySelector("[data-bind-image]");
        var nameEl = node.querySelector("[data-bind-image-name]");
        var fileInput = node.querySelector('input[type="file"]');
        if (current) {
          img.src = current;
          nameEl.textContent = "Current image";
        }
        fileInput.addEventListener("change", function () {
          var file = fileInput.files[0];
          if (!file) return;
          nameEl.textContent = "Uploading…";
          var fd = new FormData();
          fd.append("file", file);
          fetch(window.CMS.uploadUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": window.CMS.csrfToken },
            body: fd,
          })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) {
              img.src = data.url;
              nameEl.textContent = file.name;
              setValue(fieldId, data.url);
              var p = {}; p[fieldId] = data.url;
              pushToPreview(p);
              scheduleSave();
            })
            .catch(function () { nameEl.textContent = "Upload failed"; });
        });
      }
    });

    // sidebar jump
    document.querySelectorAll(".sidebar-link").forEach(function (link) {
      link.addEventListener("click", function () {
        var id = link.dataset.jump;
        document.querySelectorAll(".sidebar-link").forEach(function (l) {
          l.classList.remove("active");
        });
        link.classList.add("active");
        var target = document.getElementById("section-" + id);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    // sidebar search
    var search = document.getElementById("section-search");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.toLowerCase().trim();
        document.querySelectorAll(".sidebar-link").forEach(function (link) {
          var label = link.textContent.toLowerCase();
          link.style.display = !q || label.indexOf(q) !== -1 ? "" : "none";
        });
      });
    }

    // viewport toggle
    document.querySelectorAll("#viewport-toggle button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("#viewport-toggle button").forEach(function (b) {
          b.classList.remove("active");
        });
        btn.classList.add("active");
        previewFrame.classList.remove("viewport-tablet", "viewport-mobile");
        if (btn.dataset.viewport === "tablet") previewFrame.classList.add("viewport-tablet");
        if (btn.dataset.viewport === "mobile") previewFrame.classList.add("viewport-mobile");
      });
    });

    // observe scrolling -> highlight current sidebar entry
    var sections = document.querySelectorAll(".editor-form-section");
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          var id = entry.target.dataset.sectionId;
          document.querySelectorAll(".sidebar-link").forEach(function (l) {
            l.classList.toggle("active", l.dataset.jump === id);
          });
        }
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    sections.forEach(function (s) { observer.observe(s); });

    setStatus("saved");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
