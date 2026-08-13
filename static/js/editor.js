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
  var saveInFlight = false;
  var saveQueued = false;
  var hasUnsavedChanges = false;
  var saveDot = document.getElementById("save-dot");
  var saveText = document.getElementById("save-text");
  var saveRetry = document.getElementById("save-retry");
  var previewFrame = document.getElementById("preview-frame");
  var previewReady = false;
  var previewLoading = document.getElementById("preview-loading");
  var previewLoadingTitle = document.getElementById("preview-loading-title");
  var previewLoadingCopy = document.getElementById("preview-loading-copy");
  var previewRetry = document.getElementById("preview-retry");
  var previewTimeout = null;

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
    saveDot.classList.remove("saving", "saved", "error");
    if (saveRetry) saveRetry.hidden = state !== "error";
    if (state === "saving") {
      saveDot.classList.add("saving");
      saveText.textContent = "Saving…";
    } else if (state === "saved") {
      saveDot.classList.add("saved");
      saveText.textContent = "All changes saved";
    } else if (state === "dirty") {
      saveText.textContent = "Unsaved changes";
    } else if (state === "error") {
      saveDot.classList.add("error");
      saveText.textContent = navigator.onLine ? "Changes not saved" : "Offline — changes not saved";
    }
  }

  // ---- save ------------------------------------------------------------
  function scheduleSave() {
    if (window.CMS && window.CMS.readOnly) return;
    hasUnsavedChanges = true;
    setStatus("dirty");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 600);
  }

  function save() {
    if (window.CMS && window.CMS.readOnly) return;
    if (saveInFlight) { saveQueued = true; return; }
    saveInFlight = true;
    saveQueued = false;
    var snapshot = JSON.stringify(content);
    setStatus("saving");
    fetch(window.CMS.saveUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.CMS.csrfToken,
      },
      body: JSON.stringify({ content: JSON.parse(snapshot) }),
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () {
        saveInFlight = false;
        if (saveQueued || JSON.stringify(content) !== snapshot) {
          saveQueued = false;
          save();
          return;
        }
        hasUnsavedChanges = false;
        setStatus("saved");
      })
      .catch(function () {
        saveInFlight = false;
        hasUnsavedChanges = true;
        setStatus("error");
      });
  }

  if (saveRetry) saveRetry.addEventListener("click", save);
  window.addEventListener("online", function () { if (hasUnsavedChanges) save(); });
  window.addEventListener("beforeunload", function (e) {
    if (!hasUnsavedChanges || (window.CMS && window.CMS.readOnly)) return;
    e.preventDefault();
    e.returnValue = "";
  });

  function setPreviewState(state) {
    if (!previewLoading) return;
    clearTimeout(previewTimeout);
    if (state === "ready") {
      previewLoading.hidden = true;
      return;
    }
    previewLoading.hidden = false;
    if (previewRetry) previewRetry.hidden = state !== "error";
    if (previewLoadingTitle) previewLoadingTitle.textContent = state === "error" ? "Preview didn’t load" : "Loading preview…";
    if (previewLoadingCopy) previewLoadingCopy.textContent = state === "error" ? "Your edits are safe. Reload just the preview to try again." : "Preparing the latest version of this page.";
    if (state === "loading") {
      previewTimeout = setTimeout(function () { if (!previewReady) setPreviewState("error"); }, 12000);
    }
  }

  function reloadPreview() {
    previewReady = false;
    setPreviewState("loading");
    var src = previewFrame.getAttribute("src").split("#")[0];
    previewFrame.setAttribute("src", src + "#reload-" + Date.now());
  }
  if (previewRetry) previewRetry.addEventListener("click", reloadPreview);
  setPreviewState("loading");

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
  if (typeof content._tokens !== "object" || content._tokens === null) content._tokens = {};

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
  function pushTokensToPreview() {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-tokens", payload: content._tokens }, "*");
  }

  // ---- curated choice lists (pre-made fonts / sizes / colors) ----------
  var CMS_FONTS = [
    "Inter", "Poppins", "Roboto", "Open Sans", "Lato", "Montserrat",
    "Raleway", "Nunito", "Work Sans", "Rubik", "DM Sans", "Source Sans 3",
    "Playfair Display", "Merriweather", "Lora", "Oswald", "Bebas Neue",
    "Dancing Script",
  ];
  var CMS_SIZES = [
    { label: "Small", value: "14px" }, { label: "Normal", value: "16px" },
    { label: "Medium", value: "20px" }, { label: "Large", value: "28px" },
    { label: "X-Large", value: "40px" }, { label: "Huge", value: "56px" },
    { label: "Display", value: "72px" },
  ];
  var CMS_BASE_SIZES = [
    { label: "14px", value: "14px" }, { label: "15px", value: "15px" },
    { label: "16px (default)", value: "16px" }, { label: "17px", value: "17px" },
    { label: "18px", value: "18px" }, { label: "20px", value: "20px" },
  ];
  var CMS_COLORS = [
    "#000000", "#1f2937", "#374151", "#6b7280", "#9ca3af", "#ffffff",
    "#b91c1c", "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16",
    "#22c55e", "#10b981", "#14b8a6", "#06b6d4", "#3b82f6", "#6366f1",
    "#8b5cf6", "#a855f7", "#d946ef", "#ec4899", "#f43f5e", "#7c3aed",
  ];

  // Load the curated fonts into the dashboard document so dropdown option
  // labels render in their own typeface (data-cookieconsent ignore for parity).
  function cmsLoadEditorFonts() {
    if (document.getElementById("cms-editor-fonts")) return;
    var fam = CMS_FONTS.map(function (f) {
      return "family=" + f.replace(/ /g, "+") + ":wght@400;700";
    }).join("&");
    var link = document.createElement("link");
    link.id = "cms-editor-fonts";
    link.rel = "stylesheet";
    link.setAttribute("data-cookieconsent", "ignore");
    link.href = "https://fonts.googleapis.com/css2?" + fam + "&display=swap";
    document.head.appendChild(link);
  }

  function buildFontSelect(sel, current) {
    sel.innerHTML = "";
    var def = document.createElement("option");
    def.value = ""; def.textContent = "Default";
    sel.appendChild(def);
    CMS_FONTS.forEach(function (f) {
      var o = document.createElement("option");
      o.value = f; o.textContent = f; o.style.fontFamily = "'" + f + "'";
      if (f === current) o.selected = true;
      sel.appendChild(o);
    });
    if (!current) sel.value = "";
  }

  function buildSizeSelect(sel, list, current) {
    sel.innerHTML = "";
    var def = document.createElement("option");
    def.value = ""; def.textContent = "Default";
    sel.appendChild(def);
    list.forEach(function (s) {
      var o = document.createElement("option");
      o.value = s.value; o.textContent = s.label;
      if (s.value === current) o.selected = true;
      sel.appendChild(o);
    });
    if (!current) sel.value = "";
  }

  // Build a palette of preset color chips + a "none/default" chip.
  function buildSwatches(container, current, onPick) {
    container.innerHTML = "";
    function mark(el) {
      container.querySelectorAll(".cms-swatch").forEach(function (x) {
        x.classList.remove("active");
      });
      if (el) el.classList.add("active");
    }
    var none = document.createElement("button");
    none.type = "button";
    none.className = "cms-swatch cms-swatch-none";
    none.title = "Default (no override)";
    none.textContent = "×";
    none.addEventListener("click", function () { mark(none); onPick(""); });
    container.appendChild(none);
    var activeSet = false;
    // Surface the current colour as its own chip when it isn't one of the
    // presets (e.g. a theme token's existing value like #6b47b8) so it stays
    // visible and selectable.
    var curLc = (current || "").toLowerCase();
    var inPresets = CMS_COLORS.some(function (c) { return c.toLowerCase() === curLc; });
    if (current && curLc.charAt(0) === "#" && !inPresets) {
      var cur = document.createElement("button");
      cur.type = "button";
      cur.className = "cms-swatch active";
      cur.style.background = current;
      cur.title = current + " (current)";
      cur.setAttribute("data-color", current);
      cur.addEventListener("click", function () { mark(cur); onPick(current); });
      container.appendChild(cur);
      activeSet = true;
    }
    CMS_COLORS.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cms-swatch";
      b.style.background = c;
      b.title = c;
      b.setAttribute("data-color", c);
      if (c.toLowerCase() === (current || "").toLowerCase()) { b.classList.add("active"); activeSet = true; }
      b.addEventListener("click", function () { mark(b); onPick(c); });
      container.appendChild(b);
    });
    if (!activeSet && !current) none.classList.add("active");
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
      setPreviewState("ready");
      pushAllToPreview();
      // Re-assert hidden state in case content._hidden has unsaved changes the
      // freshly server-rendered iframe doesn't reflect yet.
      content._hidden.forEach(function (id) { pushVisibility(id, true); });
      // Same for per-element and global styles.
      Object.keys(content._styles).forEach(function (fid) { pushStyleToPreview(fid); });
      if (content._global && Object.keys(content._global).length) pushGlobalToPreview();
      if (content._tokens && Object.keys(content._tokens).length) pushTokensToPreview();
    } else if (data.type === "focus-field") {
      focusFieldInForm(data.payload.id);
    } else if (data.type === "text-selection") {
      cmsOnSelection(data.payload || {});
    } else if (data.type === "text-update") {
      cmsOnTextUpdate(data.payload || {});
    }
  });

  // ---- selection-based text styling (floating bubble on the preview) ----
  // When text is highlighted on the preview, a small bubble pops up just below
  // it; its controls send colour/bold/italic/size back, the preview applies
  // them to that selection and echoes a `text-update` we persist.
  var selStyleField = null;
  var selStylePanel = document.getElementById("cms-seltext-panel");
  var selPicking = false, selPanelHover = false;
  function cmsSendFormat(msg) {
    if (!previewReady || !selStyleField) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "style-selection", payload: msg }, "*");
  }
  function positionSelPanel(rect) {
    if (!rect || !selStylePanel) return;
    var fr = previewFrame.getBoundingClientRect();
    var pw = selStylePanel.offsetWidth, ph = selStylePanel.offsetHeight;
    var below = fr.top + rect.bottom + 8;
    // Flip above the text if it would overflow the bottom of the window.
    var top = (below + ph > window.innerHeight - 8) ? fr.top + rect.top - ph - 8 : below;
    var left = fr.left + rect.left + rect.width / 2 - pw / 2;
    selStylePanel.style.top = Math.max(8, Math.min(window.innerHeight - ph - 8, top)) + "px";
    selStylePanel.style.left = Math.max(8, Math.min(window.innerWidth - pw - 8, left)) + "px";
  }
  function cmsOnSelection(p) {
    if (p.present && p.id) {
      selStyleField = p.id;
      if (selStylePanel) {
        selStylePanel.classList.add("is-active"); // display it so it can be measured
        positionSelPanel(p.rect);
      }
    } else if (selStylePanel && !selPicking && !selPanelHover) {
      // Keep selStyleField (the controls still target the last highlight) but
      // hide the bubble once nothing is selected and we're not mid-interaction.
      selStylePanel.classList.remove("is-active");
    }
  }
  function cmsOnTextUpdate(p) {
    if (!p.id) return;
    setValue(p.id, p.html);
    var box = document.querySelector('.cms-field-richtext[data-bind="' + p.id + '"]');
    if (box) box.innerHTML = p.html; // keep the form's richtext box in sync
    scheduleSave();
  }
  if (selStylePanel) {
    selStylePanel.addEventListener("mouseenter", function () { selPanelHover = true; });
    selStylePanel.addEventListener("mouseleave", function () { selPanelHover = false; });
    // Keep the preview selection alive when pressing a control (except the
    // native colour input, which needs the mousedown to open its picker).
    selStylePanel.addEventListener("mousedown", function (e) {
      // Buttons keep the selection alive; the colour input and size select need
      // the mousedown to open their native pickers.
      if (!e.target.closest("[data-seltext-color], [data-seltext-size]")) e.preventDefault();
    });
    var selColor = selStylePanel.querySelector("[data-seltext-color]");
    if (selColor) {
      selColor.addEventListener("mousedown", function () { selPicking = true; });
      selColor.addEventListener("input", function () { cmsSendFormat({ prop: "color", value: this.value }); });
      selColor.addEventListener("change", function () { selPicking = false; });
    }
    selStylePanel.querySelectorAll("[data-seltext-cmd]").forEach(function (b) {
      b.addEventListener("click", function () {
        var cmd = b.getAttribute("data-seltext-cmd");
        if (cmd === "bold") cmsSendFormat({ prop: "font-weight", value: "700" });
        else if (cmd === "italic") cmsSendFormat({ prop: "font-style", value: "italic" });
        else if (cmd === "clear") cmsSendFormat({ clear: true });
      });
    });
    var selSize = selStylePanel.querySelector("[data-seltext-size]");
    if (selSize) {
      buildSizeSelect(selSize, CMS_SIZES, "");
      selSize.addEventListener("change", function () { if (selSize.value) cmsSendFormat({ prop: "font-size", value: selSize.value }); });
    }
  }

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
    // Reveal the Style panel so clicking an element in the preview lets you
    // restyle it (font / color / background) right away.
    var stylePanel = node.querySelector(".cms-style-panel");
    if (stylePanel && stylePanel.tagName.toLowerCase() === "details") stylePanel.open = true;
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
          // Server-proxied upload: browser -> our server -> Iceberg. Progress
          // reflects the browser->server leg (the heavy one for the client).
          vname.textContent = "Uploading… 0%";
          var fd = new FormData();
          fd.append("file", file);
          var xhr = new XMLHttpRequest();
          xhr.open("POST", window.CMS.videoUploadUrl);
          xhr.setRequestHeader("X-CSRFToken", window.CMS.csrfToken);
          xhr.withCredentials = true;
          xhr.upload.onprogress = function (e) {
            if (e.lengthComputable) {
              vname.textContent = "Uploading… " + Math.round((e.loaded / e.total) * 100) + "%";
            }
          };
          xhr.onload = function () {
            var conf;
            try { conf = JSON.parse(xhr.responseText); } catch (err) { conf = null; }
            if (xhr.status < 200 || xhr.status >= 300 || !conf || !conf.ok) {
              vname.textContent = (conf && conf.error) || "Upload failed.";
              vfile.value = "";
              return;
            }
            vid.src = conf.url;
            vid.hidden = false;
            if (vid.load) vid.load();
            vname.textContent = file.name;
            setValue(fieldId, conf.url);
            var p = {}; p[fieldId] = conf.url;
            pushToPreview(p);
            scheduleSave();
          };
          xhr.onerror = function () {
            vname.textContent = "Upload failed — please try again.";
            vfile.value = "";
          };
          xhr.send(fd);
        });
      }
    });

    // Per-field Style panel — whole-element styling (colour/size/font/weight/
    // italic/align). Colours use a custom picker (swatch) rather than presets.
    // Selection-level styling is separate (the floating bubble on the preview).
    document.querySelectorAll("[data-style-panel]").forEach(function (panel) {
      var fieldId = panel.getAttribute("data-style-panel");
      var current = getStyle(fieldId);

      function commit(prop, value) {
        setStyleProp(fieldId, prop, value);
        pushStyleToPreview(fieldId);
        scheduleSave();
      }

      var color = panel.querySelector("[data-style-color]");
      if (color) {
        if (current.color) color.value = current.color;
        color.addEventListener("input", function () { commit("color", color.value); });
      }
      var bg = panel.querySelector("[data-style-bgcolor]");
      if (bg) {
        if (current.bgColor) bg.value = current.bgColor;
        bg.addEventListener("input", function () { commit("bgColor", bg.value); });
      }
      panel.querySelectorAll("[data-style-clear]").forEach(function (btn) {
        btn.addEventListener("click", function () { commit(btn.getAttribute("data-style-clear"), ""); });
      });

      var size = panel.querySelector("[data-style-sizeselect]");
      if (size) {
        buildSizeSelect(size, CMS_SIZES, current.fontSize);
        size.addEventListener("change", function () { commit("fontSize", size.value); });
      }

      var fam = panel.querySelector("[data-style-fontselect]");
      if (fam) {
        buildFontSelect(fam, current.fontFamily);
        fam.addEventListener("change", function () { commit("fontFamily", fam.value); });
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

    // Bind global Design controls (fonts / size dropdowns + color swatches).
    cmsLoadEditorFonts();

    function commitGlobal(key, value) {
      if (value) content._global[key] = value; else delete content._global[key];
      pushGlobalToPreview();
      scheduleSave();
    }

    document.querySelectorAll("[data-global-fontselect]").forEach(function (sel) {
      var key = sel.getAttribute("data-global-bind");
      buildFontSelect(sel, content._global[key]);
      sel.addEventListener("change", function () { commitGlobal(key, sel.value); });
    });
    document.querySelectorAll("[data-global-sizeselect]").forEach(function (sel) {
      var key = sel.getAttribute("data-global-bind");
      buildSizeSelect(sel, CMS_BASE_SIZES, content._global[key]);
      sel.addEventListener("change", function () { commitGlobal(key, sel.value); });
    });
    document.querySelectorAll("[data-global-swatches]").forEach(function (container) {
      var key = container.getAttribute("data-global-swatches");
      buildSwatches(container, content._global[key], function (c) { commitGlobal(key, c); });
    });

    // Theme colors — override the template's design tokens site-wide.
    document.querySelectorAll("[data-token-bind]").forEach(function (container) {
      var name = container.getAttribute("data-token-bind");
      var def = container.getAttribute("data-token-default") || "";
      var current = content._tokens[name] || def;
      buildSwatches(container, current, function (c) {
        if (c) content._tokens[name] = c; else delete content._tokens[name];
        pushTokensToPreview();
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

    // Narrow screens use explicit, predictable views instead of squeezing all
    // three editor columns into unusable slivers.
    var editorShell = document.getElementById("editor-shell");
    document.querySelectorAll("[data-editor-view]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var view = btn.getAttribute("data-editor-view");
        editorShell.setAttribute("data-mobile-view", view);
        document.querySelectorAll("[data-editor-view]").forEach(function (other) {
          var active = other === btn;
          other.classList.toggle("active", active);
          other.setAttribute("aria-selected", active ? "true" : "false");
        });
      });
    });

    var activeDialog = null;
    var dialogReturnFocus = null;
    function openDialog(dialog, trigger) {
      if (!dialog) return;
      dialogReturnFocus = trigger || document.activeElement;
      dialog.hidden = false;
      activeDialog = dialog;
      document.body.classList.add("has-modal");
      var first = dialog.querySelector("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
      if (first) first.focus();
    }
    function closeDialog(dialog) {
      if (!dialog) return;
      dialog.hidden = true;
      if (activeDialog === dialog) activeDialog = null;
      document.body.classList.remove("has-modal");
      if (dialogReturnFocus && dialogReturnFocus.focus) dialogReturnFocus.focus();
      dialogReturnFocus = null;
    }
    document.addEventListener("keydown", function (e) {
      if (!activeDialog) return;
      if (e.key === "Escape") { e.preventDefault(); closeDialog(activeDialog); return; }
      if (e.key !== "Tab") return;
      var focusable = Array.prototype.slice.call(activeDialog.querySelectorAll(
        "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
      )).filter(function (el) { return !el.hidden; });
      if (!focusable.length) return;
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

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
      openDialog(settingsModal, openBtn);
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
      closeDialog(settingsModal);
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

    // ---- version history modal ------------------------------------------
    var historyModal = document.getElementById("history-modal");
    var openHistoryBtn = document.getElementById("open-history-btn");
    var closeHistoryBtn = document.getElementById("close-history-btn");
    var cancelHistoryBtn = document.getElementById("cancel-history-btn");
    var versionList = document.getElementById("version-list");
    var historyStatus = document.getElementById("history-status");

    function closeHistory() { closeDialog(historyModal); }

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
      openDialog(historyModal, openHistoryBtn);
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

    // ---- media gallery modal --------------------------------------------
    var galleryModal = document.getElementById("gallery-modal");
    var openGalleryBtn = document.getElementById("open-gallery-btn");
    var closeGalleryBtn = document.getElementById("close-gallery-btn");
    var cancelGalleryBtn = document.getElementById("cancel-gallery-btn");
    var useGalleryBtn = document.getElementById("use-gallery-btn");
    var galleryGrid = document.getElementById("gallery-grid");
    var galleryStatus = document.getElementById("gallery-status");
    var galleryCount = document.getElementById("gallery-count");
    var galleryHint = document.getElementById("gallery-hint");
    var galleryDetail = document.getElementById("gallery-detail");
    var galleryDetailImg = document.getElementById("gallery-detail-img");
    var galleryDetailName = document.getElementById("gallery-detail-name");
    var galleryDetailNote = document.getElementById("gallery-detail-note");
    var galleryOpenBtn = document.getElementById("gallery-open-btn");
    var galleryCopyBtn = document.getElementById("gallery-copy-btn");
    var galleryDownloadBtn = document.getElementById("gallery-download-btn");
    var galleryRenameBtn = document.getElementById("gallery-rename-btn");
    var galleryDeleteBtn = document.getElementById("gallery-delete-btn");
    var gallerySelected = null;
    var galleryPickFieldId = null;
    var galleryAssets = [];
    var galleryRenaming = false;

    function mediaItemUrl(id) {
      var base = (window.CMS.galleryUrl || "").replace(/\/?$/, "/");
      return base + id + "/";
    }

    function hideGalleryDetail() {
      if (galleryDetail) galleryDetail.hidden = true;
      galleryRenaming = false;
      if (galleryDetailName) {
        galleryDetailName.readOnly = true;
        galleryDetailName.value = "";
      }
    }

    function closeGallery() {
      closeDialog(galleryModal);
      gallerySelected = null;
      galleryPickFieldId = null;
      galleryAssets = [];
      if (useGalleryBtn) useGalleryBtn.hidden = true;
      hideGalleryDetail();
    }

    function fmtBytes(n) {
      if (!n || n < 1024) return (n || 0) + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
      return (n / (1024 * 1024)).toFixed(1) + " MB";
    }

    function applyGalleryImage(url, name) {
      if (!galleryPickFieldId) return;
      var fieldId = galleryPickFieldId;
      var node = document.querySelector('[data-field-id="' + fieldId + '"]');
      if (node) {
        var img = node.querySelector("[data-bind-image]");
        var nameEl = node.querySelector("[data-bind-image-name]");
        if (img) img.src = url;
        if (nameEl) nameEl.textContent = name || "Gallery image";
      }
      setValue(fieldId, url);
      var p = {}; p[fieldId] = url;
      pushToPreview(p);
      scheduleSave();
      closeGallery();
    }

    function showGalleryDetail(item) {
      if (!galleryDetail || !item) {
        hideGalleryDetail();
        return;
      }
      galleryRenaming = false;
      galleryDetail.hidden = false;
      if (galleryDetailImg) {
        galleryDetailImg.hidden = false;
        galleryDetailImg.src = item.url;
        galleryDetailImg.alt = item.name || "";
      }
      if (galleryDetailName) {
        galleryDetailName.value = item.name || "";
        galleryDetailName.readOnly = true;
      }
      var editable = !!item.editable;
      if (galleryRenameBtn) {
        galleryRenameBtn.hidden = !editable;
        galleryRenameBtn.textContent = "Rename";
      }
      if (galleryDeleteBtn) galleryDeleteBtn.hidden = !editable;
      if (galleryDetailNote) {
        if (editable) {
          galleryDetailNote.textContent = item.bytes
            ? "Your upload · " + fmtBytes(item.bytes)
            : "Your upload";
        } else {
          galleryDetailNote.textContent =
            "Default page image — you can open, copy, or download it, but not rename or delete it.";
        }
      }
      if (useGalleryBtn) useGalleryBtn.hidden = !galleryPickFieldId;
    }

    function selectGalleryItem(item, el) {
      gallerySelected = item;
      if (galleryGrid) {
        galleryGrid.querySelectorAll(".gallery-item").forEach(function (n) {
          n.classList.toggle("is-selected", n === el);
        });
      }
      showGalleryDetail(item);
    }

    function renderGallery(assets) {
      galleryAssets = assets || [];
      if (!galleryGrid) return;
      galleryGrid.innerHTML = "";
      hideGalleryDetail();
      gallerySelected = null;
      if (useGalleryBtn) useGalleryBtn.hidden = true;
      if (!galleryAssets.length) {
        var empty = document.createElement("div");
        empty.className = "gallery-empty";
        empty.textContent = "No images yet. Images from this site's pages and any uploads will show up here.";
        galleryGrid.appendChild(empty);
        if (galleryCount) galleryCount.textContent = "";
        return;
      }
      if (galleryCount) {
        galleryCount.textContent =
          galleryAssets.length + " image" + (galleryAssets.length === 1 ? "" : "s");
      }
      galleryAssets.forEach(function (asset) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "gallery-item";
        btn.title = (asset.name || "Image") + (asset.bytes ? " · " + fmtBytes(asset.bytes) : "");
        if (!asset.editable) {
          var badge = document.createElement("span");
          badge.className = "gallery-item-badge";
          badge.textContent = "Default";
          btn.appendChild(badge);
        }
        var img = document.createElement("img");
        img.src = asset.url;
        img.alt = asset.name || "";
        img.loading = "lazy";
        var meta = document.createElement("span");
        meta.className = "gallery-item-meta";
        meta.textContent = asset.name || "Image";
        btn.appendChild(img);
        btn.appendChild(meta);
        btn.addEventListener("click", function () {
          selectGalleryItem(asset, btn);
        });
        btn.addEventListener("dblclick", function () {
          if (galleryPickFieldId) {
            applyGalleryImage(asset.url, asset.name);
          } else {
            window.open(asset.url, "_blank", "noopener");
          }
        });
        galleryGrid.appendChild(btn);
      });
    }

    function reloadGallery() {
      if (!window.CMS.galleryUrl) return;
      if (galleryStatus) galleryStatus.textContent = "Loading…";
      return fetch(window.CMS.galleryUrl, {
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (galleryStatus) galleryStatus.textContent = "";
          renderGallery((d && d.assets) || []);
        })
        .catch(function () {
          if (galleryStatus) galleryStatus.textContent = "Couldn't load gallery.";
        });
    }

    function openGallery(pickFieldId) {
      if (!galleryModal || !window.CMS.galleryUrl) return;
      galleryPickFieldId = pickFieldId || null;
      gallerySelected = null;
      if (useGalleryBtn) useGalleryBtn.hidden = true;
      hideGalleryDetail();
      if (galleryHint) {
        galleryHint.textContent = galleryPickFieldId
          ? "Select an image, then click Use — or double-click to apply it."
          : "Click an image for details. Default page images can be opened, copied, or downloaded — only your uploads can be renamed or deleted.";
      }
      openDialog(galleryModal, galleryPickFieldId ? document.querySelector('[data-gallery-pick="' + galleryPickFieldId + '"]') : openGalleryBtn);
      if (galleryGrid) galleryGrid.innerHTML = "";
      if (galleryCount) galleryCount.textContent = "";
      reloadGallery();
    }

    function copyGalleryUrl() {
      if (!gallerySelected) return;
      var url = gallerySelected.url;
      function ok() {
        if (galleryStatus) {
          galleryStatus.textContent = "URL copied.";
          setTimeout(function () {
            if (galleryStatus && galleryStatus.textContent === "URL copied.") {
              galleryStatus.textContent = "";
            }
          }, 1500);
        }
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(ok).catch(function () {
          window.prompt("Copy this URL:", url);
        });
      } else {
        window.prompt("Copy this URL:", url);
        ok();
      }
    }

    function downloadGalleryImage() {
      if (!gallerySelected) return;
      var url = gallerySelected.url;
      var name = gallerySelected.name || "image";
      // Prefer a real download attribute via blob fetch; fall back to new tab.
      fetch(url, { mode: "cors" })
        .then(function (r) {
          if (!r.ok) throw new Error("fetch failed");
          return r.blob();
        })
        .then(function (blob) {
          var a = document.createElement("a");
          var obj = URL.createObjectURL(blob);
          a.href = obj;
          a.download = name;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(function () { URL.revokeObjectURL(obj); }, 1000);
        })
        .catch(function () {
          window.open(url, "_blank", "noopener");
        });
    }

    function startRename() {
      if (!gallerySelected || !gallerySelected.editable || !galleryDetailName) return;
      galleryRenaming = true;
      galleryDetailName.readOnly = false;
      galleryDetailName.focus();
      galleryDetailName.select();
      if (galleryRenameBtn) galleryRenameBtn.textContent = "Save name";
    }

    function saveRename() {
      if (!gallerySelected || !gallerySelected.editable || !galleryDetailName) return;
      var name = (galleryDetailName.value || "").trim();
      if (!name) {
        if (galleryStatus) galleryStatus.textContent = "Name is required.";
        return;
      }
      if (galleryRenameBtn) galleryRenameBtn.disabled = true;
      if (galleryStatus) galleryStatus.textContent = "Saving…";
      fetch(mediaItemUrl(gallerySelected.id), {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": window.CMS.csrfToken,
        },
        body: JSON.stringify({ name: name }),
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (galleryRenameBtn) galleryRenameBtn.disabled = false;
          if (!res.ok || !res.data.ok) {
            if (galleryStatus) {
              galleryStatus.textContent = (res.data && res.data.error) || "Rename failed.";
            }
            return;
          }
          gallerySelected.name = res.data.name;
          galleryAssets.forEach(function (a) {
            if (a.id === gallerySelected.id) a.name = res.data.name;
          });
          galleryRenaming = false;
          galleryDetailName.readOnly = true;
          galleryDetailName.value = res.data.name;
          if (galleryRenameBtn) galleryRenameBtn.textContent = "Rename";
          if (galleryStatus) galleryStatus.textContent = "Renamed.";
          var selectedBtn = galleryGrid && galleryGrid.querySelector(".gallery-item.is-selected");
          if (selectedBtn) {
            var meta = selectedBtn.querySelector(".gallery-item-meta");
            if (meta) meta.textContent = res.data.name;
            selectedBtn.title = res.data.name + (gallerySelected.bytes ? " · " + fmtBytes(gallerySelected.bytes) : "");
          }
          if (galleryDetailNote) {
            galleryDetailNote.textContent = gallerySelected.bytes
              ? "Your upload · " + fmtBytes(gallerySelected.bytes)
              : "Your upload";
          }
        })
        .catch(function () {
          if (galleryRenameBtn) galleryRenameBtn.disabled = false;
          if (galleryStatus) galleryStatus.textContent = "Rename failed — try again.";
        });
    }

    function deleteGalleryImage() {
      if (!gallerySelected || !gallerySelected.editable) return;
      if (!window.confirm("Delete this upload from your gallery? Fields using it will fall back to the default image.")) {
        return;
      }
      if (galleryDeleteBtn) galleryDeleteBtn.disabled = true;
      if (galleryStatus) galleryStatus.textContent = "Deleting…";
      fetch(mediaItemUrl(gallerySelected.id), {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (galleryDeleteBtn) galleryDeleteBtn.disabled = false;
          if (!res.ok || !res.data.ok) {
            if (galleryStatus) {
              galleryStatus.textContent = (res.data && res.data.error) || "Delete failed.";
            }
            return;
          }
          if (galleryStatus) galleryStatus.textContent = "Deleted.";
          reloadGallery();
        })
        .catch(function () {
          if (galleryDeleteBtn) galleryDeleteBtn.disabled = false;
          if (galleryStatus) galleryStatus.textContent = "Delete failed — try again.";
        });
    }

    if (openGalleryBtn) {
      openGalleryBtn.addEventListener("click", function () { openGallery(null); });
    }
    if (closeGalleryBtn) closeGalleryBtn.addEventListener("click", closeGallery);
    if (cancelGalleryBtn) cancelGalleryBtn.addEventListener("click", closeGallery);
    if (useGalleryBtn) {
      useGalleryBtn.addEventListener("click", function () {
        if (gallerySelected) applyGalleryImage(gallerySelected.url, gallerySelected.name);
      });
    }
    if (galleryOpenBtn) {
      galleryOpenBtn.addEventListener("click", function () {
        if (gallerySelected) window.open(gallerySelected.url, "_blank", "noopener");
      });
    }
    if (galleryCopyBtn) galleryCopyBtn.addEventListener("click", copyGalleryUrl);
    if (galleryDownloadBtn) galleryDownloadBtn.addEventListener("click", downloadGalleryImage);
    if (galleryRenameBtn) {
      galleryRenameBtn.addEventListener("click", function () {
        if (galleryRenaming) saveRename();
        else startRename();
      });
    }
    if (galleryDetailName) {
      galleryDetailName.addEventListener("keydown", function (e) {
        if (!galleryRenaming) return;
        if (e.key === "Enter") {
          e.preventDefault();
          saveRename();
        } else if (e.key === "Escape") {
          e.preventDefault();
          galleryRenaming = false;
          galleryDetailName.value = gallerySelected ? (gallerySelected.name || "") : "";
          galleryDetailName.readOnly = true;
          if (galleryRenameBtn) galleryRenameBtn.textContent = "Rename";
        }
      });
    }
    if (galleryDeleteBtn) galleryDeleteBtn.addEventListener("click", deleteGalleryImage);
    if (galleryModal) {
      galleryModal.addEventListener("click", function (e) {
        if (e.target === galleryModal) closeGallery();
      });
    }
    document.querySelectorAll("[data-gallery-pick]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openGallery(btn.getAttribute("data-gallery-pick"));
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
