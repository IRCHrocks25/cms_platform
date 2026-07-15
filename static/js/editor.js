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
  // Minimal in-browser HTML scrub — neutralizes stored richtext before it is
  // injected into the (same-origin, authenticated) editor DOM. <template>
  // content is inert so onerror/onload can't fire while we clean.
  function cmsScrub(html) {
    var tpl = document.createElement("template");
    tpl.innerHTML = html || "";
    var bad = tpl.content.querySelectorAll(
      "script,style,iframe,object,embed,form,input,button,link,meta,base,svg,math,noscript"
    );
    for (var i = 0; i < bad.length; i++) { bad[i].remove(); }
    var els = tpl.content.querySelectorAll("*");
    for (var j = 0; j < els.length; j++) {
      var el = els[j];
      for (var k = el.attributes.length - 1; k >= 0; k--) {
        var name = el.attributes[k].name.toLowerCase();
        var val = (el.attributes[k].value || "").replace(/\s/g, "").toLowerCase();
        if (name.indexOf("on") === 0) {
          el.removeAttribute(el.attributes[k].name);
        } else if ((name === "href" || name === "src" || name === "xlink:href") &&
                   val.indexOf("javascript:") === 0) {
          el.removeAttribute(el.attributes[k].name);
        }
      }
    }
    return tpl.innerHTML;
  }

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

  function scrollPreviewToSection(sectionId) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "scroll-to-section", payload: { id: sectionId } },
      "*"
    );
  }

  // ---- visibility: hide / show sections & individual items -------------
  // State lives in content._hidden (a list of ids) so it rides the normal
  // autosave. A bare id ("hero") hides a section; a dotted id ("hero.cta")
  // hides one field. Hiding is fully reversible — structure stays locked.
  if (!Array.isArray(content._hidden)) content._hidden = [];

  // ---- per-element styles (color / size / font / weight / italic / align) ----
  // State lives in content._styles[fieldId] = { color, fontSize, ... } so it
  // rides the normal autosave. Empty style objects are pruned.
  if (typeof content._styles !== "object" || content._styles === null) content._styles = {};
  if (typeof content._global !== "object" || content._global === null) content._global = {};

  function getStyle(fieldId) { return content._styles[fieldId] || {}; }
  function setStyleProp(fieldId, prop, value) {
    var s = content._styles[fieldId] || {};
    if (value === "" || value === null || value === undefined || value === false) {
      delete s[prop];
    } else {
      s[prop] = value;
    }
    if (Object.keys(s).length) content._styles[fieldId] = s;
    else delete content._styles[fieldId];
  }
  function pushStyleToPreview(fieldId) {
    if (!previewReady) return;
    var p = {}; p[fieldId] = getStyle(fieldId);
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-styles", payload: p }, "*");
  }
  function pushGlobalToPreview() {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-global", payload: content._global }, "*");
  }

  var EYE_ON =
    '<svg class="cms-eye cms-eye-on" width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF =
    '<svg class="cms-eye cms-eye-off" width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M9.9 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a18 18 0 0 1-3 3.9M6.6 6.6A18 18 0 0 0 2 12s3.5 7 10 7a10.4 10.4 0 0 0 4.1-.9"/>' +
    '<path d="M3 3l18 18"/></svg>';
  var visResetBar = null;

  function isHidden(id) { return content._hidden.indexOf(id) !== -1; }
  function setHiddenState(id, hide) {
    var i = content._hidden.indexOf(id);
    if (hide && i === -1) content._hidden.push(id);
    else if (!hide && i !== -1) content._hidden.splice(i, 1);
  }
  function pushVisibility(id, hidden) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "toggle-visibility", payload: { id: id, hidden: hidden } },
      "*"
    );
  }
  function reflectVisibility(id, hidden) {
    document.querySelectorAll('[data-vis-id="' + id + '"]').forEach(function (b) {
      b.setAttribute("aria-pressed", hidden ? "true" : "false");
      b.title = hidden ? "Hidden on your site — click to show" : "Hide this on your site";
    });
    var f = document.querySelector('.field[data-field-id="' + id + '"]');
    if (f) f.classList.toggle("cms-form-hidden", hidden);
    var s = document.querySelector('.editor-form-section[data-section-id="' + id + '"]');
    if (s) s.classList.toggle("cms-form-hidden", hidden);
  }
  function toggleVisibility(id) {
    var hide = !isHidden(id);
    setHiddenState(id, hide);
    reflectVisibility(id, hide);
    pushVisibility(id, hide);
    updateResetBar();
    scheduleSave();
  }
  function makeVisToggle(id) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "cms-vis-toggle";
    b.setAttribute("data-vis-id", id);
    b.setAttribute("aria-pressed", isHidden(id) ? "true" : "false");
    b.title = isHidden(id) ? "Hidden on your site — click to show" : "Hide this on your site";
    b.innerHTML = EYE_ON + EYE_OFF;
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      toggleVisibility(id);
    });
    return b;
  }
  function updateResetBar() {
    if (!visResetBar) return;
    var n = content._hidden.length;
    visResetBar.hidden = n === 0;
    var label = visResetBar.querySelector("[data-vis-count]");
    if (label) label.textContent = n + (n === 1 ? " item" : " items") + " hidden on your site";
  }
  function injectVisibilityToggles() {
    // Section toggles — skip the Brand panel (global colors aren't hideable).
    document.querySelectorAll(".editor-form-section[data-section-id]").forEach(function (sec) {
      if (sec.closest && sec.closest('[data-panel="brand"]')) return;
      var head = sec.querySelector(".editor-form-section-head");
      if (!head) return;
      var id = sec.getAttribute("data-section-id");
      if (isHidden(id)) sec.classList.add("cms-form-hidden");
      head.appendChild(makeVisToggle(id));
    });
    // Per-field toggles — skip Brand fields too.
    document.querySelectorAll(".field[data-field-id]").forEach(function (node) {
      if (node.closest && node.closest('[data-panel="brand"]')) return;
      var id = node.getAttribute("data-field-id");
      if (isHidden(id)) node.classList.add("cms-form-hidden");
      node.classList.add("cms-has-vis");
      node.appendChild(makeVisToggle(id));
    });
    // "Show all hidden" reset bar pinned to the top of the form.
    var form = document.getElementById("editor-form");
    if (form) {
      visResetBar = document.createElement("div");
      visResetBar.className = "cms-vis-resetbar";
      visResetBar.hidden = true;
      visResetBar.innerHTML =
        '<span data-vis-count></span>' +
        '<button type="button" class="btn btn-ghost btn-sm" data-vis-reset>Show all hidden</button>';
      form.insertBefore(visResetBar, form.firstChild);
      visResetBar.querySelector("[data-vis-reset]").addEventListener("click", function () {
        var ids = content._hidden.slice();
        if (!ids.length) return;
        content._hidden = [];
        ids.forEach(function (id) { reflectVisibility(id, false); pushVisibility(id, false); });
        updateResetBar();
        scheduleSave();
      });
      updateResetBar();
    }
  }

  window.addEventListener("message", function (e) {
    var data = e.data || {};
    if (data.source !== "cms-preview") return;
    if (data.type === "ready") {
      previewReady = true;
      pushAllToPreview();
      // Re-assert hidden state in case content._hidden has unsaved changes the
      // freshly server-rendered iframe doesn't reflect yet.
      content._hidden.forEach(function (id) { pushVisibility(id, true); });
      // Same for per-element and global styles.
      Object.keys(content._styles).forEach(function (fid) { pushStyleToPreview(fid); });
      if (content._global && Object.keys(content._global).length) pushGlobalToPreview();
    } else if (data.type === "focus-field") {
      focusFieldInForm(data.payload.id);
    }
  });

  function pushAllToPreview() {
    var patch = {};
    Object.keys(content).forEach(function (sec) {
      if (sec.charAt(0) === "_") return; // skip meta keys (e.g. _hidden)
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
    // Activate the tab / sub-tab that holds this field, else it's in a hidden
    // panel and can't be scrolled to or seen.
    var panel = node.closest && node.closest(".editor-tab-panel");
    if (panel && window.cmsSwitchTab) window.cmsSwitchTab(panel.getAttribute("data-panel"));
    var sub = node.closest && node.closest(".nav-subpanel");
    if (sub && window.cmsSwitchSub) window.cmsSwitchSub(sub.getAttribute("data-subpanel"));
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
    // Tab switching (Content / Brand) is wired inline in editor.html so it is
    // immune to static-file caching; window.cmsSwitchTab is exposed there.
    document.querySelectorAll("[data-field-id]").forEach(function (node) {
      var fieldId = node.dataset.fieldId;
      var ftype = node.dataset.fieldType;
      var current = getValue(fieldId) || "";

      if (ftype === "text") {
        var input = node.querySelector("[data-bind]");
        input.value = current;
        input.addEventListener("input", function () {
          setValue(fieldId, input.value);
          var p = {}; p[fieldId] = input.value;
          pushToPreview(p);
          scheduleSave();
        });
      }

      if (ftype === "link") {
        var sel = node.querySelector("[data-bind-link-select]");
        var row = node.querySelector("[data-link-custom-row]");
        var txt = node.querySelector("[data-bind]");
        var testBtn = node.querySelector("[data-link-test]");
        var warn = node.querySelector("[data-link-warn]");
        txt.value = current;

        function commitLink(v) {
          setValue(fieldId, v);
          var p = {}; p[fieldId] = v;
          pushToPreview(p);
          scheduleSave();
        }

        // A link is "ok" if it's empty, an in-page anchor, a relative path,
        // a mailto:/tel:, or a parseable absolute URL.
        function linkLooksValid(v) {
          if (!v) return true;
          if (v.charAt(0) === "#" || v.charAt(0) === "/") return true;
          if (/^(mailto:|tel:)\S+/i.test(v)) return true;
          try { var u = new URL(v); return !!(u.protocol && u.host); }
          catch (e) { return false; }
        }
        function showWarn(on) {
          if (!warn) return;
          warn.hidden = !on;
          if (on) txt.classList.add("input-error");
          else txt.classList.remove("input-error");
        }

        function isAnchorOption(v) {
          for (var j = 0; j < sel.options.length; j++) {
            var o = sel.options[j].value;
            if (o && o !== "__custom__" && o === v) return true;
          }
          return false;
        }

        // Pick the matching dropdown option, else fall back to the custom row.
        var matchesOption = false;
        for (var i = 0; i < sel.options.length; i++) {
          var ov = sel.options[i].value;
          if (ov && ov !== "__custom__" && ov === current) { matchesOption = true; break; }
        }
        if (current && matchesOption) {
          sel.value = current; row.hidden = true;
        } else if (current) {
          sel.value = "__custom__"; row.hidden = false; showWarn(!linkLooksValid(current));
        } else {
          sel.value = ""; row.hidden = true;
        }

        sel.addEventListener("change", function () {
          if (sel.value === "__custom__") {
            if (isAnchorOption(txt.value)) txt.value = "";
            row.hidden = false;
            showWarn(false);
            txt.focus();
            commitLink(txt.value);
          } else {
            row.hidden = true;
            showWarn(false);
            commitLink(sel.value); // "" (not linked) or "#anchor"
          }
        });

        // Save on every keystroke (non-blocking); clear any warning while typing.
        txt.addEventListener("input", function () {
          showWarn(false);
          commitLink(txt.value);
        });

        // On blur: auto-prepend https:// for bare domains, then warn (but allow).
        txt.addEventListener("blur", function () {
          var v = txt.value.trim();
          var hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(v);
          if (v && !hasScheme && v.charAt(0) !== "#" && v.charAt(0) !== "/" &&
              /^[^\s]+\.[^\s]+$/.test(v)) {
            v = "https://" + v;
            txt.value = v;
            commitLink(v);
          }
          showWarn(!linkLooksValid(txt.value.trim()));
        });

        if (testBtn) {
          testBtn.addEventListener("click", function () {
            var v = txt.value.trim();
            if (v) window.open(v, "_blank", "noopener");
          });
        }
      }

      if (ftype === "richtext") {
        var rt = node.querySelector("[data-bind]");
        rt.innerHTML = cmsScrub(current);
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
            .then(function (r) {
              return r.json().then(function (d) { return { ok: r.ok, data: d }; });
            })
            .then(function (res) {
              if (!res.ok || !res.data.ok) {
                nameEl.textContent = (res.data && res.data.error) || "Upload failed.";
                fileInput.value = "";
                return;
              }
              img.src = res.data.url;
              nameEl.textContent = file.name;
              setValue(fieldId, res.data.url);
              var p = {}; p[fieldId] = res.data.url;
              pushToPreview(p);
              scheduleSave();
            })
            .catch(function () { nameEl.textContent = "Upload failed — please try again."; });
        });
      }

      if (ftype === "video") {
        var vid = node.querySelector("[data-bind-video]");
        var vname = node.querySelector("[data-bind-video-name]");
        var vfile = node.querySelector('input[type="file"]');
        if (current) {
          vid.src = current;
          vid.hidden = false;
          vname.textContent = "Current video";
        }
        vfile.addEventListener("change", function () {
          var file = vfile.files[0];
          if (!file) return;
          if (file.type.indexOf("video/") !== 0) {
            vname.textContent = "Please choose a video file.";
            vfile.value = "";
            return;
          }
          vname.textContent = "Preparing upload…";
          // 1) Ask our server for a signed, scoped upload signature.
          fetch(window.CMS.videoSignUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "X-CSRFToken": window.CMS.csrfToken,
              "Content-Type": "application/json",
            },
            body: "{}",
          })
            .then(function (r) { return r.json(); })
            .then(function (sig) {
              if (!sig.ok) throw new Error(sig.error || "Could not start upload.");
              // 2) Upload the file DIRECTLY to Cloudinary (bypasses our server).
              var fd = new FormData();
              fd.append("file", file);
              fd.append("api_key", sig.api_key);
              fd.append("timestamp", sig.timestamp);
              fd.append("signature", sig.signature);
              fd.append("folder", sig.folder);
              var endpoint = "https://api.cloudinary.com/v1_1/" + sig.cloud_name + "/video/upload";
              return new Promise(function (resolve, reject) {
                var xhr = new XMLHttpRequest();
                xhr.open("POST", endpoint);
                xhr.upload.onprogress = function (e) {
                  if (e.lengthComputable) {
                    vname.textContent = "Uploading… " + Math.round((e.loaded / e.total) * 100) + "%";
                  }
                };
                xhr.onload = function () {
                  if (xhr.status >= 200 && xhr.status < 300) {
                    try { resolve(JSON.parse(xhr.responseText)); }
                    catch (err) { reject(new Error("Bad response from Cloudinary.")); }
                  } else {
                    reject(new Error("Cloudinary upload failed."));
                  }
                };
                xhr.onerror = function () { reject(new Error("Network error during upload.")); };
                xhr.send(fd);
              });
            })
            .then(function (up) {
              vname.textContent = "Finalizing…";
              // 3) Send the public_id back so our server can verify + store it.
              return fetch(window.CMS.videoConfirmUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                  "X-CSRFToken": window.CMS.csrfToken,
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({ public_id: up.public_id, original_name: file.name }),
              }).then(function (r) { return r.json(); });
            })
            .then(function (conf) {
              if (!conf.ok) throw new Error(conf.error || "Could not save video.");
              vid.src = conf.url;
              vid.hidden = false;
              if (vid.load) vid.load();
              vname.textContent = file.name;
              setValue(fieldId, conf.url);
              var p = {}; p[fieldId] = conf.url;
              pushToPreview(p);
              scheduleSave();
            })
            .catch(function (err) {
              vname.textContent = err.message || "Upload failed.";
              vfile.value = "";
            });
        });
      }
    });

    // Bind per-element Style panels.
    document.querySelectorAll("[data-style-panel]").forEach(function (panel) {
      var fieldId = panel.getAttribute("data-style-panel");
      var current = getStyle(fieldId);

      function commit(prop, value) {
        setStyleProp(fieldId, prop, value);
        pushStyleToPreview(fieldId);
        scheduleSave();
      }

      var colorPicker = panel.querySelector("[data-style-color-picker]");
      var colorText = panel.querySelector('[data-style-bind="colorText"]');
      if (current.color) {
        if (colorText) colorText.value = current.color;
        if (colorPicker && /^#[0-9a-fA-F]{6}$/.test(current.color)) colorPicker.value = current.color;
      }
      if (colorPicker) colorPicker.addEventListener("input", function () {
        if (colorText) colorText.value = colorPicker.value;
        commit("color", colorPicker.value);
      });
      if (colorText) colorText.addEventListener("input", function () {
        if (colorPicker && /^#[0-9a-fA-F]{6}$/.test(colorText.value)) colorPicker.value = colorText.value;
        commit("color", colorText.value);
      });

      var size = panel.querySelector('[data-style-bind="fontSize"]');
      if (size) {
        if (current.fontSize) size.value = parseInt(current.fontSize, 10) || "";
        size.addEventListener("input", function () {
          commit("fontSize", size.value ? size.value + "px" : "");
        });
      }

      var fam = panel.querySelector('[data-style-bind="fontFamily"]');
      if (fam) {
        if (current.fontFamily) fam.value = current.fontFamily;
        fam.addEventListener("input", function () { commit("fontFamily", fam.value.trim()); });
      }

      var weight = panel.querySelector('[data-style-bind="fontWeight"]');
      if (weight) {
        if (current.fontWeight) weight.value = current.fontWeight;
        weight.addEventListener("change", function () { commit("fontWeight", weight.value); });
      }

      var italic = panel.querySelector('[data-style-bind="italic"]');
      if (italic) {
        italic.checked = !!current.italic;
        italic.addEventListener("change", function () { commit("italic", italic.checked); });
      }

      var alignBtns = panel.querySelectorAll("[data-style-align]");
      function reflectAlign(val) {
        alignBtns.forEach(function (b) {
          b.setAttribute("aria-pressed", b.getAttribute("data-style-align") === val ? "true" : "false");
        });
      }
      reflectAlign(current.align || "");
      alignBtns.forEach(function (b) {
        b.addEventListener("click", function () {
          var val = b.getAttribute("data-style-align");
          if (getStyle(fieldId).align === val) val = ""; // toggle off
          reflectAlign(val);
          commit("align", val);
        });
      });
    });

    // Bind global typography controls (Design tab).
    document.querySelectorAll("[data-global-bind]").forEach(function (input) {
      var key = input.getAttribute("data-global-bind");
      var cur = content._global[key];
      if (cur) input.value = key === "baseSize" ? parseInt(cur, 10) || "" : cur;
      input.addEventListener("input", function () {
        var v = input.value.trim();
        if (key === "baseSize" && v) v = v + "px";
        if (v) content._global[key] = v; else delete content._global[key];
        pushGlobalToPreview();
        scheduleSave();
      });
    });

    // Inject hide/show eye-toggles onto every section head and field.
    injectVisibilityToggles();

    // Click / focus a field on the form -> highlight + scroll to it in the preview.
    var formEl = document.getElementById("editor-form");
    if (formEl) {
      formEl.addEventListener("focusin", function (e) {
        var node = e.target.closest ? e.target.closest("[data-field-id]") : null;
        if (!node) return;
        highlightField(node);
        highlightInPreview(node.getAttribute("data-field-id"));
      });
    }

    // sidebar jump
    document.querySelectorAll(".sidebar-link").forEach(function (link) {
      link.addEventListener("click", function () {
        var id = link.dataset.jump;
        if (window.cmsSwitchTab) window.cmsSwitchTab("content"); // sections live on the Content tab
        document.querySelectorAll(".sidebar-link").forEach(function (l) {
          l.classList.remove("active");
        });
        link.classList.add("active");
        var target = document.getElementById("section-" + id);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        scrollPreviewToSection(id); // mirror the jump in the live preview
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

    // ---- site settings modal --------------------------------------------
    var settingsModal = document.getElementById("settings-modal");
    var openBtn = document.getElementById("open-settings-btn");
    var closeBtn = document.getElementById("close-settings-btn");
    var cancelBtn = document.getElementById("cancel-settings-btn");
    var saveBtn = document.getElementById("save-settings-btn");
    var statusEl = document.getElementById("settings-status");

    var ssTitle = document.getElementById("ss-page-title");
    var ssDesc = document.getElementById("ss-meta-desc");
    var ssOgImage = document.getElementById("ss-og-image");
    var ssGaId = document.getElementById("ss-ga-id");
    var ssScript = document.getElementById("ss-custom-script");

    function openSettings() {
      statusEl.textContent = "Loading…";
      settingsModal.style.display = "";
      fetch(window.CMS.settingsUrl, {
        method: "GET",
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
        .then(function (data) {
          var s = data.settings || {};
          ssTitle.value = s.page_title || "";
          ssDesc.value = s.meta_description || "";
          ssOgImage.value = s.og_image_url || "";
          ssGaId.value = s.ga_measurement_id || "";
          ssScript.value = s.custom_head_script || "";
          statusEl.textContent = "";
        })
        .catch(function () {
          statusEl.textContent = "Failed to load settings.";
        });
    }

    function closeSettings() {
      settingsModal.style.display = "none";
    }

    function saveSettings() {
      statusEl.textContent = "Saving…";
      saveBtn.disabled = true;
      fetch(window.CMS.settingsUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.CMS.csrfToken,
        },
        body: JSON.stringify({
          page_title: ssTitle.value,
          meta_description: ssDesc.value,
          og_image_url: ssOgImage.value,
          ga_measurement_id: ssGaId.value,
          custom_head_script: ssScript.value,
        }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (result) {
          saveBtn.disabled = false;
          if (!result.ok) {
            statusEl.textContent = (result.data.errors || ["Save failed."]).join(" ");
            return;
          }
          statusEl.textContent = "Saved!";
          setTimeout(closeSettings, 600);
        })
        .catch(function () {
          saveBtn.disabled = false;
          statusEl.textContent = "Save failed — try again.";
        });
    }

    if (openBtn) openBtn.addEventListener("click", openSettings);
    if (closeBtn) closeBtn.addEventListener("click", closeSettings);
    if (cancelBtn) cancelBtn.addEventListener("click", closeSettings);
    if (saveBtn) saveBtn.addEventListener("click", saveSettings);
    if (settingsModal) {
      settingsModal.addEventListener("click", function (e) {
        if (e.target === settingsModal) closeSettings();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && settingsModal && settingsModal.style.display !== "none") {
        closeSettings();
      }
    });

    // ---- version history modal ------------------------------------------
    var historyModal = document.getElementById("history-modal");
    var openHistoryBtn = document.getElementById("open-history-btn");
    var closeHistoryBtn = document.getElementById("close-history-btn");
    var cancelHistoryBtn = document.getElementById("cancel-history-btn");
    var versionList = document.getElementById("version-list");
    var historyStatus = document.getElementById("history-status");

    function closeHistory() { if (historyModal) historyModal.style.display = "none"; }

    function fmtTime(iso) {
      try { return new Date(iso).toLocaleString(); } catch (e) { return iso; }
    }

    function renderVersions(versions) {
      versionList.innerHTML = "";
      if (!versions.length) {
        historyStatus.textContent = "No saved versions yet — edit something and it'll appear here.";
        return;
      }
      historyStatus.textContent = "";
      versions.forEach(function (v) {
        var li = document.createElement("li");
        li.className = "version-row";

        var meta = document.createElement("div");
        meta.className = "version-meta";
        var t = document.createElement("strong");
        t.textContent = fmtTime(v.saved_at);
        var by = document.createElement("span");
        by.textContent = "by " + (v.saved_by || "unknown");
        meta.appendChild(t); meta.appendChild(by);

        var actions = document.createElement("div");
        actions.className = "version-actions";
        var prev = document.createElement("a");
        prev.className = "btn btn-ghost btn-sm";
        prev.textContent = "Preview";
        prev.href = v.preview_url;
        prev.target = "_blank";
        prev.rel = "noopener";
        var rest = document.createElement("button");
        rest.type = "button";
        rest.className = "btn btn-secondary btn-sm";
        rest.textContent = "Restore";
        rest.addEventListener("click", function () {
          if (!window.confirm("Restore this version? Your current content is saved first, so you can undo this.")) return;
          rest.disabled = true;
          historyStatus.textContent = "Restoring…";
          fetch(window.CMS.versionRestoreUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": window.CMS.csrfToken, "Content-Type": "application/json" },
            body: JSON.stringify({ version_id: v.id }),
          })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (!d.ok) { historyStatus.textContent = d.error || "Restore failed."; rest.disabled = false; return; }
              historyStatus.textContent = "Restored — reloading…";
              window.location.reload();
            })
            .catch(function () { historyStatus.textContent = "Restore failed — try again."; rest.disabled = false; });
        });
        actions.appendChild(prev); actions.appendChild(rest);

        li.appendChild(meta); li.appendChild(actions);
        versionList.appendChild(li);
      });
    }

    function openHistory() {
      if (!historyModal) return;
      historyModal.style.display = "";
      historyStatus.textContent = "Loading…";
      versionList.innerHTML = "";
      fetch(window.CMS.versionsUrl, {
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
      })
        .then(function (r) { return r.json(); })
        .then(function (d) { renderVersions((d && d.versions) || []); })
        .catch(function () { historyStatus.textContent = "Couldn't load history."; });
    }

    if (openHistoryBtn) openHistoryBtn.addEventListener("click", openHistory);
    if (closeHistoryBtn) closeHistoryBtn.addEventListener("click", closeHistory);
    if (cancelHistoryBtn) cancelHistoryBtn.addEventListener("click", closeHistory);
    if (historyModal) {
      historyModal.addEventListener("click", function (e) {
        if (e.target === historyModal) closeHistory();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && historyModal && historyModal.style.display !== "none") closeHistory();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
