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

  // ---- block-instance mode --------------------------------------------
  // A block-shell page stores an ordered list of block instances under
  // content.regions[REGION]. Each instance = { id, type, fields:{...} }.
  // Instance field ids are "<instanceId>.<field>"; chrome (nav/footer) stays
  // in the classic top-level content maps. All block logic is gated on BLOCKS
  // so classic pages behave exactly as before.
  var BLOCKS = !!(window.CMS && window.CMS.blocksMode);
  var REGION = (window.CMS && window.CMS.regionName) || "main";
  var SHELL_REGIONS = (window.CMS && window.CMS.shellRegions) || [REGION];
  if (!Array.isArray(SHELL_REGIONS) || !SHELL_REGIONS.length) SHELL_REGIONS = [REGION];
  var CHROME_DEST = {
    header: "header-center", nav: "header-center", footer: "footer-center",
  };
  var HEADER_BLOCKS = {
    "header-left": [],
    "header-center": ["nav-link"],
    "header-right": ["button"],
    header: ["button"],
    nav: ["nav-link"],
  };
  var HEADER_BUTTON_CAP = 2;
  var HEADER_PAGES = (window.CMS && window.CMS.headerPages) || [];
  var BLOCK_DEFAULTS = (window.CMS && window.CMS.blockDefaults) || {};
  var PALETTE = (window.CMS && window.CMS.palette) || [];
  var MAX_BLOCKS = 40;
  var MAX_DEPTH = (window.CMS && window.CMS.maxBlockDepth) || 2;
  var structuralReload = false;
  var skipLayerClick = false;
  // Drag state shared between the palette cards and the form-panel drop zones.
  // { kind: "new", type } when dragging a palette card; { kind: "move", id }
  // when reordering an existing section.
  var dragPayload = null;
  // Set by initPalette; opens the Quick Add drawer, optionally targeting a
  // preset destination path ("main" or "<instanceId>/<column>").
  var openPalette = null;
  // Applied to the next addBlock() as content._styles["<id>.__block"].
  var pendingBlockStyle = null;
  // Palette metadata by block key (column slot names, is-layout).
  var PALETTE_BY_KEY = {};
  PALETTE.forEach(function (p) { PALETTE_BY_KEY[p.key] = p; });
  if (BLOCKS) {
    if (!content.regions || typeof content.regions !== "object") content.regions = {};
    if ((content.regions.header || []).length && !(content.regions["header-right"] || []).length) {
      content.regions["header-right"] = content.regions.header;
      content.regions.header = [];
    }
    if ((content.regions.footer || []).length && !(content.regions["footer-center"] || []).length) {
      content.regions["footer-center"] = content.regions.footer;
      content.regions.footer = [];
    }
    SHELL_REGIONS.forEach(function (name) {
      if (!Array.isArray(content.regions[name])) content.regions[name] = [];
    });
  }
  function allShellRegionNames() {
    var names = SHELL_REGIONS.slice();
    if (content.regions) {
      Object.keys(content.regions).forEach(function (n) {
        if (names.indexOf(n) === -1) names.push(n);
      });
    }
    return names;
  }
  function regionList(name) {
    var key = name || REGION;
    if (!BLOCKS || !content.regions) return [];
    if (!Array.isArray(content.regions[key])) content.regions[key] = [];
    return content.regions[key];
  }
  function walkAllInstances(cb) {
    var stop = false;
    allShellRegionNames().forEach(function (name) {
      if (stop) return;
      if (walkInstances(regionList(name), cb, 0, regionList(name), null, name)) stop = true;
    });
    return stop;
  }
  function shellRegionLabel(name) {
    if (name === "main") return "Page (bottom)";
    if (name === "header-left") return "Header · Logo";
    if (name === "header-center") return "Header · Menu";
    if (name === "header-right") return "Header · Buttons";
    if (name === "header" || name === "nav") return "Header · Menu";
    if (name === "footer-left") return "Footer · Left";
    if (name === "footer-center") return "Footer · Center";
    if (name === "footer-right") return "Footer · Right";
    if (name === "footer") return "Footer · Center";
    return regionLabel(name);
  }
  function resolveShellDest(name) {
    var names = allShellRegionNames();
    if (name === "header" || name === "nav") {
      if (names.indexOf("header-center") !== -1) return "header-center";
      if (names.indexOf("header-right") !== -1) return "header-right";
      if (names.indexOf("header") !== -1) return "header";
    }
    if (name === "footer") {
      if (names.indexOf("footer-center") !== -1) return "footer-center";
      if (names.indexOf("footer") !== -1) return "footer";
    }
    return name;
  }
  // Depth-first search over the whole instance tree. Returns the matching
  // instance, or null. Used by getValue/setValue so nested field ids resolve.
  function findInstance(id) {
    var found = null;
    walkAllInstances(function (inst) {
      if (inst.id === id) { found = inst; return true; }
      return false;
    });
    return found;
  }
  // Visit every instance (nested included). Callback returning true stops.
  function walkInstances(list, cb, depth, parentList, parentId, region) {
    depth = depth || 0;
    for (var i = 0; i < (list || []).length; i++) {
      var inst = list[i];
      if (cb(inst, { list: list, index: i, depth: depth, parentId: parentId || null, region: region || REGION })) {
        return true;
      }
      if (inst.children && typeof inst.children === "object") {
        var names = Object.keys(inst.children);
        for (var n = 0; n < names.length; n++) {
          if (walkInstances(inst.children[names[n]], cb, depth + 1, inst.children[names[n]], inst.id, names[n])) {
            return true;
          }
        }
      }
    }
    return false;
  }
  // Locate an instance and the list it lives in (for move/duplicate/delete).
  function findInstanceLoc(id) {
    var loc = null;
    walkAllInstances(function (inst, ctx) {
      if (inst.id === id) {
        loc = {
          inst: inst, list: ctx.list, index: ctx.index, depth: ctx.depth,
          parentId: ctx.parentId || null, region: ctx.region || REGION,
        };
        return true;
      }
      return false;
    });
    return loc;
  }
  function countAllInstances() {
    var total = 0;
    walkAllInstances(function () { total++; return false; });
    return total;
  }
  function syncCanvasStart() {
    var el = document.getElementById("cms-canvas-start");
    if (!el) return;
    el.hidden = countAllInstances() > 0;
  }
  function newInstanceId() {
    return "blk_" + Math.random().toString(16).slice(2, 10);
  }
  // Resolve a destination list from a path token: "main" (top-level region) or
  // "<instanceId>/<columnName>" for a row column. Returns the array or null.
  function destinationList(path) {
    if (!path) return regionList(REGION);
    var slash = path.indexOf("/");
    if (slash === -1) return regionList(resolveShellDest(path));
    var instId = path.slice(0, slash);
    var col = path.slice(slash + 1);
    var loc = findInstanceLoc(instId);
    if (!loc || !loc.inst.children) return null;
    if (!Array.isArray(loc.inst.children[col])) loc.inst.children[col] = [];
    return loc.inst.children[col];
  }
  function destRegionName(path) {
    if (!path || path.indexOf("/") !== -1) return "";
    return resolveShellDest(path);
  }
  function allowedTypesForDest(path) {
    var name = destRegionName(path);
    return Object.prototype.hasOwnProperty.call(HEADER_BLOCKS, name)
      ? HEADER_BLOCKS[name] : null;
  }
  function chromeAllows(type, destPath, movingId) {
    var allow = allowedTypesForDest(destPath);
    if (!allow) return true;
    if (allow.indexOf(type) === -1) {
      if (!allow.length) {
        window.alert("The logo stays on the left. Add menu links in the center, or a button on the right.");
      } else {
        window.alert("The header only takes menu links and buttons.");
      }
      return false;
    }
    if (type === "button" && destRegionName(destPath) === "header-right") {
      var list = destinationList(destPath) || [];
      var n = list.filter(function (inst) { return !movingId || inst.id !== movingId; }).length;
      if (n >= HEADER_BUTTON_CAP) {
        window.alert("The header holds up to two buttons.");
        return false;
      }
    }
    return true;
  }
  function addToDestOrPalette(destPath, presetCat) {
    destPath = destPath || defaultInsertDest();
    if (isHeaderDest(destPath)) {
      if (headerDestName(destPath) === "header-right") enableHeaderButton();
      else addHeaderLink();
      return;
    }
    var allow = allowedTypesForDest(destPath);
    if (allow && allow.length === 1) {
      addBlock(allow[0], destPath);
      return;
    }
    if (allow && allow.length === 0) {
      window.alert("The logo stays on the left. Add menu links in the center, or a button on the right.");
      return;
    }
    if (openPalette) openPalette(destPath, presetCat);
  }
  function newNavId() {
    return "nav_" + Math.random().toString(16).slice(2, 10);
  }
  function brandFieldId() {
    if (content.header && Object.prototype.hasOwnProperty.call(content.header, "brand")) {
      return "header.brand";
    }
    if (content.nav && Object.prototype.hasOwnProperty.call(content.nav, "brand")) {
      return "nav.brand";
    }
    return "header.brand";
  }
  function ensureHeader() {
    if (!content._header || typeof content._header !== "object") content._header = {};
    var h = content._header;
    if (h.layout !== "classic" && h.layout !== "packed" && h.layout !== "centered") {
      var place = h.place || {};
      if (place.brand === "center") h.layout = "centered";
      else if (place.nav === "right") h.layout = "packed";
      else h.layout = "classic";
    }
    if (!Array.isArray(h.menu)) h.menu = [];
    if (!h.menu.length && content.regions) {
      ["header-center", "header", "nav"].forEach(function (slot) {
        (content.regions[slot] || []).forEach(function (inst) {
          if (!inst || inst.type !== "nav-link") return;
          var f = inst.fields || {};
          h.menu.push({
            id: newNavId(),
            label: String(f.text || "Link").slice(0, 80) || "Link",
            href: f.text_href || f.href || "/",
            page_id: null,
          });
        });
      });
    }
    if (typeof h.logo !== "string") h.logo = "";
    var size = parseInt(h.logo_size, 10);
    if (!(size >= 24 && size <= 80)) h.logo_size = 40;
    else h.logo_size = size;
    if (typeof h.show_name !== "boolean") h.show_name = !h.logo;
    if (!h.button || typeof h.button !== "object") {
      h.button = { on: false, label: "Get Started", href: "#" };
      if (content.regions) {
        ["header-right", "header"].some(function (slot) {
          return (content.regions[slot] || []).some(function (inst) {
            if (!inst || inst.type !== "button") return false;
            var f = inst.fields || {};
            h.button = {
              on: true,
              label: String(f.label || "Get Started").slice(0, 80) || "Get Started",
              href: f.link || f.href || "#",
            };
            return true;
          });
        });
      }
    }
    return h;
  }
  function isHeaderSection(id) {
    var s = String(id || "").toLowerCase();
    return s === "header" || s === "nav";
  }
  function headerDestName(path) {
    return destRegionName(path || "");
  }
  function isHeaderDest(path) {
    var name = headerDestName(path);
    return name.indexOf("header") === 0 || name === "nav";
  }
  function saveAndRefreshHeader() {
    renderHeaderPanel();
    livePreviewReload(null);
  }
  function addHeaderLink(label, href) {
    var h = ensureHeader();
    h.menu.push({
      id: newNavId(),
      label: label || "New link",
      href: href || "/",
      page_id: null,
    });
    saveAndRefreshHeader();
  }
  function enableHeaderButton() {
    var h = ensureHeader();
    h.button.on = true;
    if (!h.button.label) h.button.label = "Get Started";
    if (!h.button.href) h.button.href = "#";
    saveAndRefreshHeader();
  }
  function setHeaderLogo(url) {
    var h = ensureHeader();
    var had = !!h.logo;
    h.logo = url || "";
    if (url && !had) h.show_name = false;
    if (!url) h.show_name = true;
    saveAndRefreshHeader();
  }
  function setHeaderLayout(layout) {
    if (["classic", "packed", "centered"].indexOf(layout) === -1) return;
    ensureHeader().layout = layout;
    saveAndRefreshHeader();
  }
  function setHeaderMenuLabel(id, html) {
    var h = ensureHeader();
    var item = h.menu.filter(function (row) { return row.id === id; })[0];
    if (!item) return;
    var tmp = document.createElement("div");
    tmp.innerHTML = html || "";
    item.label = (tmp.textContent || "").trim().slice(0, 80) || "Link";
    renderHeaderPanel();
    scheduleSave();
  }
  function pageOptionHtml(selectedId, selectedHref) {
    var html = "";
    HEADER_PAGES.forEach(function (page) {
      var pid = page.id == null ? "" : String(page.id);
      var sel = "";
      if (selectedId != null && String(selectedId) === pid) sel = " selected";
      else if (selectedId == null && !pid && (selectedHref === page.url || selectedHref === "/")) sel = " selected";
      html += '<option value="' + pid + '" data-url="' + String(page.url || "/").replace(/"/g, "") + '"' + sel + ">" +
        String(page.title || "Page").replace(/</g, "") + "</option>";
    });
    html += '<option value="__custom__"' + (selectedId == null && selectedHref && selectedHref !== "/" ? " selected" : "") + ">Custom URL</option>";
    return html;
  }
  function renderHeaderPanel() {
    var panel = document.getElementById("cms-header-panel");
    if (!panel) return;
    var h = ensureHeader();
    var logo = panel.querySelector("[data-header-logo]");
    if (logo && document.activeElement !== logo) logo.value = getValue(brandFieldId()) || "";
    var preview = panel.querySelector("[data-header-logo-preview]");
    var status = panel.querySelector("[data-header-logo-status]");
    var clearBtn = panel.querySelector("[data-header-logo-clear]");
    if (preview) {
      preview.hidden = !h.logo;
      if (h.logo) preview.src = h.logo;
    }
    if (status) {
      status.textContent = h.logo
        ? (h.show_name
          ? "Logo and site name both show in the header."
          : "The logo is showing. Turn on site name to place the word next to it.")
        : "Upload a logo image, or use the site name in the header.";
    }
    var showName = panel.querySelector("[data-header-show-name]");
    var nameRow = panel.querySelector("[data-header-name-row]");
    if (showName) showName.checked = !!h.show_name;
    if (nameRow) nameRow.hidden = !h.show_name;
    if (clearBtn) clearBtn.hidden = !h.logo;
    var sizeRow = panel.querySelector("[data-header-logo-size-row]");
    var sizeInput = panel.querySelector("[data-header-logo-size]");
    var sizeLabel = panel.querySelector("[data-header-logo-size-label]");
    if (sizeRow) sizeRow.hidden = !h.logo;
    if (sizeInput && document.activeElement !== sizeInput) sizeInput.value = String(h.logo_size || 40);
    if (sizeLabel) sizeLabel.textContent = (h.logo_size || 40) + "px";
    if (preview && h.logo) preview.style.height = (h.logo_size || 40) + "px";
    panel.querySelectorAll("[data-header-layout]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-header-layout") === h.layout);
    });
    var list = panel.querySelector("[data-header-menu]");
    if (list) {
      list.innerHTML = h.menu.map(function (item, index) {
        var custom = item.page_id == null && item.href && item.href !== "/";
        return (
          '<div class="cms-header-menu-row" data-header-row="' + item.id + '">' +
            '<input type="text" class="input" data-header-label maxlength="80" value="">' +
            '<select class="select" data-header-page>' + pageOptionHtml(item.page_id, item.href) + "</select>" +
            '<input type="text" class="input" data-header-href maxlength="500"' + (custom ? "" : " hidden") + ">" +
            '<div class="cms-header-menu-ops">' +
              '<button type="button" data-header-move="-1" title="Move up" aria-label="Move up"' + (index === 0 ? " disabled" : "") + ">↑</button>" +
              '<button type="button" data-header-move="1" title="Move down" aria-label="Move down"' + (index === h.menu.length - 1 ? " disabled" : "") + ">↓</button>" +
              '<button type="button" data-header-del title="Remove" aria-label="Remove">×</button>' +
            "</div>" +
          "</div>"
        );
      }).join("");
      h.menu.forEach(function (item) {
        var row = list.querySelector('[data-header-row="' + item.id + '"]');
        if (!row) return;
        row.querySelector("[data-header-label]").value = item.label || "";
        row.querySelector("[data-header-href]").value = item.href || "/";
      });
    }
    var on = panel.querySelector("[data-header-btn-on]");
    var fields = panel.querySelector("[data-header-btn-fields]");
    var lab = panel.querySelector("[data-header-btn-label]");
    var href = panel.querySelector("[data-header-btn-href]");
    if (on) on.checked = !!h.button.on;
    if (fields) fields.hidden = !h.button.on;
    if (lab && document.activeElement !== lab) lab.value = h.button.label || "";
    if (href && document.activeElement !== href) href.value = h.button.href || "";
  }
  var headerPanelBound = false;
  function bindHeaderPanel() {
    var panel = document.getElementById("cms-header-panel");
    if (!panel || headerPanelBound) return;
    headerPanelBound = true;
    panel.addEventListener("input", function (e) {
      var h = ensureHeader();
      var t = e.target;
      if (t.hasAttribute("data-header-logo-size")) {
        var px = parseInt(t.value, 10);
        if (!(px >= 24 && px <= 80)) px = 40;
        h.logo_size = px;
        var sizeLabelLive = panel.querySelector("[data-header-logo-size-label]");
        if (sizeLabelLive) sizeLabelLive.textContent = px + "px";
        if (previewReady) {
          previewFrame.contentWindow.postMessage(
            { source: "cms-editor", type: "header-logo-size", payload: { size: px } },
            PREVIEW_ORIGIN
          );
        }
        scheduleSave();
        return;
      }
      if (t.hasAttribute("data-header-logo")) {
        setValue(brandFieldId(), t.value);
        if (!ensureHeader().logo) {
          var patch = {};
          patch[brandFieldId()] = t.value;
          pushToPreview(patch);
        }
        scheduleSave();
        return;
      }
      if (t.hasAttribute("data-header-btn-label")) {
        h.button.label = t.value.slice(0, 80) || "Get Started";
        scheduleSave();
        return;
      }
      if (t.hasAttribute("data-header-btn-href")) {
        h.button.href = t.value.slice(0, 500) || "#";
        scheduleSave();
        return;
      }
      var row = t.closest("[data-header-row]");
      if (!row) return;
      var item = h.menu.filter(function (rowItem) { return rowItem.id === row.getAttribute("data-header-row"); })[0];
      if (!item) return;
      if (t.hasAttribute("data-header-label")) item.label = t.value.slice(0, 80) || "Link";
      if (t.hasAttribute("data-header-href")) item.href = t.value.slice(0, 500) || "/";
      scheduleSave();
    });
    panel.addEventListener("change", function (e) {
      var h = ensureHeader();
      var t = e.target;
      if (t.hasAttribute("data-header-show-name")) {
        h.show_name = !!t.checked;
        saveAndRefreshHeader();
        return;
      }
      if (t.hasAttribute("data-header-btn-on")) {
        h.button.on = !!t.checked;
        saveAndRefreshHeader();
        return;
      }
      var row = t.closest("[data-header-row]");
      if (!row || !t.hasAttribute("data-header-page")) return;
      var item = h.menu.filter(function (rowItem) { return rowItem.id === row.getAttribute("data-header-row"); })[0];
      if (!item) return;
      if (t.value === "__custom__") {
        item.page_id = null;
        renderHeaderPanel();
        return;
      }
      var opt = t.selectedOptions && t.selectedOptions[0];
      item.page_id = t.value === "" ? null : parseInt(t.value, 10);
      if (isNaN(item.page_id)) item.page_id = null;
      item.href = (opt && opt.getAttribute("data-url")) || "/";
      saveAndRefreshHeader();
    });
    panel.addEventListener("click", function (e) {
      if (e.target.closest("[data-header-logo-gallery]")) {
        if (openContentGallery) openContentGallery("header-logo");
        return;
      }
      if (e.target.closest("[data-header-logo-upload]")) {
        var file = panel.querySelector("[data-header-logo-file]");
        if (file) file.click();
        return;
      }
      if (e.target.closest("[data-header-logo-clear]")) {
        setHeaderLogo("");
        return;
      }
      var layoutBtn = e.target.closest("[data-header-layout]");
      if (layoutBtn) {
        setHeaderLayout(layoutBtn.getAttribute("data-header-layout"));
        return;
      }
      if (e.target.closest("[data-header-add-link]")) {
        addHeaderLink();
        return;
      }
      var row = e.target.closest("[data-header-row]");
      if (!row) return;
      var h = ensureHeader();
      var id = row.getAttribute("data-header-row");
      var idx = -1;
      h.menu.forEach(function (item, i) { if (item.id === id) idx = i; });
      if (idx < 0) return;
      if (e.target.closest("[data-header-del]")) {
        h.menu.splice(idx, 1);
        saveAndRefreshHeader();
        return;
      }
      var move = e.target.closest("[data-header-move]");
      if (move) {
        var dir = parseInt(move.getAttribute("data-header-move"), 10);
        var next = idx + dir;
        if (next < 0 || next >= h.menu.length) return;
        var tmp = h.menu[idx];
        h.menu[idx] = h.menu[next];
        h.menu[next] = tmp;
        saveAndRefreshHeader();
      }
    });
    var logoFile = panel.querySelector("[data-header-logo-file]");
    if (logoFile) {
      logoFile.addEventListener("change", function () {
        var chosen = logoFile.files && logoFile.files[0];
        logoFile.value = "";
        if (!chosen || !window.CMS.uploadUrl) return;
        var status = panel.querySelector("[data-header-logo-status]");
        if (status) status.textContent = "Uploading…";
        var fd = new FormData();
        fd.append("file", chosen);
        fetch(window.CMS.uploadUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRFToken": window.CMS.csrfToken },
          body: fd,
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            if (!res.ok || !res.data.ok) {
              if (status) status.textContent = (res.data && res.data.error) || "Upload failed.";
              return;
            }
            setHeaderLogo(res.data.url);
          })
          .catch(function () { if (status) status.textContent = "Upload failed — try again."; });
      });
    }
  }
  if (BLOCKS) ensureHeader();
  // Depth of a destination list ("main" = 0; a row's column = row depth + 1).
  function destinationDepth(path) {
    if (!path || path.indexOf("/") === -1) return 0;
    var instId = path.slice(0, path.indexOf("/"));
    var loc = findInstanceLoc(instId);
    return loc ? loc.depth + 1 : 0;
  }

  var saveTimer = null;
  var saveInFlight = false;
  var saveQueued = false;
  var hasUnsavedChanges = false;
  // Lets undo abort an in-flight save so it can't re-persist the state the user
  // is trying to discard (E4/E5).
  var saveAbort = null;
  var saveDot = document.getElementById("save-dot");
  var saveText = document.getElementById("save-text");
  var saveRetry = document.getElementById("save-retry");
  var previewFrame = document.getElementById("preview-frame");
  // Preview is same-origin with the dashboard. Pin the target so a stolen
  // postMessage can't be delivered to an unexpected origin (E9).
  var PREVIEW_ORIGIN = (typeof location !== "undefined" && location.origin)
    ? location.origin : "*";
  var previewReady = false;
  var previewLoading = document.getElementById("preview-loading");
  var previewLoadingTitle = document.getElementById("preview-loading-title");
  var previewLoadingCopy = document.getElementById("preview-loading-copy");
  var previewRetry = document.getElementById("preview-retry");
  var previewTimeout = null;
  var previewReloadAfterSave = false;
  // Set by a live reorder: after the iframe-only preview reload signals ready,
  // restore the canvas scroll (y) and re-frame the moved block (id).
  var previewResync = null;
  var ghlFormsRequest = null;

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

  function cmsIsBlankHtml(value) {
    var s = String(value == null ? "" : value);
    s = s.replace(/&nbsp;|&#160;|\u00a0/gi, " ");
    s = s.replace(/<br\s*\/?>/gi, "");
    s = s.replace(/<[^>]+>/g, "");
    return !s.replace(/\s+/g, "");
  }

  function getValue(fieldId) {
    var idx = fieldId.indexOf(".");
    var sec = fieldId.slice(0, idx), fld = fieldId.slice(idx + 1);
    if (BLOCKS) {
      var inst = findInstance(sec);
      if (inst) {
        var stored = inst.fields && inst.fields[fld];
        // Empty / <br>-only contenteditable leftovers mean "use the designed
        // default". The properties panel hydrates from CMS.blockDefaults so
        // unedited fields show the designed copy instead of a blank box.
        if (stored !== undefined && stored !== null && !cmsIsBlankHtml(stored)) {
          return stored;
        }
        return (BLOCK_DEFAULTS[inst.type] || {})[fld];
      }
    }
    return (content[sec] || {})[fld];
  }
  function setValue(fieldId, value) {
    var idx = fieldId.indexOf(".");
    var sec = fieldId.slice(0, idx), fld = fieldId.slice(idx + 1);
    if (BLOCKS) {
      var inst = findInstance(sec);
      if (inst) {
        if (!inst.fields) inst.fields = {};
        if (cmsIsBlankHtml(value)) {
          delete inst.fields[fld];
          return;
        }
        inst.fields[fld] = value;
        return;
      }
    }
    if (!content[sec]) content[sec] = {};
    content[sec][fld] = value;
  }

  function setStatus(state, message) {
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
      saveText.textContent = message || (navigator.onLine
        ? "Changes were not saved. Try again."
        : "You’re offline. Changes will retry when you reconnect.");
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
    saveAbort = (typeof AbortController !== "undefined") ? new AbortController() : null;
    fetch(window.CMS.saveUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": window.CMS.csrfToken,
      },
      body: JSON.stringify({ content: JSON.parse(snapshot) }),
      signal: saveAbort ? saveAbort.signal : undefined,
    })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          if (!r.ok) return Promise.reject({ status: r.status, data: data });
          return data;
        });
      })
      .then(function () {
        saveInFlight = false;
        if (saveQueued || JSON.stringify(content) !== snapshot) {
          saveQueued = false;
          save();
          return;
        }
        hasUnsavedChanges = false;
        setStatus("saved");
        if (structuralReload) {
          // A few structural ops still need a full editor reload (empty-page
          // zero-state, header chrome). Stash the canvas scroll so we land
          // back where we were, not at the top.
          structuralReload = false;
          stashPreviewScroll();
          window.location.reload();
          return;
        }
        if (previewReloadAfterSave) {
          previewReloadAfterSave = false;
          reloadPreview();
        }
      })
      .catch(function (error) {
        saveInFlight = false;
        // A deliberate abort (undo is discarding this state) is not an error —
        // leave status alone; the undo flow reloads the page next.
        if (error && error.name === "AbortError") { structuralReload = false; return; }
        hasUnsavedChanges = true;
        // A structural op that failed to save must NOT leave the reload flag
        // armed: otherwise the next (possibly unrelated) successful save would
        // trigger a surprise full-page reload and lose the user's place (E10).
        structuralReload = false;
        if (error && (error.status === 401 || error.status === 403)) {
          setStatus("error", "Your session expired. Sign in again, then retry the save.");
        } else if (error && error.status === 429) {
          setStatus("error", "Too many save attempts. Wait a moment, then try again.");
        } else if (error && error.status >= 500) {
          setStatus("error", "The server could not save your changes. Try again.");
        } else {
          setStatus("error", error && error.data && error.data.error);
        }
      });
  }

  if (saveRetry) saveRetry.addEventListener("click", save);
  window.addEventListener("online", function () { if (hasUnsavedChanges) save(); });
  window.addEventListener("beforeunload", function (e) {
    if (!hasUnsavedChanges || (window.CMS && window.CMS.readOnly)) return;
    e.preventDefault();
    e.returnValue = "";
  });

  // Flush any pending/in-flight save, then run onOk — or onFail if the save
  // ultimately errored. Used before publish (a full POST+redirect) so a
  // debounced edit can't be silently dropped by the navigation.
  function flushSaveThen(onOk, onFail) {
    if (window.CMS && window.CMS.readOnly) { onOk(); return; }
    if (!hasUnsavedChanges && !saveInFlight) { onOk(); return; }
    clearTimeout(saveTimer);
    if (!saveInFlight) save();
    var tries = 0;
    var timer = setInterval(function () {
      if (!hasUnsavedChanges && !saveInFlight) { clearInterval(timer); onOk(); return; }
      // Save settled but the change is still dirty => it errored.
      if (!saveInFlight && hasUnsavedChanges) { clearInterval(timer); (onFail || onOk)(); return; }
      if (tries++ > 60) { clearInterval(timer); (onFail || onOk)(); return; } // ~6s guard
    }, 100);
  }

  // Publish / unpublish: confirm the effect in plain language (this is where a
  // client learns that Live = instantly public), and never publish on top of an
  // unsaved edit. form.submit() (below) does not re-fire this handler.
  var publishForm = document.querySelector(".editor-publish-form");
  if (publishForm) {
    publishForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var action = publishForm.getAttribute("data-publish-action");
      var title = publishForm.getAttribute("data-target-title") || "this page";
      var msg = action === "unpublish"
        ? "Unpublish \u201c" + title + "\u201d?\n\nVisitors will no longer be able to see it \u2014 they'll get a \u201cnot found\u201d page. You can publish it again anytime."
        : "Publish \u201c" + title + "\u201d?\n\nIt will be visible to anyone who visits your site, including the changes you've saved. From now on, saved edits stay live.";
      if (!window.confirm(msg)) return;
      flushSaveThen(
        function () { publishForm.submit(); },
        function () {
          window.alert("Your most recent changes couldn't be saved, so publishing was cancelled to avoid losing them. Check the save status at the top, then try again.");
        }
      );
    });
  }

  // One automatic reload attempt before we surface the scary error — most
  // "didn't load" cases are a transient handshake miss that a real reload fixes.
  var previewAutoRetried = false;

  function setPreviewState(state) {
    if (!previewLoading) return;
    clearTimeout(previewTimeout);
    if (state === "ready") {
      previewAutoRetried = false;
      previewLoading.hidden = true;
      return;
    }
    previewLoading.hidden = false;
    if (previewRetry) previewRetry.hidden = state !== "error";
    if (previewLoadingTitle) previewLoadingTitle.textContent = state === "error" ? "Preview didn’t load" : "Loading preview…";
    if (previewLoadingCopy) previewLoadingCopy.textContent = state === "error" ? "Your edits are safe. Reload just the preview to try again." : "Preparing the latest version of this page.";
    if (state === "loading") {
      previewTimeout = setTimeout(function () {
        if (previewReady) return;
        if (!previewAutoRetried) { previewAutoRetried = true; reloadPreview(); }
        else setPreviewState("error");
      }, 12000);
    }
  }

  // Ask the (same-origin) preview to confirm it's ready. Covers the race where
  // the iframe posted 'ready' before this window's listener was attached.
  function pingPreview() {
    try {
      previewFrame.contentWindow.postMessage(
        { source: "cms-editor", type: "ping" }, PREVIEW_ORIGIN);
    } catch (e) {}
  }

  function reloadPreview() {
    previewReady = false;
    setPreviewState("loading");
    // A fragment-only src change does NOT reload an iframe — that left the
    // preview stuck "loading" forever. Force a real document reload instead.
    try {
      previewFrame.contentWindow.location.reload();
    } catch (e) {
      var base = previewFrame.getAttribute("src").split("#")[0].split("?")[0];
      previewFrame.setAttribute("src", base + "?r=" + Date.now());
    }
  }
  if (previewRetry) previewRetry.addEventListener("click", reloadPreview);

  // On every iframe load, briefly poll with pings until 'ready' arrives, so a
  // missed handshake recovers on its own instead of timing out.
  if (previewFrame) {
    previewFrame.addEventListener("load", function () {
      var tries = 0;
      var t = setInterval(function () {
        if (previewReady || tries++ > 10) { clearInterval(t); return; }
        pingPreview();
      }, 350);
    });
  }
  setPreviewState("loading");

  // ---- preview bridge --------------------------------------------------
  function pushToPreview(patch) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-content", payload: patch },
      PREVIEW_ORIGIN
    );
  }

  function highlightInPreview(fieldId) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "highlight-field", payload: { id: fieldId } },
      PREVIEW_ORIGIN
    );
  }

  function scrollPreviewToSection(sectionId) {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "scroll-to-section", payload: { id: sectionId } },
      PREVIEW_ORIGIN
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

  // Auto-grow a textarea to fit its content so long field values are never
  // truncated (Stage B3). Safe no-op on non-textarea nodes.
  function cmsAutoGrow(el) {
    if (!el || el.tagName !== "TEXTAREA") return;
    el.style.height = "auto";
    el.style.height = (el.scrollHeight || 0) + "px";
  }

  function cmsCap(s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); }
  // Parse a field's sub-key into a repeated-group token: card2_name -> {base:"card",
  // index:2}. Returns null when the field isn't part of a numbered series.
  function cmsFieldGroupInfo(fieldId) {
    var sub = fieldId.indexOf(".") >= 0 ? fieldId.slice(fieldId.indexOf(".") + 1) : fieldId;
    var m = sub.match(/^([a-zA-Z]+?)[ _-]?(\d+)(?:[ _-].*)?$/);
    if (!m) return null;
    return { base: m[1].toLowerCase(), raw: m[1], index: parseInt(m[2], 10) };
  }
  // B1: fold repeated fields into collapsed groups (Card 1 / Card 2 …) and
  // consolidate their per-field Style panels into ONE disclosure per group.
  function groupSectionFields() {
    document.querySelectorAll(".editor-form-section").forEach(function (sec) {
      if (sec.getAttribute("data-grouped") === "1") return;
      var stack = sec.querySelector(".stack-5");
      if (!stack) return;
      var fields = Array.prototype.slice.call(stack.querySelectorAll(":scope > .field"));
      if (fields.length < 4) return;
      var order = [], groups = {}, baseIdx = {};
      fields.forEach(function (f) {
        var info = cmsFieldGroupInfo(f.getAttribute("data-field-id") || "");
        if (!info) return;
        var key = info.base + "#" + info.index;
        if (!groups[key]) {
          groups[key] = { label: cmsCap(info.raw) + " " + info.index, base: info.base, nodes: [] };
          order.push(key);
        }
        groups[key].nodes.push(f);
        (baseIdx[info.base] = baseIdx[info.base] || {})[info.index] = true;
      });
      var groupable = order.filter(function (k) {
        return Object.keys(baseIdx[groups[k].base]).length >= 2;
      });
      if (!groupable.length) return;
      sec.setAttribute("data-grouped", "1");
      groupable.forEach(function (k) {
        var g = groups[k];
        var det = document.createElement("details");
        det.className = "cms-fieldgroup";
        det.open = true;
        det.setAttribute("data-fieldgroup", k);
        det.innerHTML =
          '<summary class="cms-fieldgroup-summary">' +
            '<span class="cms-fieldgroup-title"></span>' +
            '<span class="cms-fieldgroup-count"></span>' +
            '<svg class="cms-fieldgroup-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>' +
          '</summary>';
        det.querySelector(".cms-fieldgroup-title").textContent = g.label;
        det.querySelector(".cms-fieldgroup-count").textContent = g.nodes.length;
        var body = document.createElement("div");
        body.className = "cms-fieldgroup-body";
        det.appendChild(body);
        var stylePanels = [];
        g.nodes.forEach(function (f) {
          var sp = f.querySelector(":scope > .cms-style-panel");
          if (sp) stylePanels.push({ field: f, panel: sp });
          body.appendChild(f);
        });
        if (stylePanels.length) {
          var gstyle = document.createElement("details");
          gstyle.className = "cms-group-style";
          gstyle.innerHTML =
            '<summary class="cms-style-summary">' +
              '<svg class="cms-style-ico" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>' +
              '<span>Style</span>' +
              '<svg class="cms-style-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>' +
            '</summary>';
          var gbody = document.createElement("div");
          gbody.className = "cms-group-style-body";
          stylePanels.forEach(function (item) {
            var lbl = item.field.querySelector(".field-label");
            var head = document.createElement("div");
            head.className = "cms-group-style-field";
            head.textContent = lbl ? (lbl.textContent || "").trim() : "";
            gbody.appendChild(head);
            item.panel.open = true;                 // controls always visible…
            item.panel.classList.add("cms-style-panel--flat"); // …under the one group toggle
            gbody.appendChild(item.panel);
          });
          gstyle.appendChild(gbody);
          body.appendChild(gstyle);
        }
        // Re-measure auto-grow textareas once the group is expanded (they were
        // hidden while collapsed, so scrollHeight read as 0).
        det.addEventListener("toggle", function () {
          if (det.open) det.querySelectorAll("textarea.cms-autogrow").forEach(cmsAutoGrow);
        });
        stack.appendChild(det);
      });
    });
  }

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
      { source: "cms-editor", type: "apply-styles", payload: p }, PREVIEW_ORIGIN);
  }
  function commitStyle(fieldId, prop, value) {
    setStyleProp(fieldId, prop, value);
    pushStyleToPreview(fieldId);
    scheduleSave();
  }
  function fieldBgMode(style) {
    if (style.bgMode === "image" || style.bgImage) return "image";
    return "color";
  }
  function syncFieldStyleBg(panel) {
    if (!panel) return;
    var current = getStyle(panel.getAttribute("data-style-panel") || "");
    var mode = fieldBgMode(current);
    panel.querySelectorAll("[data-style-bg-mode]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-style-bg-mode") === mode);
    });
    panel.querySelectorAll("[data-style-bg-pane]").forEach(function (pane) {
      pane.hidden = pane.getAttribute("data-style-bg-pane") !== mode;
    });
    var preview = panel.querySelector("[data-style-bg-preview]");
    if (preview) {
      if (current.bgImage) {
        preview.hidden = false;
        preview.style.backgroundImage = 'url("' + String(current.bgImage).replace(/["')(]/g, "") + '")';
      } else {
        preview.hidden = true;
        preview.style.backgroundImage = "";
      }
    }
    var swatch = panel.querySelector("[data-style-bgcolor]");
    if (swatch && current.bgColor) swatch.value = current.bgColor;
  }
  function syncBlockLayout(instId) {
    if (!drawerLayout) return;
    fillLayoutTargets();
    var styleId = layoutStyleId(instId);
    var boxStyle = getStyle(blockStyleId(instId));
    var current = getStyle(styleId);
    var mode = current.bgMode || (current.bgImage ? "image" : (current.bgGradient ? "gradient" : "color"));
    drawerLayout.querySelectorAll("[data-bg-mode]").forEach(function (tab) {
      tab.classList.toggle("is-active", tab.getAttribute("data-bg-mode") === mode);
    });
    drawerLayout.querySelectorAll("[data-bg-pane]").forEach(function (pane) {
      pane.hidden = pane.getAttribute("data-bg-pane") !== mode;
    });
    var color = drawerLayout.querySelector('[data-layout-bind="bgColor"]');
    if (color) color.value = current.bgColor || "#ffffff";
    ["bgSize", "bgPosition", "bgOverlay", "bgOpacity", "bgBlur"].forEach(function (prop) {
      var sel = drawerLayout.querySelector('[data-layout-bind="' + prop + '"]');
      if (sel) sel.value = current[prop] || (prop === "bgOpacity" ? "100" : (prop === "bgBlur" ? "8" : ""));
    });
    ["maxWidth", "minHeight", "padding", "borderRadius"].forEach(function (prop) {
      var sel = drawerLayout.querySelector('[data-layout-bind="' + prop + '"]');
      if (sel) sel.value = boxStyle[prop] || "";
    });
    drawerLayout.querySelectorAll("[data-layout-align]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-layout-align") === (boxStyle.align || "") ? "true" : "false");
    });
    var urlField = drawerLayout.querySelector('[data-layout-bind="bgImage"]');
    if (urlField) urlField.value = current.bgImage || "";
    var option = drawerLayout.querySelector("[data-layout-image-option]");
    if (option) {
      var combo = (current.bgSize || "cover") + "|" + (current.bgPosition || "center");
      option.value = option.querySelector('option[value="' + combo + '"]') ? combo : "cover|center";
    }
    var blurOn = drawerLayout.querySelector("[data-layout-blur-on]");
    var blurRow = drawerLayout.querySelector("[data-layout-blur-row]");
    var hasBlur = parseInt(current.bgBlur, 10) > 0;
    if (blurOn) blurOn.checked = hasBlur;
    if (blurRow) blurRow.hidden = !hasBlur;
    var preview = drawerLayout.querySelector("[data-layout-image-preview]");
    if (preview) {
      if (current.bgImage) {
        preview.hidden = false;
        preview.style.backgroundImage = 'url("' + String(current.bgImage).replace(/["')(]/g, "") + '")';
      } else {
        preview.hidden = true;
        preview.style.backgroundImage = "";
      }
    }
    var status = drawerLayout.querySelector("[data-layout-image-status]");
    if (status) status.textContent = current.bgImage ? "Background image set." : "Upload, pick from the gallery, or paste a URL.";
    var grad = (current.bgGradient || "").split(",");
    var from = drawerLayout.querySelector("[data-layout-grad-from]");
    var to = drawerLayout.querySelector("[data-layout-grad-to]");
    var angle = drawerLayout.querySelector("[data-layout-grad-angle]");
    if (from) from.value = (grad[1] || "#1e3a8a").trim() || "#1e3a8a";
    if (to) to.value = (grad[2] || "#2563eb").trim() || "#2563eb";
    if (angle) angle.value = String(parseInt(grad[0], 10) || 180);
  }
  var layoutTarget = "block";
  var LAYOUT_BG_PROPS = {
    bgColor: 1, bgImage: 1, bgSize: 1, bgPosition: 1, bgOverlay: 1,
    bgOpacity: 1, bgBlur: 1, bgMode: 1, bgGradient: 1,
  };
  function currentBlockRegions() {
    var type = drawerSection && drawerSection.getAttribute("data-block-type");
    if (!type) return [];
    var item = null;
    PALETTE.forEach(function (p) { if (p.key === type) item = p; });
    return (item && item.regions) || [];
  }
  function regionLabel(name) {
    if (name === "content") return "Inner container";
    var col = /^col(\d+)$/i.exec(name);
    if (col) return "Column " + col[1];
    return name.replace(/[-_]/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }
  function layoutStyleId(instId) {
    var id = instId || currentBlockId || "";
    if (layoutTarget && layoutTarget !== "block") return id + ".__region." + layoutTarget;
    return id + ".__block";
  }
  function styleIdForLayoutProp(prop, instId) {
    return LAYOUT_BG_PROPS[prop] ? layoutStyleId(instId) : blockStyleId(instId);
  }
  function fillLayoutTargets() {
    var row = drawerLayout && drawerLayout.querySelector("[data-layout-target-row]");
    var sel = drawerLayout && drawerLayout.querySelector("[data-layout-target]");
    if (!row || !sel) return;
    var regions = currentBlockRegions();
    var options = [["block", "This block"]].concat(regions.map(function (name) {
      return [name, regionLabel(name)];
    }));
    var keep = layoutTarget;
    sel.innerHTML = "";
    options.forEach(function (pair) {
      var opt = document.createElement("option");
      opt.value = pair[0];
      opt.textContent = pair[1];
      sel.appendChild(opt);
    });
    var valid = options.some(function (pair) { return pair[0] === keep; });
    layoutTarget = valid ? keep : "block";
    sel.value = layoutTarget;
    row.hidden = options.length < 2;
  }
  function writeBlockGradient() {
    if (!drawerLayout || !currentBlockId) return;
    var from = drawerLayout.querySelector("[data-layout-grad-from]");
    var to = drawerLayout.querySelector("[data-layout-grad-to]");
    var angle = drawerLayout.querySelector("[data-layout-grad-angle]");
    var value = (angle && angle.value ? angle.value : "180") + "deg," +
      (from && from.value ? from.value : "#1e3a8a") + "," +
      (to && to.value ? to.value : "#2563eb");
    commitStyle(layoutStyleId(), "bgGradient", value);
    commitStyle(layoutStyleId(), "bgMode", "gradient");
  }
  function bindBlockLayout() {
    if (!drawerLayout || drawerLayout.getAttribute("data-bound") === "1") return;
    drawerLayout.setAttribute("data-bound", "1");
    var targetSel = drawerLayout.querySelector("[data-layout-target]");
    if (targetSel) targetSel.addEventListener("change", function () {
      layoutTarget = targetSel.value || "block";
      syncBlockLayout(currentBlockId);
    });
    drawerLayout.querySelectorAll("[data-bg-mode]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        if (!currentBlockId) return;
        var nextMode = tab.getAttribute("data-bg-mode");
        commitStyle(layoutStyleId(), "bgMode", nextMode);
        if (nextMode === "gradient") writeBlockGradient();
        syncBlockLayout(currentBlockId);
      });
    });
    drawerLayout.querySelectorAll("[data-layout-align]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!currentBlockId) return;
        var val = btn.getAttribute("data-layout-align") || "";
        var current = getStyle(blockStyleId()).align || "";
        commitStyle(blockStyleId(), "align", current === val ? "" : val);
        syncBlockLayout(currentBlockId);
      });
    });
    drawerLayout.querySelectorAll("[data-layout-bind]").forEach(function (el) {
      var prop = el.getAttribute("data-layout-bind");
      var ev = el.type === "color" ? "input" : "change";
      el.addEventListener(ev, function () {
        if (!currentBlockId) return;
        var sid = styleIdForLayoutProp(prop);
        commitStyle(sid, prop, el.value);
        if (prop === "bgColor") commitStyle(sid, "bgMode", "color");
        if (prop === "bgImage") {
          commitStyle(sid, "bgMode", el.value ? "image" : "color");
        }
        if (prop === "bgOverlay" || prop === "bgSize" || prop === "bgPosition" || prop === "bgOpacity" || prop === "bgBlur") {
          commitStyle(sid, "bgMode", "image");
        }
        if (prop === "bgSize" || prop === "bgPosition" || prop === "bgImage" || prop === "bgOpacity" || prop === "bgBlur") {
          syncBlockLayout(currentBlockId);
        }
      });
    });
    drawerLayout.querySelectorAll("[data-layout-clear]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (!currentBlockId) return;
        var prop = btn.getAttribute("data-layout-clear");
        var sid = styleIdForLayoutProp(prop);
        commitStyle(sid, prop, "");
        if (prop === "bgImage") commitStyle(sid, "bgMode", "color");
        syncBlockLayout(currentBlockId);
      });
    });
    var imageOption = drawerLayout.querySelector("[data-layout-image-option]");
    if (imageOption) imageOption.addEventListener("change", function () {
      if (!currentBlockId) return;
      var parts = (imageOption.value || "cover|center").split("|");
      var sid = layoutStyleId();
      commitStyle(sid, "bgSize", parts[0] || "cover");
      commitStyle(sid, "bgPosition", parts[1] || "center");
      commitStyle(sid, "bgMode", "image");
      syncBlockLayout(currentBlockId);
    });
    var blurToggle = drawerLayout.querySelector("[data-layout-blur-on]");
    if (blurToggle) blurToggle.addEventListener("change", function () {
      if (!currentBlockId) return;
      var sid = layoutStyleId();
      if (blurToggle.checked) {
        var current = getStyle(sid);
        var amt = parseInt(current.bgBlur, 10);
        commitStyle(sid, "bgBlur", (amt >= 1 && amt <= 20) ? String(amt) : "8");
        commitStyle(sid, "bgMode", "image");
      } else {
        commitStyle(sid, "bgBlur", "");
      }
      syncBlockLayout(currentBlockId);
    });
    ["data-layout-grad-from", "data-layout-grad-to", "data-layout-grad-angle"].forEach(function (attr) {
      var el = drawerLayout.querySelector("[" + attr + "]");
      if (!el) return;
      el.addEventListener(el.type === "color" ? "input" : "change", writeBlockGradient);
    });
    var pick = drawerLayout.querySelector("[data-layout-pick-image]");
    var file = drawerLayout.querySelector("[data-layout-upload]");
    var status = drawerLayout.querySelector("[data-layout-image-status]");
    if (pick) pick.addEventListener("click", function () {
      if (!currentBlockId) return;
      if (openGalleryForStyle) openGalleryForStyle(layoutStyleId());
      else if (file) file.click();
    });
    var uploadBtn = drawerLayout.querySelector("[data-layout-upload-btn]");
    if (uploadBtn && file) uploadBtn.addEventListener("click", function () {
      if (currentBlockId) file.click();
    });
    if (file) file.addEventListener("change", function () {
      var chosen = file.files && file.files[0];
      file.value = "";
      if (!chosen || !currentBlockId || !window.CMS.uploadUrl) return;
      if (status) status.textContent = "Uploading…";
      var fd = new FormData();
      fd.append("file", chosen);
      fetch(window.CMS.uploadUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
        body: fd,
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.data.ok) {
            if (status) status.textContent = (res.data && res.data.error) || "Upload failed.";
            return;
          }
          var sid = layoutStyleId();
          commitStyle(sid, "bgImage", res.data.url);
          commitStyle(sid, "bgMode", "image");
          syncBlockLayout(currentBlockId);
        })
        .catch(function () { if (status) status.textContent = "Upload failed — try again."; });
    });
  }
  function refreshReplacePreview() {
    if (!drawerReplace || !currentReplaceFieldId) return;
    var preview = drawerReplace.querySelector("[data-replace-preview]");
    var status = drawerReplace.querySelector("[data-replace-status]");
    var url = getValue(currentReplaceFieldId) || "";
    if (preview) {
      preview.hidden = !url;
      if (url) preview.src = url;
    }
    if (status) {
      status.textContent = url
        ? "This is the picture on the page — not the section background."
        : "No photo yet. Upload one or pick from the gallery.";
    }
    var fieldNode = document.querySelector('[data-field-id="' + currentReplaceFieldId + '"]');
    var thumb = fieldNode && fieldNode.querySelector("[data-bind-image]");
    var nameEl = fieldNode && fieldNode.querySelector("[data-bind-image-name]");
    if (thumb && url) thumb.src = url;
    if (nameEl && url) nameEl.textContent = "Current image";
  }
  function syncReplaceImage(sectionId, preferFieldId) {
    if (!drawerReplace) return;
    var sec = document.getElementById("section-" + sectionId);
    if (!sec) { drawerReplace.hidden = true; currentReplaceFieldId = null; return; }
    var images = sec.querySelectorAll('.field[data-field-type="image"]');
    if (!images.length) { drawerReplace.hidden = true; currentReplaceFieldId = null; return; }
    var node = null;
    if (preferFieldId) {
      for (var i = 0; i < images.length; i++) {
        if (images[i].getAttribute("data-field-id") === preferFieldId) { node = images[i]; break; }
      }
    }
    if (!node) node = images[0];
    currentReplaceFieldId = node.getAttribute("data-field-id");
    drawerReplace.hidden = false;
    refreshReplacePreview();
  }
  function uploadReplaceImage(file) {
    if (!file || !currentReplaceFieldId || !window.CMS.uploadUrl) return;
    var status = drawerReplace && drawerReplace.querySelector("[data-replace-status]");
    if (file.type && file.type.indexOf("image/") !== 0) {
      if (status) status.textContent = "Please choose an image file.";
      return;
    }
    if (status) status.textContent = "Uploading…";
    var fd = new FormData();
    fd.append("file", file);
    fetch(window.CMS.uploadUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": window.CMS.csrfToken },
      body: fd,
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data.ok) {
          if (status) status.textContent = (res.data && res.data.error) || "Upload failed.";
          return;
        }
        setValue(currentReplaceFieldId, res.data.url);
        var p = {}; p[currentReplaceFieldId] = res.data.url;
        pushToPreview(p);
        scheduleSave();
        refreshReplacePreview();
      })
      .catch(function () { if (status) status.textContent = "Upload failed — try again."; });
  }
  function bindReplaceImage() {
    if (!drawerReplace || drawerReplace.getAttribute("data-bound") === "1") return;
    drawerReplace.setAttribute("data-bound", "1");
    var galleryBtn = drawerReplace.querySelector("[data-replace-gallery]");
    var uploadBtn = drawerReplace.querySelector("[data-replace-upload]");
    var file = drawerReplace.querySelector("[data-replace-file]");
    if (galleryBtn) galleryBtn.addEventListener("click", function () {
      if (currentReplaceFieldId && openContentGallery) openContentGallery(currentReplaceFieldId);
    });
    if (uploadBtn && file) uploadBtn.addEventListener("click", function () { file.click(); });
    if (file) file.addEventListener("change", function () {
      var chosen = file.files && file.files[0];
      file.value = "";
      uploadReplaceImage(chosen);
    });
  }
  function pushGlobalToPreview() {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-global", payload: content._global }, PREVIEW_ORIGIN);
  }
  function pushTokensToPreview() {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "apply-tokens", payload: content._tokens }, PREVIEW_ORIGIN);
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
      PREVIEW_ORIGIN
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
  // Scroll the preview to an id so a change is actually visible. A bare id
  // (section / whole block) jumps to its wrapper; a dotted id (section.field)
  // highlights that element. Block instances carry both data-section and
  // data-edit ids, so both paths resolve.
  function revealInPreview(id) {
    if (!previewReady || !id) return;
    if (id.indexOf(".") === -1) scrollPreviewToSection(id);
    else highlightInPreview(id);
  }
  function toggleVisibility(id) {
    var hide = !isHidden(id);
    setHiddenState(id, hide);
    reflectVisibility(id, hide);
    pushVisibility(id, hide);
    // Bring the toggled element into view so the show/hide is visible even when
    // the block sits below the current preview scroll position.
    revealInPreview(id);
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
      if (!head || head.querySelector(".cms-vis-toggle")) return;
      var id = sec.getAttribute("data-section-id");
      if (isHidden(id)) sec.classList.add("cms-form-hidden");
      head.appendChild(makeVisToggle(id));
    });
    // Per-field toggles — skip Brand fields too.
    document.querySelectorAll(".field[data-field-id]").forEach(function (node) {
      if (node.closest && node.closest('[data-panel="brand"]')) return;
      if (node.querySelector(".cms-vis-toggle")) return;
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
    // Only trust messages coming from the preview iframe we created. The
    // `source` string can be spoofed by any window; `e.source` cannot.
    if (previewFrame && e.source !== previewFrame.contentWindow) return;
    var data = e.data || {};
    if (data.source !== "cms-preview") return;
    if (data.type === "ready") {
      previewReady = true;
      setPreviewState("ready");
      // The iframe already has the server-rendered designed HTML. Re-applying
      // every field on first load wrote empty form values over body copy.
      // Only push when the client has unsaved edits that the iframe lacks.
      if (hasUnsavedChanges) pushAllToPreview();
      // Re-assert hidden state in case content._hidden has unsaved changes the
      // freshly server-rendered iframe doesn't reflect yet.
      content._hidden.forEach(function (id) { pushVisibility(id, true); });
      // Same for per-element and global styles.
      Object.keys(content._styles).forEach(function (fid) { pushStyleToPreview(fid); });
      if (content._global && Object.keys(content._global).length) pushGlobalToPreview();
      if (content._tokens && Object.keys(content._tokens).length) pushTokensToPreview();
      // Tell the canvas how deep rows may nest (for drag valid/invalid state).
      previewFrame.contentWindow.postMessage(
        { source: "cms-editor", type: "config", payload: { maxDepth: MAX_DEPTH } }, PREVIEW_ORIGIN);
      // Restore the user's place after a structural reload (add/move/etc.).
      applyPendingFocus();
      // Live reorder: iframe reloaded on its own — put the canvas back where it
      // was and re-frame the block that moved (no full-page reload happened).
      if (previewResync) {
        var pr = previewResync; previewResync = null;
        previewFrame.contentWindow.postMessage(
          { source: "cms-editor", type: "restore-scroll", payload: { y: pr.y } }, PREVIEW_ORIGIN);
        if (pr.id) frameBlockInPreview(pr.id);
      }
    } else if (data.type === "focus-field") {
      focusFieldInForm(data.payload.id);
      if (data.payload && data.payload.openReplace && openContentGallery) {
        openContentGallery(data.payload.id);
      }
    } else if (data.type === "replace-image") {
      focusFieldInForm(data.payload.id);
      if (openContentGallery) openContentGallery(data.payload.id);
    } else if (data.type === "select-block") {
      selectBlockInForm(data.payload.id);
    } else if (data.type === "block-action") {
      var bid = data.payload && data.payload.id;
      var act = data.payload && data.payload.action;
      if (bid) {
        if (act === "move-up") moveBlock(bid, -1);
        else if (act === "move-down") moveBlock(bid, 1);
        else if (act === "duplicate") duplicateBlock(bid);
        else if (act === "delete") deleteBlock(bid);
      }
    } else if (data.type === "deselect") {
      if (drawerEl && drawerEl.classList.contains("open")) closeBlockDrawer();
    } else if (data.type === "canvas-add") {
      var ca = data.payload || {};
      insertNewCanvas(ca.key, ca.dest, ca.beforeId);
    } else if (data.type === "canvas-move") {
      var cm = data.payload || {};
      moveInstanceCanvas(cm.id, cm.dest, cm.beforeId);
    } else if (data.type === "header-add-link") {
      addHeaderLink();
    } else if (data.type === "header-add-button") {
      enableHeaderButton();
    } else if (data.type === "header-menu-label") {
      setHeaderMenuLabel(data.payload && data.payload.id, data.payload && data.payload.html);
    } else if (data.type === "add-block") {
      addToDestOrPalette((data.payload && data.payload.dest) || null);
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
      { source: "cms-editor", type: "style-selection", payload: msg }, PREVIEW_ORIGIN);
  }
  function cmsSendLink(msg) {
    if (!previewReady || !selStyleField) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "link-selection", payload: msg }, PREVIEW_ORIGIN);
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
    // Keep whichever form control mirrors this field in sync: a richtext box
    // (contenteditable div) or a plain-text textarea (Stage B autogrow).
    // Scrub before writing to the contenteditable — this HTML lands in the
    // authenticated editor DOM, so it must be neutralized like any richtext.
    var rich = document.querySelector('.cms-field-richtext[data-bind="' + p.id + '"]');
    if (rich) { rich.innerHTML = cmsScrub(p.html); }
    var box = document.querySelector('textarea[data-bind="' + p.id + '"]');
    if (box && box.value !== p.html) {
      box.value = p.html;
      if (typeof cmsAutoGrow === "function") cmsAutoGrow(box);
    }
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
        else if (cmd === "link") {
          var url = window.prompt("Link to (https://…, mailto:…, tel:…). Leave blank to remove the link.", "https://");
          if (url === null) return;  // cancelled
          url = url.trim();
          cmsSendLink(url ? { href: url } : { clear: true });
        }
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
      if (sec === "regions") return;     // block instances handled below
      Object.keys(content[sec]).forEach(function (f) {
        patch[sec + "." + f] = content[sec][f];
      });
    });
    if (BLOCKS) {
      walkAllInstances(function (inst) {
        var merged = {};
        var defs = BLOCK_DEFAULTS[inst.type] || {};
        Object.keys(defs).forEach(function (k) { merged[k] = defs[k]; });
        Object.keys(inst.fields || {}).forEach(function (k) {
          if (inst.fields[k] !== undefined && inst.fields[k] !== null &&
              !cmsIsBlankHtml(inst.fields[k])) {
            merged[k] = inst.fields[k];
          }
        });
        Object.keys(merged).forEach(function (k) { patch[inst.id + "." + k] = merged[k]; });
        return false;
      });
    }
    pushToPreview(patch);
  }

  // ---- focus on field --------------------------------------------------
  function focusFieldInForm(fieldId) {
    var node = document.querySelector('[data-field-id="' + fieldId + '"]');
    if (!node) return;
    // Block-instance field: open its settings drawer (this relocates the whole
    // section node, taking `node` along with it) so the client sees every
    // option for that block in the focused panel.
    var instSec = node.closest && node.closest(".editor-form-section[data-instance-id]");
    if (instSec && drawerEl) {
      openBlockDrawer(instSec.getAttribute("data-section-id"));
      if (node.getAttribute("data-field-type") === "image") {
        syncReplaceImage(instSec.getAttribute("data-section-id"), fieldId);
      }
    } else if (BLOCKS && drawerEl) {
      // Chrome (nav / brand / tokens) lives in a hidden form column in block
      // mode — open it in the properties rail so the click is still useful.
      var chromeSec = node.closest && node.closest(".editor-form-section");
      var chromeId = chromeSec && (chromeSec.getAttribute("data-section-id") ||
        (chromeSec.id || "").replace(/^section-/, ""));
      if (chromeId) openBlockDrawer(chromeId);
    } else {
      // Classic field: activate the tab / sub-tab that holds it, else
      // it's in a hidden panel and can't be scrolled to or seen.
      var panel = node.closest && node.closest(".editor-tab-panel");
      if (panel && window.cmsSwitchTab) window.cmsSwitchTab(panel.getAttribute("data-panel"));
      var sub = node.closest && node.closest(".nav-subpanel");
      if (sub && window.cmsSwitchSub) window.cmsSwitchSub(sub.getAttribute("data-subpanel"));
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    // If the field lives inside a collapsed repeated-field group, expand it so
    // the field is actually visible (B1).
    var grp = node.closest && node.closest(".cms-fieldgroup");
    if (grp && !grp.open) grp.open = true;
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
    if (drawerBody && node && drawerBody.contains(node) && node.scrollIntoView) {
      node.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function highlightField(node) {
    document.querySelectorAll(".cms-field-active").forEach(function (n) {
      n.classList.remove("cms-field-active");
    });
    node.classList.add("cms-field-active");
  }

  // ---- block settings slide-over drawer (GHL-style) --------------------
  // Clicking a block (preview or sidebar) relocates its form section into a
  // right-edge drawer so the client sees ALL its options in a focused panel.
  // The section node is *moved* (not cloned) so every wired input keeps
  // working; closing the drawer puts it back where it was in the form list.
  var drawerEl = document.getElementById("cms-block-drawer");
  var drawerBody = drawerEl && drawerEl.querySelector("[data-drawer-body]");
  var drawerIdle = drawerEl && drawerEl.querySelector("[data-drawer-idle]");
  var drawerLayout = drawerEl && drawerEl.querySelector("[data-block-layout]");
  var drawerReplace = drawerEl && drawerEl.querySelector("[data-replace-image]");
  var drawerTitle = drawerEl && drawerEl.querySelector("[data-drawer-title]");
  var drawerTabs = drawerEl && drawerEl.querySelector("[data-drawer-tabs]");
  var openGalleryForStyle = null;
  var openContentGallery = null;
  var currentReplaceFieldId = null;
  var drawerBackdrop = document.getElementById("cms-drawer-backdrop");
  var drawerSection = null;     // the .editor-form-section currently relocated
  var drawerHomeParent = null;  // original parent, to restore on close
  var drawerHomeNext = null;    // original next sibling, to restore position
  var currentBlockId = null;    // the block instance currently selected/framed
  var editorShellEl = document.getElementById("editor-shell");

  // ---- canvas <-> panel bridge (Stage A) -------------------------------
  // Draw / clear the on-canvas selection frame around a block, and "peek"
  // (light outline + scroll into view) an element while hovering its field.
  function frameBlockInPreview(id) {
    if (!previewReady || !id) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "frame-block", payload: { id: id } }, PREVIEW_ORIGIN);
  }
  function clearSelectionInPreview() {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "clear-selection", payload: {} }, PREVIEW_ORIGIN);
  }
  function peekFieldInPreview(id) {
    if (!previewReady || !id) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "peek-field", payload: { id: id } }, PREVIEW_ORIGIN);
  }
  function clearPeekInPreview() {
    if (!previewReady) return;
    previewFrame.contentWindow.postMessage(
      { source: "cms-editor", type: "peek-clear", payload: {} }, PREVIEW_ORIGIN);
  }
  // Flat, document-order list of every block instance id (for keyboard nav).
  function orderedBlockIds() {
    var ids = [];
    try { walkAllInstances(function (inst) { ids.push(inst.id); return false; }); }
    catch (e) {}
    return ids;
  }

  // Now that text is edited on the canvas, the drawer leads with properties:
  // each standalone text/richtext VALUE editor is tucked into a collapsed,
  // keyboard-accessible disclosure ("Headline — edit on page ✎"), leaving its
  // Style controls in view. Repeated-group fields already live behind their
  // own collapsed group, so we skip those. Reversed on close so the form panel
  // (the fallback surface) is never left restructured.
  var drawerCollapsed = [];
  function demoteDrawerTextFields(sec) {
    drawerCollapsed = [];
    var fields = sec.querySelectorAll('.field[data-field-type="text"], .field[data-field-type="richtext"]');
    Array.prototype.forEach.call(fields, function (field) {
      if (field.closest(".cms-fieldgroup")) return;         // already grouped/collapsed
      if (field.querySelector(":scope > .cms-textcollapse")) return; // idempotent
      var stylePanel = field.querySelector(":scope > .cms-style-panel");
      var valueNodes = Array.prototype.filter.call(field.children, function (c) { return c !== stylePanel; });
      if (!valueNodes.length) return;
      var labelEl = field.querySelector(":scope > label.field-label");
      var labelText = labelEl ? (labelEl.textContent || "").trim() : "Text";
      var det = document.createElement("details");
      det.className = "cms-textcollapse";
      det.open = true;
      var sum = document.createElement("summary");
      sum.className = "cms-textcollapse-sum";
      sum.innerHTML =
        '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>' +
        '<span class="cms-textcollapse-label"></span>' +
        '<span class="cms-textcollapse-hint">edit on page</span>' +
        '<svg class="cms-textcollapse-caret" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
      sum.querySelector(".cms-textcollapse-label").textContent = labelText;
      det.appendChild(sum);
      var body = document.createElement("div");
      body.className = "cms-textcollapse-body";
      field.insertBefore(det, valueNodes[0]);
      valueNodes.forEach(function (n) { body.appendChild(n); });
      det.appendChild(body);
      // A hidden textarea measures 0, so re-fit it once revealed.
      det.addEventListener("toggle", function () {
        if (!det.open) return;
        det.querySelectorAll("textarea.cms-autogrow").forEach(function (ta) {
          if (typeof cmsAutoGrow === "function") cmsAutoGrow(ta);
        });
      });
      drawerCollapsed.push({ field: field, det: det, body: body });
      det.querySelectorAll("textarea.cms-autogrow").forEach(function (ta) {
        if (typeof cmsAutoGrow === "function") cmsAutoGrow(ta);
      });
    });
  }
  function undoDemoteDrawerTextFields() {
    drawerCollapsed.forEach(function (rec) {
      while (rec.body.firstChild) rec.field.insertBefore(rec.body.firstChild, rec.det);
      rec.det.remove();
    });
    drawerCollapsed = [];
  }

  // A one-line banner at the top of the drawer telling the client that text is
  // edited on the canvas — shown only for blocks that actually have text.
  var drawerHintEl = null;
  function ensureDrawerHint(sec) {
    if (!drawerBody) return;
    var hasText = !!sec.querySelector('.field[data-field-type="text"], .field[data-field-type="richtext"]');
    var sid = (sec.getAttribute("data-section-id") || "").toLowerCase();
    var chromeHint = !!CHROME_DEST[sid];
    if (!drawerHintEl) {
      drawerHintEl = document.createElement("div");
      drawerHintEl.className = "cms-drawer-hint";
      drawerHintEl.innerHTML =
        '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>' +
        '<span data-drawer-hint-copy></span>';
    }
    var copy = drawerHintEl.querySelector("[data-drawer-hint-copy]");
    if (copy) {
      copy.textContent = chromeHint
        ? "Add extra links with Quick Add (Add to Header or Footer), or click + Add block on the bar. Double-click text to edit it."
        : "Double-click text on the page to edit it. Container background, width and spacing are on the Styles tab.";
    }
    drawerBody.insertBefore(drawerHintEl, drawerBody.firstChild);  // keep it on top
    drawerHintEl.hidden = !(hasText || chromeHint);
  }

  function restoreDrawerSection() {
    undoDemoteDrawerTextFields();
    if (drawerSection && drawerHomeParent) {
      drawerHomeParent.insertBefore(drawerSection, drawerHomeNext);
    }
    drawerSection = null;
    drawerHomeParent = null;
    drawerHomeNext = null;
  }
  function blockStyleId(instId) { return (instId || currentBlockId || "") + ".__block"; }
  function switchDrawerTab(name) {
    if (!drawerEl) return;
    var pane = name === "styles" ? "styles" : "general";
    drawerEl.setAttribute("data-drawer-pane", pane);
    drawerEl.querySelectorAll("[data-drawer-tab]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-drawer-tab") === pane);
    });
  }
  function showDrawerIdle() {
    if (!drawerEl) return;
    drawerEl.classList.add("is-idle", "open");
    drawerEl.setAttribute("aria-hidden", "false");
    if (drawerTitle) drawerTitle.textContent = "Properties";
    if (drawerTabs) drawerTabs.hidden = true;
    switchDrawerTab("general");
    if (drawerIdle) drawerIdle.hidden = false;
    if (drawerLayout) drawerLayout.hidden = true;
    if (drawerReplace) drawerReplace.hidden = true;
    hideHeaderPanel();
    drawerEl.classList.remove("is-header");
    currentReplaceFieldId = null;
    if (editorShellEl && editorShellEl.classList.contains("is-block-mode")) {
      editorShellEl.classList.add("cms-drawer-open");
    }
  }
  function closeBlockDrawer() {
    if (!drawerEl) return;
    hideHeaderPanel();
    drawerEl.classList.remove("is-header");
    restoreDrawerSection();
    document.querySelectorAll(".editor-form-section.cms-block-active").forEach(function (n) {
      n.classList.remove("cms-block-active");
    });
    document.querySelectorAll(".sidebar-link.active").forEach(function (l) { l.classList.remove("active"); });
    currentBlockId = null;
    clearSelectionInPreview();
    if (drawerBackdrop) { drawerBackdrop.classList.remove("open"); drawerBackdrop.hidden = true; }
    if (editorShellEl && editorShellEl.classList.contains("is-block-mode") &&
        window.matchMedia && window.matchMedia("(min-width: 641px)").matches) {
      showDrawerIdle();
      return;
    }
    drawerEl.classList.remove("open", "is-idle");
    drawerEl.setAttribute("aria-hidden", "true");
    if (editorShellEl) editorShellEl.classList.remove("cms-drawer-open");
  }
  function hideHeaderPanel() {
    var panel = document.getElementById("cms-header-panel");
    if (panel) panel.hidden = true;
  }
  function openHeaderDrawer(sectionId) {
    if (!drawerEl) return;
    restoreDrawerSection();
    bindHeaderPanel();
    renderHeaderPanel();
    var panel = document.getElementById("cms-header-panel");
    if (panel) panel.hidden = false;
    drawerEl.classList.remove("is-idle");
    drawerEl.classList.add("is-header");
    if (drawerIdle) drawerIdle.hidden = true;
    if (drawerTabs) drawerTabs.hidden = true;
    if (drawerLayout) drawerLayout.hidden = true;
    if (drawerReplace) drawerReplace.hidden = true;
    switchDrawerTab("general");
    if (drawerTitle) drawerTitle.textContent = "Header";
    if (drawerBackdrop) { drawerBackdrop.hidden = false; drawerBackdrop.classList.add("open"); }
    drawerEl.classList.add("open");
    drawerEl.setAttribute("aria-hidden", "false");
    if (editorShellEl) editorShellEl.classList.add("cms-drawer-open");
    document.querySelectorAll(".editor-form-section.cms-block-active").forEach(function (n) {
      n.classList.remove("cms-block-active");
    });
    currentBlockId = sectionId || "header";
    document.querySelectorAll(".sidebar-link").forEach(function (l) {
      l.classList.toggle("active", isHeaderSection(l.dataset.jump));
    });
    frameBlockInPreview(currentBlockId);
    revealInPreview(currentBlockId);
  }
  function openBlockDrawer(sectionId) {
    if (isHeaderSection(sectionId)) {
      openHeaderDrawer(sectionId);
      return;
    }
    hideHeaderPanel();
    if (drawerEl) drawerEl.classList.remove("is-header");
    if (!drawerEl || !drawerBody || !sectionId) return;
    var sec = document.getElementById("section-" + sectionId);
    if (!sec) return;
    if (drawerSection && drawerSection !== sec) restoreDrawerSection();
    if (drawerSection !== sec) {
      drawerHomeParent = sec.parentNode;
      drawerHomeNext = sec.nextSibling;
      drawerBody.appendChild(sec);
      drawerSection = sec;
      demoteDrawerTextFields(sec);
    }
    drawerEl.classList.remove("is-idle");
    if (drawerIdle) drawerIdle.hidden = true;
    var isInst = !!document.querySelector(
      '.editor-form-section[data-instance-id="' + sectionId + '"]'
    );
    if (drawerTabs) drawerTabs.hidden = !isInst;
    var isLayout = !!(sec.getAttribute("data-is-layout"));
    var hasFields = !!sec.querySelector(".field");
    switchDrawerTab(isInst && (isLayout || !hasFields) ? "styles" : "general");
    layoutTarget = "block";
    if (drawerLayout) {
      drawerLayout.hidden = !isInst;
      if (isInst) syncBlockLayout(sectionId);
    }
    syncReplaceImage(sectionId, null);
    ensureDrawerHint(sec);
    var label = sec.querySelector(".editor-form-section-head h2");
    if (drawerTitle) drawerTitle.textContent = label ? (label.textContent || "").trim() : "Edit block";
    if (drawerBackdrop) { drawerBackdrop.hidden = false; drawerBackdrop.classList.add("open"); }
    drawerEl.classList.add("open");
    drawerEl.setAttribute("aria-hidden", "false");
    // Offset the canvas so the drawer sits BESIDE the selected element, never
    // over it — the user watches their edit land.
    if (editorShellEl) editorShellEl.classList.add("cms-drawer-open");
    document.querySelectorAll(".editor-form-section.cms-block-active").forEach(function (n) {
      n.classList.remove("cms-block-active");
    });
    sec.classList.add("cms-block-active");
    if (drawerBody.scrollTo) drawerBody.scrollTo(0, 0); else drawerBody.scrollTop = 0;
    // Also mark the matching sidebar entry active.
    document.querySelectorAll(".sidebar-link").forEach(function (l) {
      l.classList.toggle("active", l.dataset.jump === sectionId);
    });
    currentBlockId = sectionId;
    frameBlockInPreview(sectionId);   // draw the on-canvas selection frame
    revealInPreview(sectionId);
  }
  // Reverse link: hovering a field row outlines + scrolls the matching element
  // on the canvas (peek); leaving it clears the peek. Delegated so it also
  // covers the section once it's relocated into the drawer.
  function bindFieldHoverPeek(root) {
    if (!root) return;
    root.addEventListener("mouseover", function (e) {
      var node = e.target.closest ? e.target.closest("[data-field-id]") : null;
      if (!node) return;
      peekFieldInPreview(node.getAttribute("data-field-id"));
    });
    root.addEventListener("mouseout", function (e) {
      var node = e.target.closest ? e.target.closest("[data-field-id]") : null;
      if (!node) return;
      if (e.relatedTarget && node.contains(e.relatedTarget)) return; // still inside
      clearPeekInPreview();
    });
  }

  if (drawerEl) {
    var drawerCloseBtn = document.getElementById("cms-drawer-close");
    if (drawerCloseBtn) drawerCloseBtn.addEventListener("click", closeBlockDrawer);
    if (drawerTabs) {
      drawerTabs.addEventListener("click", function (e) {
        var tab = e.target.closest ? e.target.closest("[data-drawer-tab]") : null;
        if (tab) switchDrawerTab(tab.getAttribute("data-drawer-tab"));
      });
    }
    if (drawerBackdrop) drawerBackdrop.addEventListener("click", closeBlockDrawer);
    document.addEventListener("keydown", function (e) {
      // Esc deselects / closes the drawer.
      if (e.key === "Escape" && drawerEl.classList.contains("open")) { closeBlockDrawer(); return; }
      // Never hijack typing (text fields, selects, contenteditable, or the
      // in-canvas inline editor which also focuses editable nodes).
      var t = e.target;
      var typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
        t.tagName === "SELECT" || t.isContentEditable);
      if (typing) return;
      var mod = e.ctrlKey || e.metaKey;
      // The block shortcuts act on the currently selected block. (Undo and the
      // help popover are handled by a page-wide listener below so they work on
      // classic pages too.)
      if (!currentBlockId) return;
      if (mod && (e.key === "d" || e.key === "D")) {   // duplicate
        e.preventDefault();
        duplicateBlock(currentBlockId);
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {  // remove (confirms)
        e.preventDefault();
        deleteBlock(currentBlockId);
        return;
      }
      // Arrow keys move the selection between blocks.
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      var ids = orderedBlockIds();
      var i = ids.indexOf(currentBlockId);
      if (i === -1) return;
      var j = e.key === "ArrowDown" ? i + 1 : i - 1;
      if (j < 0 || j >= ids.length) return;
      e.preventDefault();
      openBlockDrawer(ids[j]);
    });
    // The section lives outside #editor-form while in the drawer, so mirror the
    // form's focus->preview highlight here too.
    if (drawerBody) {
      drawerBody.addEventListener("focusin", function (e) {
        var node = e.target.closest ? e.target.closest("[data-field-id]") : null;
        if (!node) return;
        highlightField(node);
        highlightInPreview(node.getAttribute("data-field-id"));
      });
      bindFieldHoverPeek(drawerBody);
    }
    var formElForPeek = document.getElementById("editor-form");
    if (formElForPeek) bindFieldHoverPeek(formElForPeek);
  }

  // ---- page-wide shortcuts + help popover (works on classic pages too) ----
  var helpFab = null, helpPop = null;
  function buildShortcutHelp() {
    if (helpFab || !document.getElementById("editor-shell")) return;
    helpPop = document.createElement("div");
    helpPop.className = "cms-help-pop";
    helpPop.hidden = true;
    helpPop.setAttribute("role", "dialog");
    helpPop.setAttribute("aria-label", "Keyboard shortcuts");
    var rows = [
      ["Double-click text", "Edit it on the page"],
      ["Drag the handle", "Move a block"],
      ["\u2191 / \u2193", "Select previous / next block"],
      ["Ctrl / \u2318 + D", "Duplicate block"],
      ["Delete", "Remove block"],
      ["Ctrl / \u2318 + Z", "Undo"],
      ["Esc", "Close this panel"],
    ];
    var html = '<div class="cms-help-head">Keyboard shortcuts</div><dl class="cms-help-list">';
    rows.forEach(function () { html += '<div class="cms-help-row"><dt></dt><dd></dd></div>'; });
    helpPop.innerHTML = html + "</dl>";
    var rowEls = helpPop.querySelectorAll(".cms-help-row");
    rows.forEach(function (r, i) {
      rowEls[i].querySelector("dt").textContent = r[0];
      rowEls[i].querySelector("dd").textContent = r[1];
    });
    helpFab = document.createElement("button");
    helpFab.type = "button";
    helpFab.className = "cms-help-fab";
    helpFab.setAttribute("aria-label", "Keyboard shortcuts");
    helpFab.setAttribute("aria-expanded", "false");
    helpFab.setAttribute("title", "Keyboard shortcuts (?)");
    helpFab.textContent = "?";
    document.body.appendChild(helpPop);
    document.body.appendChild(helpFab);
    helpFab.addEventListener("click", function (e) { e.stopPropagation(); toggleShortcutHelp(); });
    document.addEventListener("click", function (e) {
      if (!helpPop || helpPop.hidden) return;
      if (e.target === helpFab || helpPop.contains(e.target)) return;
      setShortcutHelp(false);
    });
  }
  function setShortcutHelp(open) {
    if (!helpPop) return;
    helpPop.hidden = !open;
    helpFab.setAttribute("aria-expanded", open ? "true" : "false");
    helpFab.classList.toggle("is-open", open);
  }
  function toggleShortcutHelp() { buildShortcutHelp(); setShortcutHelp(helpPop && helpPop.hidden); }
  buildShortcutHelp();

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && helpPop && !helpPop.hidden) { setShortcutHelp(false); return; }
    var t = e.target;
    var typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
      t.tagName === "SELECT" || t.isContentEditable);
    if (typing) return;
    var mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && (e.key === "z" || e.key === "Z")) {   // undo (any page)
      var ub = document.getElementById("undo-btn");
      if (ub && !ub.hidden && !ub.disabled) { e.preventDefault(); ub.click(); }
      return;
    }
    if (!mod && e.key === "?") { e.preventDefault(); toggleShortcutHelp(); }  // shortcuts help
  });

  // ---- block instances: select / add / reorder / duplicate / delete ----
  function selectBlockInForm(instId) {
    if (drawerEl) { openBlockDrawer(instId); return; }
    var sec = document.querySelector('.editor-form-section[data-instance-id="' + instId + '"]');
    if (!sec) return;
    if (window.cmsSwitchTab) window.cmsSwitchTab("content");
    document.querySelectorAll(".editor-form-section.cms-block-active").forEach(function (n) {
      n.classList.remove("cms-block-active");
    });
    sec.classList.add("cms-block-active");
    sec.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function saveAndReload() {
    structuralReload = true;
    save();
  }

  // Reorders don't need a full-page reload (which flashes the whole editor and
  // dumps you at the top). Instead we mutate the content tree, re-sync the form
  // panel + layers tree in place, then reload ONLY the preview iframe and land
  // it back at the same scroll offset with the moved block re-framed. Used by
  // every move path (up/down buttons, form drag, canvas drag).
  // Save, then reload ONLY the preview iframe (no full-page flash) and land it
  // back at the same scroll offset, optionally re-framing a block.
  function livePreviewReload(focusId) {
    var y = 0;
    try { y = (previewFrame && previewFrame.contentWindow && previewFrame.contentWindow.scrollY) || 0; } catch (e) {}
    previewResync = { y: y, id: focusId || null };
    previewReloadAfterSave = true;
    save();
  }

  function saveAndResync(focusId) {
    liveResyncStructure(focusId);
    livePreviewReload(focusId);
  }

  // Delete is live too: prune the removed subtree's form sections + layer
  // entries, refresh the surviving blocks' controls, and reload just the
  // preview. Add / duplicate mount new form sections in place the same way.
  function deleteResync(removedIds) {
    if (currentBlockId && removedIds.indexOf(currentBlockId) !== -1) closeBlockDrawer();
    removedIds.forEach(function (rid) {
      var sec = document.querySelector('.editor-form-section[data-instance-id="' + rid + '"]');
      if (sec && sec.parentNode) sec.parentNode.removeChild(sec);
      document.querySelectorAll('.sidebar-link[data-jump="' + rid + '"]').forEach(function (l) {
        if (l.parentNode) l.parentNode.removeChild(l);
      });
    });
    // Drop any layer group left with no entries.
    document.querySelectorAll(".sidebar-group").forEach(function (grp) {
      if (!grp.querySelector(".sidebar-link") && grp.parentNode) grp.parentNode.removeChild(grp);
    });
    refreshBlockControls();
    currentBlockId = null;
    livePreviewReload(null);
  }

  // Re-order the flat form-section list + layers tree to match the (already
  // mutated) content tree, refresh per-block controls, and mark the moved
  // block selected — all without re-rendering any section's inner fields.
  function liveResyncStructure(focusId) {
    var dfs = [];
    walkAllInstances(function (inst, ctx) {
      dfs.push({ id: inst.id, depth: ctx.depth, parentId: ctx.parentId, region: ctx.region });
      return false;
    });
    var order = {};
    dfs.forEach(function (n, i) { order[n.id] = i; });

    // 1. Refresh each instance section's structural metadata (depth/parent/region).
    dfs.forEach(function (n) {
      var sec = document.querySelector('.editor-form-section[data-instance-id="' + n.id + '"]');
      if (!sec) return;
      sec.setAttribute("data-depth", String(n.depth));
      sec.setAttribute("data-parent-id", n.parentId || "");
      sec.setAttribute("data-region", n.region);
    });

    // 2. Re-order the content panel's sections in place (skip any node the
    //    settings drawer has temporarily relocated — it isn't a panel child).
    var panel = document.querySelector('.editor-tab-panel[data-panel="content"]');
    if (panel) {
      dfs.forEach(function (n) {
        var sec = panel.querySelector('.editor-form-section[data-instance-id="' + n.id + '"]');
        if (sec && sec.parentNode === panel) panel.appendChild(sec);
      });
    }

    // 3. Rebuild the up/down/duplicate/delete controls + column adders so their
    //    disabled states, indentation, and per-column counts reflect new order.
    refreshBlockControls();

    // 4. Re-order the layers tree within each group + refresh depth affordances.
    document.querySelectorAll(".sidebar-group").forEach(function (grp) {
      var links = Array.prototype.slice.call(grp.querySelectorAll(".sidebar-link"))
        .filter(function (l) { return order[l.dataset.jump] != null; })
        .sort(function (a, b) { return order[a.dataset.jump] - order[b.dataset.jump]; });
      links.forEach(function (l) {
        var d = dfs[order[l.dataset.jump]].depth || 0;
        l.setAttribute("data-depth", String(d));
        l.style.setProperty("--tree-depth", String(d));
        l.classList.toggle("is-nested", d > 0);
        grp.appendChild(l);
      });
    });

    // 5. Keep the moved block selected so the user can see what shifted.
    currentBlockId = focusId;
    document.querySelectorAll(".sidebar-link").forEach(function (l) {
      l.classList.toggle("active", l.dataset.jump === focusId);
    });
  }

  // injectBlockControls is idempotent-guarded, so wipe the injected bits first
  // to force a clean rebuild against the current tree order.
  function refreshBlockControls() {
    document.querySelectorAll(".editor-form-section[data-instance-id]").forEach(function (sec) {
      var head = sec.querySelector(".editor-form-section-head");
      if (head) { var bc = head.querySelector(".block-controls"); if (bc) bc.remove(); }
      var lar = sec.querySelector(".layout-add-row"); if (lar) lar.remove();
      sec.classList.remove("block-depth-1", "block-depth-2");
    });
    injectBlockControls();
  }

  // Structural block ops trigger a full editor reload, so "which block should
  // open afterwards" is stashed in sessionStorage (keyed per page) and consumed
  // on the next load. This is what makes adding a block open its settings panel
  // — mirroring GHL, where picking a block reveals its properties on the right.
  function focusKey() {
    return "cms:focusBlock:" + ((window.CMS && window.CMS.saveUrl) || "");
  }
  function scrollKey() {
    return "cms:previewScroll:" + ((window.CMS && window.CMS.saveUrl) || "");
  }
  // mode: "settings" (open the block's panel — for add/duplicate) or "select"
  // (just re-select + keep it in view — for reorder/move).
  function setPendingFocus(id, mode) {
    try { window.sessionStorage.setItem(focusKey(), JSON.stringify({ id: id, mode: mode || "settings" })); } catch (e) {}
  }
  function consumePendingFocus() {
    try {
      var k = focusKey();
      var v = window.sessionStorage.getItem(k);
      window.sessionStorage.removeItem(k);
      if (!v) return null;
      if (v.charAt(0) === "{") { var o = JSON.parse(v); return o && o.id ? o : null; }
      return { id: v, mode: "settings" };  // back-compat with older string form
    } catch (e) { return null; }
  }
  // Remember the canvas scroll offset so a structural reload lands you back
  // where you were editing instead of at the top of the page.
  function stashPreviewScroll() {
    try {
      var y = previewFrame && previewFrame.contentWindow ? (previewFrame.contentWindow.scrollY || 0) : 0;
      window.sessionStorage.setItem(scrollKey(), String(y));
    } catch (e) {}
  }
  function consumePreviewScroll() {
    try {
      var k = scrollKey();
      var v = window.sessionStorage.getItem(k);
      window.sessionStorage.removeItem(k);
      return v == null ? null : parseInt(v, 10);
    } catch (e) { return null; }
  }
  // Open a block's settings: highlight the section and, if it has fields, focus
  // the first one (which also opens its style panel).
  function openBlockSettings(instId) {
    var sec = document.querySelector('.editor-form-section[data-instance-id="' + instId + '"]');
    if (!sec) return;
    selectBlockInForm(instId);
    var firstField = sec.querySelector("[data-field-id]");
    if (firstField) focusFieldInForm(firstField.getAttribute("data-field-id"));
  }

  // Build a fresh instance for a block type, seeding empty column children so
  // a row is ready to receive drops. Mirrors blocks.seed_instance server-side.
  function makeInstance(type) {
    var meta = PALETTE_BY_KEY[type] || {};
    var inst = { id: newInstanceId(), type: type, fields: {} };
    var regions = meta.regions || [];
    if (regions.length) {
      inst.children = {};
      regions.forEach(function (name) { inst.children[name] = []; });
    }
    return inst;
  }

  // ---- live add / duplicate (no full-page flash) -------------------------
  // Mount a new form section + Layers row, wire fields, then save + reload
  // only the preview iframe. Falls back to a full reload if the fragment
  // cannot be built (unknown type, network error).
  function hideZeroState() {
    var zero = document.querySelector(".editor-zero-state");
    if (zero && zero.parentNode) zero.parentNode.removeChild(zero);
    syncCanvasStart();
  }

  function stripInjectedChrome(sec) {
    if (!sec) return;
    sec.querySelectorAll(".block-controls, .block-drag-grip, .layout-add-row, .cms-vis-toggle").forEach(function (n) {
      if (n.parentNode) n.parentNode.removeChild(n);
    });
    sec.classList.remove("cms-block-active", "cms-drop-target", "cms-dragging",
      "block-depth-1", "block-depth-2");
    sec.removeAttribute("data-cms-dnd");
    sec.querySelectorAll("[data-cms-bound]").forEach(function (n) { n.removeAttribute("data-cms-bound"); });
    sec.querySelectorAll("[data-cms-style-bound]").forEach(function (n) { n.removeAttribute("data-cms-style-bound"); });
  }

  function rewriteInstanceIds(root, fromId, toId) {
    if (!root || !fromId || !toId || fromId === toId) return;
    var attrs = [
      "id", "for", "data-field-id", "data-bind", "data-bind-image", "data-bind-color",
      "data-gallery-pick", "data-style-panel", "data-style-pick-image", "data-ghl-picker",
      "data-instance-id", "data-section-id", "data-parent-id", "data-vis-id",
      "aria-describedby", "aria-labelledby",
    ];
    function rewrite(node) {
      if (!node || !node.getAttribute) return;
      attrs.forEach(function (a) {
        var v = node.getAttribute(a);
        if (v && v.indexOf(fromId) !== -1) node.setAttribute(a, v.split(fromId).join(toId));
      });
    }
    rewrite(root);
    if (root.querySelectorAll) root.querySelectorAll("*").forEach(rewrite);
  }

  function makeLayerLink(sec) {
    var id = sec.getAttribute("data-instance-id");
    var labelEl = sec.querySelector(".editor-form-section-head h2");
    var label = labelEl ? (labelEl.textContent || "").trim() : "Section";
    var isLayout = sec.getAttribute("data-is-layout") === "1";
    var depth = parseInt(sec.getAttribute("data-depth") || "0", 10) || 0;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sidebar-link" + (isLayout ? " is-layout" : "") + (depth ? " is-nested" : "");
    btn.setAttribute("data-jump", id);
    btn.setAttribute("data-depth", String(depth));
    btn.style.setProperty("--tree-depth", String(depth));
    var icon = isLayout
      ? '<rect x="3" y="4" width="7" height="16" rx="1"/><rect x="14" y="4" width="7" height="16" rx="1"/>'
      : (depth
        ? '<circle cx="12" cy="12" r="3.2"/>'
        : '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>');
    var iconFill = depth && !isLayout ? "currentColor" : "none";
    btn.innerHTML =
      '<span class="sidebar-link-icon" aria-hidden="true">' +
        '<svg viewBox="0 0 24 24" fill="' + iconFill + '" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + icon + "</svg>" +
      "</span>" +
      '<span class="sidebar-link-text"></span>';
    btn.querySelector(".sidebar-link-text").textContent = label;
    return btn;
  }

  function ensureLayersGroup() {
    var aside = document.querySelector(".editor-sidebar");
    if (!aside) return null;
    var groups = aside.querySelectorAll(".sidebar-group");
    for (var i = 0; i < groups.length; i++) {
      var label = groups[i].querySelector(".sidebar-group-label");
      var text = label ? (label.textContent || "").trim() : "";
      if (text === "Sections" || groups[i].querySelector(".sidebar-link:not([data-chrome])")) {
        return groups[i];
      }
    }
    var grp = document.createElement("div");
    grp.className = "sidebar-group";
    grp.innerHTML = '<div class="sidebar-group-label">Sections</div>';
    aside.appendChild(grp);
    return grp;
  }

  function insertMountedSection(sec) {
    hideZeroState();
    var panel = document.querySelector('.editor-tab-panel[data-panel="content"]');
    if (panel) panel.appendChild(sec);
    var grp = ensureLayersGroup();
    if (grp) {
      var link = makeLayerLink(sec);
      grp.appendChild(link);
      bindLayerLink(link);
    }
    bindFormSectionDnD(sec);
    if (typeof bindEditorFields === "function") bindEditorFields(sec);
    injectVisibilityToggles();
    sec.querySelectorAll("[data-gallery-pick]").forEach(function (btn) {
      if (btn.getAttribute("data-cms-gallery") === "1") return;
      btn.setAttribute("data-cms-gallery", "1");
      btn.addEventListener("click", function () {
        if (openContentGallery) openContentGallery(btn.getAttribute("data-gallery-pick"));
      });
    });
  }

  function mountFromClone(sourceSec, fromId, toId) {
    var sec = sourceSec.cloneNode(true);
    stripInjectedChrome(sec);
    rewriteInstanceIds(sec, fromId, toId);
    insertMountedSection(sec);
    return sec;
  }

  function fetchBlockForm(type, id) {
    var url;
    try {
      var parsed = new URL(window.location.href);
      parsed.searchParams.set("block_form", "1");
      parsed.searchParams.set("type", type);
      parsed.searchParams.set("id", id);
      url = parsed.toString();
    } catch (e) {
      url = window.location.pathname + "?block_form=1&type=" + encodeURIComponent(type) +
        "&id=" + encodeURIComponent(id);
    }
    return fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || !data.ok || !data.section) {
          return Promise.reject(data);
        }
        return data.section;
      });
    });
  }

  function mountFromHtml(html) {
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    var sec = wrap.querySelector(".editor-form-section");
    if (!sec) throw new Error("empty block form");
    insertMountedSection(sec);
    return sec;
  }

  function mountOneSpec(spec) {
    if (spec.sourceId) {
      var src = document.querySelector('.editor-form-section[data-instance-id="' + spec.sourceId + '"]');
      if (src) {
        mountFromClone(src, spec.sourceId, spec.id);
        return Promise.resolve();
      }
    }
    if (spec.type) {
      var same = document.querySelector('.editor-form-section[data-block-type="' + spec.type + '"]');
      if (same) {
        mountFromClone(same, same.getAttribute("data-instance-id"), spec.id);
        return Promise.resolve();
      }
      return fetchBlockForm(spec.type, spec.id).then(mountFromHtml);
    }
    var loc = findInstanceLoc(spec.id);
    if (loc && loc.inst && loc.inst.type) {
      return fetchBlockForm(loc.inst.type, spec.id).then(mountFromHtml);
    }
    return Promise.reject(new Error("cannot mount block"));
  }

  function mountLiveBlocks(specs, focusId) {
    Promise.all(specs.map(mountOneSpec)).then(function () {
      saveAndResync(focusId);
      if (focusId) openBlockSettings(focusId);
    }).catch(function () {
      setPendingFocus(focusId);
      saveAndReload();
    });
  }

  function addBlock(type, destPath) {
    if (isHeaderDest(destPath)) {
      pendingBlockStyle = null;
      if (type === "button") enableHeaderButton();
      else addHeaderLink();
      return;
    }
    if (!type) { pendingBlockStyle = null; return; }
    if (countAllInstances() >= MAX_BLOCKS) {
      pendingBlockStyle = null;
      window.alert("This page has reached the maximum of " + MAX_BLOCKS + " sections.");
      return;
    }
    var meta = PALETTE_BY_KEY[type] || {};
    if (!chromeAllows(type, destPath || "main")) {
      pendingBlockStyle = null;
      return;
    }
    var list = destinationList(destPath);
    if (!list) {
      pendingBlockStyle = null;
      window.alert("That destination is no longer available.");
      return;
    }
    // A layout (row) block cannot be dropped so deep that its own columns would
    // exceed the nesting cap.
    if (meta.is_layout && destinationDepth(destPath) >= MAX_DEPTH) {
      pendingBlockStyle = null;
      window.alert("You can't nest a row that deep. Add it higher up the page.");
      return;
    }
    var inst = makeInstance(type);
    list.push(inst);
    if (pendingBlockStyle) {
      if (typeof content._styles !== "object" || content._styles === null) content._styles = {};
      content._styles[inst.id + ".__block"] = pendingBlockStyle;
      pendingBlockStyle = null;
    }
    mountLiveBlocks([{ id: inst.id, type: type }], inst.id);
  }

  // Total instances in a subtree (the block itself plus every nested child).
  function subtreeSize(inst) {
    var n = 1;
    if (inst && inst.children && typeof inst.children === "object") {
      Object.keys(inst.children).forEach(function (name) {
        (inst.children[name] || []).forEach(function (c) { n += subtreeSize(c); });
      });
    }
    return n;
  }

  // Deep-clone an instance subtree with fresh ids at every level.
  function remapStylesForClone(oldId, newId) {
    if (!oldId || !newId || !content._styles) return;
    Object.keys(content._styles).forEach(function (fid) {
      if (fid.indexOf(oldId + ".") !== 0) return;
      content._styles[newId + fid.slice(oldId.length)] =
        JSON.parse(JSON.stringify(content._styles[fid]));
    });
  }
  function cloneInstance(inst, idMap) {
    var copy = { id: newInstanceId(), type: inst.type,
                 fields: JSON.parse(JSON.stringify(inst.fields || {})) };
    if (idMap) idMap[inst.id] = copy.id;
    remapStylesForClone(inst.id, copy.id);
    if (inst.children && typeof inst.children === "object") {
      copy.children = {};
      Object.keys(inst.children).forEach(function (name) {
        copy.children[name] = (inst.children[name] || []).map(function (child) {
          return cloneInstance(child, idMap);
        });
      });
    }
    return copy;
  }

  function duplicateBlock(id) {
    var loc = findInstanceLoc(id);
    if (!loc) return;
    // Duplicating a row copies its whole subtree, so count the children too —
    // not just "is there room for one more" (E8). The server enforces the same
    // cap and 400s, but this keeps the UI honest before the round-trip.
    if (countAllInstances() + subtreeSize(loc.inst) > MAX_BLOCKS) {
      window.alert("This page has reached the maximum of " + MAX_BLOCKS + " sections.");
      return;
    }
    var idMap = {};
    var clone = cloneInstance(loc.inst, idMap);
    loc.list.splice(loc.index + 1, 0, clone);
    mountLiveBlocks(Object.keys(idMap).map(function (oldId) {
      return { id: idMap[oldId], type: null, sourceId: oldId };
    }), clone.id);
  }

  function moveBlock(id, dir) {
    var loc = findInstanceLoc(id);
    if (!loc) return;
    var j = loc.index + dir;
    if (j < 0 || j >= loc.list.length) return;
    var tmp = loc.list[loc.index];
    loc.list[loc.index] = loc.list[j];
    loc.list[j] = tmp;
    saveAndResync(id);
  }

  function deleteBlock(id) {
    var loc = findInstanceLoc(id);
    if (!loc) return;
    var msg = (loc.inst.children ? "Remove this row and everything in it? " : "Remove this section? ") + "You can Undo afterwards.";
    if (!window.confirm(msg)) return;
    // Capture the whole subtree's ids (block + nested descendants) before it
    // leaves the tree, so the live prune knows every section/layer to remove.
    var removedIds = [];
    walkInstances([loc.inst], function (inst) { removedIds.push(inst.id); return false; });
    loc.list.splice(loc.index, 1);
    // If that emptied the page, fall back to a reload so the server-rendered
    // "no content yet" zero-state appears.
    if (countAllInstances() === 0) { saveAndReload(); return; }
    deleteResync(removedIds);
  }

  function makeBlockControl(label, svg, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "block-ctl";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = svg;
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      onClick();
    });
    return b;
  }

  function injectBlockControls() {
    var sections = document.querySelectorAll(".editor-form-section[data-instance-id]");
    var UP = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg>';
    var DOWN = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
    var DUP = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    var DEL = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
    sections.forEach(function (sec) {
      var head = sec.querySelector(".editor-form-section-head");
      if (!head || head.querySelector(".block-controls")) return;
      var id = sec.getAttribute("data-instance-id");
      // Indent nested blocks so the tree structure reads at a glance.
      var depth = parseInt(sec.getAttribute("data-depth") || "0", 10) || 0;
      if (depth > 0) sec.classList.add("block-depth-" + Math.min(depth, 2));
      // up/down are only meaningful within the block's own container.
      var loc = findInstanceLoc(id);
      var bar = document.createElement("div");
      bar.className = "block-controls";
      var up = makeBlockControl("Move up", UP, function () { moveBlock(id, -1); });
      var down = makeBlockControl("Move down", DOWN, function () { moveBlock(id, 1); });
      if (loc && loc.index === 0) up.disabled = true;
      if (loc && loc.index === loc.list.length - 1) down.disabled = true;
      bar.appendChild(up);
      bar.appendChild(down);
      bar.appendChild(makeBlockControl("Duplicate", DUP, function () { duplicateBlock(id); }));
      var del = makeBlockControl("Remove", DEL, function () { deleteBlock(id); });
      del.classList.add("block-ctl-danger");
      bar.appendChild(del);
      head.appendChild(bar);

      // Layout rows get a direct "+ Add block" button per column, so the client
      // never has to hunt through the Add-section destination dropdown.
      if (sec.getAttribute("data-is-layout") === "1") {
        injectColumnAdders(sec, id, depth);
      }
    });
  }

  // Replace the static layout hint with real per-column add buttons that open
  // Quick Add pre-targeted at that column.
  function injectColumnAdders(sec, instId, depth) {
    var stack = sec.querySelector(".stack-5");
    if (!stack || stack.querySelector(".layout-add-row")) return;
    var hint = stack.querySelector(".layout-block-hint");
    if (hint) hint.remove();

    var meta = PALETTE_BY_KEY[sec.getAttribute("data-block-type")] || {};
    var cols = meta.regions || [];
    var wrap = document.createElement("div");
    wrap.className = "layout-add-row";

    if (depth >= MAX_DEPTH) {
      var note = document.createElement("p");
      note.className = "field-hint";
      note.textContent = "Nesting limit reached — this row can't hold more blocks.";
      wrap.appendChild(note);
    } else {
      cols.forEach(function (col, i) {
        var loc = findInstanceLoc(instId);
        var count = loc && loc.inst.children && loc.inst.children[col]
          ? loc.inst.children[col].length : 0;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "layout-add-btn";
        var label = cols.length > 1 ? ("Column " + (i + 1)) : "this column";
        btn.innerHTML =
          '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>' +
          '<span>Add block to ' + label + (count ? " (" + count + ")" : "") + '</span>';
        btn.addEventListener("click", function () {
          if (openPalette) openPalette(instId + "/" + col);
        });
        wrap.appendChild(btn);
      });
    }
    stack.appendChild(wrap);
  }

  // Human label for a destination path, shown in the "Add to" dropdown.
  function destinationOptions() {
    var opts = allShellRegionNames().filter(function (name) {
      return name.indexOf("header") !== 0 && name !== "nav";
    }).map(function (name) {
      return { value: name, label: shellRegionLabel(name) };
    });
    walkAllInstances(function (inst, ctx) {
      var meta = PALETTE_BY_KEY[inst.type] || {};
      var cols = meta.regions || [];
      if (cols.length && ctx.depth < MAX_DEPTH) {
        var rowLabel = meta.label || inst.type;
        cols.forEach(function (col, i) {
          opts.push({
            value: inst.id + "/" + col,
            label: "— " + rowLabel + " → " + (cols.length > 1 ? "Column " + (i + 1) : regionLabel(col)),
          });
        });
      }
      return false;
    });
    return opts;
  }

  // When a section/row (or a block inside one) is selected, new elements go
  // there — not onto the page as another top-level section.
  function defaultInsertDest() {
    if (!currentBlockId) return "main";
    if (isHeaderSection(currentBlockId)) return "main";
    var chromeDest = CHROME_DEST[currentBlockId];
    if (chromeDest && allShellRegionNames().indexOf(chromeDest) !== -1) return chromeDest;
    if (allShellRegionNames().indexOf(currentBlockId) !== -1) return currentBlockId;
    var sec = document.querySelector(
      '.editor-form-section[data-instance-id="' + currentBlockId + '"]'
    );
    var type = sec && sec.getAttribute("data-block-type");
    var cols = ((PALETTE_BY_KEY[type] || {}).regions) || [];
    if (cols.length) return currentBlockId + "/" + cols[0];
    var loc = findInstanceLoc(currentBlockId);
    if (loc && loc.parentId && loc.region) return loc.parentId + "/" + loc.region;
    return "main";
  }

  // Preferred category order for the Quick Add rail (GHL-like). Anything not
  // listed is appended alphabetically after these.
  var CATEGORY_ORDER = ["Rows", "Layout", "Headers", "Text", "Media", "Elements", "Form", "Sections", "Content", "Home", "General"];
  var CATEGORY_LABELS = { Layout: "Sections", General: "More", Content: "Elements" };
  var PALETTE_ICON_SVG = {
    square: '<rect x="4" y="4" width="16" height="16" rx="2"/>',
    type: '<path d="M4 7V5h16v2M12 5v14M9 19h6"/>',
    heading: '<path d="M6 5v14M18 5v14M6 12h12"/>',
    image: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5.2-5.2a2 2 0 0 0-2.8 0L6 18"/>',
    video: '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3z"/>',
    columns: '<rect x="3" y="4" width="7" height="16" rx="1"/><rect x="14" y="4" width="7" height="16" rx="1"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/>',
    list: '<path d="M9 7h12M9 12h12M9 17h12"/><circle cx="5" cy="7" r="1.2"/><circle cx="5" cy="12" r="1.2"/><circle cx="5" cy="17" r="1.2"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    mouse: '<rect x="7" y="3" width="10" height="14" rx="5"/><path d="M12 7v4"/>',
    form: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h5"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    share: '<circle cx="18" cy="5" r="2.4"/><circle cx="6" cy="12" r="2.4"/><circle cx="18" cy="19" r="2.4"/><path d="m8.2 13.4 7.6 4.2M15.8 6.4 8.2 10.6"/>'
  };
  function inferPaletteIcon(item) {
    var named = String(item.icon || "").trim();
    if (named && PALETTE_ICON_SVG[named]) return named;
    var hay = ((item.key || "") + " " + (item.label || "")).toLowerCase();
    if (item.is_layout || /row|column|grid/.test(hay)) return "columns";
    if (/image|photo|img|logo|gallery|avatar/.test(hay)) return "image";
    if (/video|youtube|reel/.test(hay)) return "video";
    if (/headline|heading|title|hero/.test(hay)) return "heading";
    if (/paragraph|body|text|copy|about/.test(hay)) return "type";
    if (/list|bullet|faq/.test(hay)) return "list";
    if (/button|cta|click/.test(hay)) return "mouse";
    if (/form|survey|embed/.test(hay)) return "form";
    if (/link|social/.test(hay)) return "share";
    if (/timer|countdown|clock/.test(hay)) return "clock";
    if (/section|header|footer|nav/.test(hay)) return "layout";
    return named && PALETTE_ICON_SVG[named] ? named : "square";
  }
  function paletteIconHtml(name) {
    var path = PALETTE_ICON_SVG[name] || PALETTE_ICON_SVG.square;
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + "</svg>";
  }
  function layoutColCount(item) {
    if (item.regions && item.regions.length) return Math.min(6, item.regions.length);
    var match = String(item.label || item.key || "").match(/(\d+)\s*(col|column)/i);
    if (match) return Math.min(6, parseInt(match[1], 10) || 2);
    return item.is_layout ? 2 : 0;
  }

  function paletteCategories() {
    var seen = {};
    PALETTE.forEach(function (p) { seen[(p.category || "General").trim()] = true; });
    var cats = Object.keys(seen);
    cats.sort(function (a, b) {
      var ia = CATEGORY_ORDER.indexOf(a); var ib = CATEGORY_ORDER.indexOf(b);
      if (ia === -1) ia = 999; if (ib === -1) ib = 999;
      if (ia !== ib) return ia - ib;
      return a.localeCompare(b);
    });
    return cats;
  }

  function initPalette() {
    var addBtn = document.getElementById("add-block-btn");
    var modal = document.getElementById("palette-modal");
    var grid = document.getElementById("palette-grid");
    var search = document.getElementById("palette-search");
    var status = document.getElementById("palette-status");
    var rail = document.getElementById("palette-rail");
    var dest = document.getElementById("palette-dest");
    if (!addBtn || !modal || !grid) return;

    var activeCat = "__all__";
    var paletteTitle = document.getElementById("palette-title");
    var quickadd = modal.querySelector(".cms-quickadd");
    var NAV = [
      { id: "__all__", label: "Quick Add", icon: "square" },
      { id: "__sections__", label: "Sections", icon: "layout" },
      { id: "__rows__", label: "Rows", icon: "columns" },
      { id: "__elements__", label: "Elements", icon: "type" }
    ];
    var NAV_TITLES = {
      __all__: "Quick Add",
      __sections__: "Add A Section",
      __rows__: "Add A Row",
      __elements__: "Add An Element"
    };
    var SECTION_WIDTHS = [
      { label: "Full Width", maxWidth: "100%", size: "full" },
      { label: "Wide", maxWidth: "1200px", size: "wide" },
      { label: "Medium", maxWidth: "960px", size: "medium" },
      { label: "Small", maxWidth: "720px", size: "small" }
    ];

    function findSectionBlockKey() {
      var byKey = PALETTE.filter(function (p) { return p.key === "section"; })[0];
      if (byKey) return byKey.key;
      var oneCol = PALETTE.filter(function (p) {
        return p.is_layout && (p.regions || []).length === 1;
      })[0];
      if (oneCol) return oneCol.key;
      var named = PALETTE.filter(function (p) {
        return /section/i.test((p.key || "") + " " + (p.label || ""));
      })[0];
      if (named) return named.key;
      var layout = PALETTE.filter(function (p) { return p.is_layout; })[0];
      if (layout) return layout.key;
      return PALETTE[0] ? PALETTE[0].key : "";
    }

    function itemMatchesCat(item) {
      if (activeCat === "__all__") return true;
      if (activeCat === "__sections__") {
        return !!item.is_layout || /section/i.test((item.key || "") + " " + (item.label || ""));
      }
      if (activeCat === "__rows__") return !!item.is_layout;
      if (activeCat === "__elements__") return !item.is_layout;
      return (item.category || "General").trim() === activeCat;
    }

    function refreshDest() {
      if (!dest) return;
      var prev = dest.value;
      dest.innerHTML = "";
      destinationOptions().forEach(function (o) {
        var opt = document.createElement("option");
        opt.value = o.value; opt.textContent = o.label;
        dest.appendChild(opt);
      });
      if (prev) dest.value = prev;
    }

    function syncPaletteChrome() {
      if (paletteTitle) paletteTitle.textContent = NAV_TITLES[activeCat] || "Quick Add";
      if (quickadd) quickadd.classList.toggle("is-sections", activeCat === "__sections__");
    }

    function renderRail() {
      if (!rail) return;
      rail.innerHTML = "";
      NAV.forEach(function (e) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "palette-nav-item" + (e.id === activeCat ? " active" : "");
        btn.innerHTML = paletteIconHtml(e.icon) + "<span></span>";
        btn.querySelector("span").textContent = e.label;
        btn.addEventListener("click", function () {
          activeCat = e.id;
          if (search && e.id === "__sections__") search.value = "";
          renderRail();
          render(search ? search.value : "");
        });
        rail.appendChild(btn);
      });
    }

    function renderSectionPresets() {
      grid.innerHTML = "";
      var hint = document.createElement("p");
      hint.className = "palette-widths-hint";
      hint.textContent = "How wide should this section sit on the page?";
      grid.appendChild(hint);
      var wrap = document.createElement("div");
      wrap.className = "palette-widths";
      SECTION_WIDTHS.forEach(function (preset) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "palette-width";
        btn.setAttribute("data-size", preset.size);
        btn.innerHTML =
          '<span class="palette-width-art" aria-hidden="true">' +
            (preset.size === "full" ? '<i class="palette-width-chevron"></i>' : "") +
            '<span class="palette-width-page"><i class="palette-width-band"></i></span>' +
            (preset.size === "full" ? '<i class="palette-width-chevron"></i>' : "") +
          '</span><span class="palette-width-label"></span>';
        btn.querySelector(".palette-width-label").textContent = preset.label;
        btn.addEventListener("click", function () {
          var type = findSectionBlockKey();
          if (!type) {
            if (status) status.textContent = "No section block is available on this site yet.";
            return;
          }
          pendingBlockStyle = { maxWidth: preset.maxWidth };
          if (preset.maxWidth === "100%") pendingBlockStyle.width = "100%";
          if (window.__cmsCloseDialog) window.__cmsCloseDialog(modal);
          addBlock(type, dest ? dest.value : "main");
        });
        wrap.appendChild(btn);
      });
      grid.appendChild(wrap);
      if (status) status.textContent = "";
    }

    function bindPaletteCard(card, item) {
      function place() {
        if (window.__cmsCloseDialog) window.__cmsCloseDialog(modal);
        addBlock(item.key, dest ? dest.value : "main");
      }
      card.addEventListener("click", place);
      card.addEventListener("dragstart", function (e) {
        dragPayload = { kind: "new", type: item.key };
        try {
          e.dataTransfer.setData("application/x-cms-newblock", item.key);
          if (item.is_layout) e.dataTransfer.setData("application/x-cms-layout", "1");
          e.dataTransfer.setData("text/plain", item.key);
        } catch (_) {}
        e.dataTransfer.effectAllowed = "copy";
        card.classList.add("cms-dragging");
        if (modal) modal.classList.add("is-dragging");
      });
      card.addEventListener("dragend", function () {
        dragPayload = null;
        clearDropHints();
        card.classList.remove("cms-dragging");
        if (modal) modal.classList.remove("is-dragging");
        if (previewReady) previewFrame.contentWindow.postMessage(
          { source: "cms-editor", type: "canvas-drag-clear", payload: {} }, PREVIEW_ORIGIN);
      });
    }

    function makePaletteCard(item) {
      var card = document.createElement("button");
      card.type = "button";
      card.className = "palette-card" + (item.is_layout ? " palette-card-layout" : "");
      card.setAttribute("draggable", "true");
      var cols = layoutColCount(item);
      var visual = "";
      if (item.is_layout && cols) {
        visual = '<span class="palette-cols" data-cols="' + cols + '" aria-hidden="true">' +
          new Array(cols + 1).join("<i></i>") + "</span>";
      } else {
        visual = '<span class="palette-card-icon">' + paletteIconHtml(inferPaletteIcon(item)) + "</span>";
      }
      card.innerHTML =
        '<span class="palette-card-grip" aria-hidden="true">' +
          '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><circle cx="9" cy="6" r="1.3"/><circle cx="15" cy="6" r="1.3"/><circle cx="9" cy="12" r="1.3"/><circle cx="15" cy="12" r="1.3"/><circle cx="9" cy="18" r="1.3"/><circle cx="15" cy="18" r="1.3"/></svg>' +
        "</span>" + visual + '<span class="palette-card-label"></span>';
      card.querySelector(".palette-card-label").textContent = item.label || item.key;
      bindPaletteCard(card, item);
      return card;
    }

    function render(filter) {
      syncPaletteChrome();
      var q = (filter || "").toLowerCase().trim();
      if (activeCat === "__sections__" && !q) {
        renderSectionPresets();
        return;
      }
      grid.innerHTML = "";
      var shown = 0;
      var grouped = {};
      PALETTE.forEach(function (item) {
        var cat = (item.category || "General").trim();
        var destAllow = allowedTypesForDest(dest ? dest.value : "");
        if (destAllow && destAllow.indexOf(item.key) === -1) return;
        if (!itemMatchesCat(item)) return;
        var hay = (item.label + " " + item.key + " " + cat).toLowerCase();
        if (q && hay.indexOf(q) === -1) return;
        shown++;
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(item);
      });
      var cats = paletteCategories().filter(function (c) { return grouped[c]; });
      Object.keys(grouped).forEach(function (c) {
        if (cats.indexOf(c) === -1) cats.push(c);
      });
      var showHeadings = activeCat === "__all__" && !q && cats.length > 1;
      cats.forEach(function (cat) {
        var host = grid;
        if (showHeadings) {
          var group = document.createElement("div");
          group.className = "palette-group";
          var title = document.createElement("div");
          title.className = "palette-group-label";
          title.textContent = CATEGORY_LABELS[cat] || cat;
          var nest = document.createElement("div");
          nest.className = "palette-group-grid";
          group.appendChild(title);
          group.appendChild(nest);
          grid.appendChild(group);
          host = nest;
        }
        grouped[cat].forEach(function (item) { host.appendChild(makePaletteCard(item)); });
      });
      if (status) {
        status.textContent = shown ? "" :
          (PALETTE.length ? "Nothing matches your search." :
            "Your agency hasn't published any blocks for this site yet.");
      }
    }

    // Open the drawer. `presetDest` (optional) pre-selects a destination like
    // a specific row column; when it targets a column we default the category
    // rail to non-row blocks so the client sees elements to drop in.
    openPalette = function (presetDest, presetCat) {
      refreshDest();
      activeCat = presetCat || "__all__";
      if (presetDest) {
        var hasOption = Array.prototype.some.call(dest ? dest.options : [], function (o) {
          return o.value === presetDest;
        });
        if (dest && hasOption) dest.value = presetDest;
        if (!presetCat && (presetDest.indexOf("/") !== -1 || /^header|^footer/.test(presetDest))) {
          activeCat = "__elements__";
        }
      }
      renderRail();
      render(search ? search.value : "");
      if (window.__cmsOpenDialog) window.__cmsOpenDialog(modal, addBtn);
    };
    addBtn.addEventListener("click", function () { addToDestOrPalette(defaultInsertDest()); });
    var barAdd = document.getElementById("bar-add-block");
    if (barAdd) barAdd.addEventListener("click", function () { addToDestOrPalette(defaultInsertDest()); });
    var zeroAdd = document.getElementById("zero-add-block");   // empty-canvas CTA
    if (zeroAdd) zeroAdd.addEventListener("click", function () { openPalette(null); });
    var canvasSection = document.getElementById("canvas-add-section");
    var canvasInsert = document.getElementById("canvas-insert-element");
    if (canvasSection) canvasSection.addEventListener("click", function () {
      openPalette(null, "__sections__");
    });
    if (canvasInsert) canvasInsert.addEventListener("click", function () {
      addToDestOrPalette(defaultInsertDest(), "__elements__");
    });
    var closeBtn = document.getElementById("close-palette-btn");
    var cancelBtn = document.getElementById("cancel-palette-btn");
    if (closeBtn) closeBtn.addEventListener("click", function () { if (window.__cmsCloseDialog) window.__cmsCloseDialog(modal); });
    if (cancelBtn) cancelBtn.addEventListener("click", function () { if (window.__cmsCloseDialog) window.__cmsCloseDialog(modal); });
    modal.addEventListener("click", function (e) { if (e.target === modal && window.__cmsCloseDialog) window.__cmsCloseDialog(modal); });
    if (search) search.addEventListener("input", function () { render(search.value); });
    if (dest) dest.addEventListener("change", function () { render(search ? search.value : ""); });
  }

  function initUndo() {
    var btn = document.getElementById("undo-btn");
    if (!btn || !window.CMS.versionsUrl) return;
    btn.hidden = false;
    btn.addEventListener("click", function () {
      // Undo means "I don't want the current state." Any pending or in-flight
      // save would re-persist exactly what we're about to discard, so cancel it
      // first: clear the debounce, abort the request, and drop the queued flag.
      // If the user has unsaved typing, make the discard explicit (E4/E5).
      if (hasUnsavedChanges || saveInFlight || saveTimer) {
        if (!window.confirm(
          "Undo will discard your most recent change and restore the previous " +
          "version. Continue?"
        )) return;
      }
      clearTimeout(saveTimer);
      saveTimer = null;
      saveQueued = false;
      if (saveAbort) { try { saveAbort.abort(); } catch (e) {} }
      saveInFlight = false;
      hasUnsavedChanges = false;
      btn.disabled = true;
      setStatus("saving");
      fetch(window.CMS.versionsUrl, {
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var versions = (d && d.versions) || [];
          if (!versions.length) {
            btn.disabled = false;
            setStatus("saved");
            window.alert("Nothing to undo yet.");
            return;
          }
          return fetch(window.CMS.versionRestoreUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: { "X-CSRFToken": window.CMS.csrfToken, "Content-Type": "application/json" },
            // pop: linear undo — consume this snapshot so repeated undo steps
            // back through history instead of toggling with the last state.
            body: JSON.stringify({ version_id: versions[0].id, pop: true }),
          })
            .then(function (r) { return r.json(); })
            .then(function (res) {
              if (!res.ok) { btn.disabled = false; setStatus("error", res.error || "Undo failed."); return; }
              window.location.reload();
            });
        })
        .catch(function () { btn.disabled = false; setStatus("error", "Undo failed — try again."); });
    });
  }

  function clearDropHints() {
    document.querySelectorAll(".editor-form-section.cms-drop-target").forEach(function (n) {
      n.classList.remove("cms-drop-target");
    });
  }

  // Resolve where a drop on a given form section should insert. Dropping on a
  // layout row targets its first column; dropping on a leaf targets that leaf's
  // own container, just after it.
  function dropTargetFor(sec) {
    var id = sec.getAttribute("data-instance-id");
    var isLayout = sec.getAttribute("data-is-layout") === "1";
    if (isLayout) {
      var meta = PALETTE_BY_KEY[sec.getAttribute("data-block-type")] || {};
      var cols = meta.regions || [];
      if (cols.length) return { path: id + "/" + cols[0], afterId: null };
    }
    var loc = findInstanceLoc(id);
    if (!loc) return null;
    return { list: loc.list, index: loc.index, afterId: id };
  }

  function pathForDestList(destList) {
    if (!destList) return null;
    var names = allShellRegionNames();
    for (var i = 0; i < names.length; i++) {
      if (destList === regionList(names[i])) return names[i];
    }
    var found = null;
    walkAllInstances(function (inst) {
      if (inst.children) {
        Object.keys(inst.children).forEach(function (k) {
          if (inst.children[k] === destList) found = inst.id + "/" + k;
        });
      }
      return !!found;
    });
    return found;
  }

  // Move an existing instance into a destination list, just after `afterId`
  // (or to the end). Prevents dropping a row into its own subtree.
  function moveInstanceTo(id, destList, afterId) {
    var loc = findInstanceLoc(id);
    if (!loc || !destList) return false;
    // Guard: don't move a row into its own subtree (would orphan the tree).
    var ownLists = [];
    walkInstances([loc.inst], function (inst) {
      if (inst.children) {
        Object.keys(inst.children).forEach(function (k) { ownLists.push(inst.children[k]); });
      }
      return false;
    });
    if (ownLists.indexOf(destList) !== -1) { return false; }
    var meta = PALETTE_BY_KEY[loc.inst.type] || {};
    var destPath = pathForDestList(destList);
    if (destPath && !chromeAllows(loc.inst.type, destPath, id)) return false;
    if (meta.is_layout && destPath && destinationDepth(destPath) >= MAX_DEPTH) {
      window.alert("You can't nest a row that deep. Drop it onto the section, not inside a column.");
      return false;
    }
    var moving = loc.list.splice(loc.index, 1)[0];
    var at = destList.length;
    if (afterId) {
      for (var i = 0; i < destList.length; i++) {
        if (destList[i].id === afterId) { at = i + 1; break; }
      }
    }
    destList.splice(at, 0, moving);
    saveAndResync(id);
    return true;
  }

  // Insert `inst` into `list` just before the block with id `beforeId`
  // (append when beforeId is null / not found).
  function listInsertBefore(list, inst, beforeId) {
    if (beforeId) {
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === beforeId) { list.splice(i, 0, inst); return; }
      }
    }
    list.push(inst);
  }

  // Canvas drop of a NEW palette block at a resolved {dest, beforeId}.
  function insertNewCanvas(type, destPath, beforeId) {
    if (isHeaderDest(destPath)) {
      if (type === "button") enableHeaderButton();
      else addHeaderLink();
      return;
    }
    if (!type) return;
    if (countAllInstances() >= MAX_BLOCKS) {
      window.alert("This page has reached the maximum of " + MAX_BLOCKS + " sections.");
      return;
    }
    var meta = PALETTE_BY_KEY[type] || {};
    if (!chromeAllows(type, destPath || "main")) return;
    if (meta.is_layout && destinationDepth(destPath) >= MAX_DEPTH) {
      window.alert("You can't nest a row that deep. Add it higher up the page.");
      return;
    }
    var list = destinationList(destPath);
    if (!list) { window.alert("That destination is no longer available."); return; }
    var inst = makeInstance(type);
    listInsertBefore(list, inst, beforeId);
    mountLiveBlocks([{ id: inst.id, type: type }], inst.id);
  }

  // Canvas drag-reorder of an EXISTING block to a resolved {dest, beforeId}.
  function moveInstanceCanvas(id, destPath, beforeId) {
    if (!id || id === beforeId) return;
    var loc = findInstanceLoc(id);
    if (!loc) return;
    var destList = destinationList(destPath);
    if (!destList) return;
    if (!chromeAllows(loc.inst.type, destPath, id)) return;
    // Guard: never move a row into its own subtree.
    var ownLists = [];
    walkInstances([loc.inst], function (inst) {
      if (inst.children) {
        Object.keys(inst.children).forEach(function (k) { ownLists.push(inst.children[k]); });
      }
      return false;
    });
    if (ownLists.indexOf(destList) !== -1) return;
    var meta = PALETTE_BY_KEY[loc.inst.type] || {};
    if (meta.is_layout && destinationDepth(destPath) >= MAX_DEPTH) return;
    var moving = loc.list.splice(loc.index, 1)[0];
    listInsertBefore(destList, moving, beforeId);
    saveAndResync(id);
  }

  function bindFormSectionDnD(sec) {
    if (!sec || sec.getAttribute("data-cms-dnd") === "1") return;
    sec.setAttribute("data-cms-dnd", "1");
    sec.addEventListener("dragover", function (e) {
        if (!dragPayload) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = dragPayload.kind === "new" ? "copy" : "move";
        clearDropHints();
        sec.classList.add("cms-drop-target");
      });
      sec.addEventListener("dragleave", function () { sec.classList.remove("cms-drop-target"); });
      sec.addEventListener("drop", function (e) {
        if (!dragPayload) return;
        e.preventDefault();
        clearDropHints();
        var target = dropTargetFor(sec);
        if (!target) { dragPayload = null; return; }
        if (dragPayload.kind === "new") {
          if (target.path) {
            addBlock(dragPayload.type, target.path);
          } else if (target.list) {
            if (countAllInstances() >= MAX_BLOCKS) {
              window.alert("This page has reached the maximum of " + MAX_BLOCKS + " sections.");
            } else {
              var inst = makeInstance(dragPayload.type);
              target.list.splice(target.index + 1, 0, inst);
              mountLiveBlocks([{ id: inst.id, type: dragPayload.type }], inst.id);
            }
          }
        } else if (dragPayload.kind === "move" && dragPayload.id !== sec.getAttribute("data-instance-id")) {
          if (target.path) {
            var dl = destinationList(target.path);
            moveInstanceTo(dragPayload.id, dl, null);
          } else if (target.list) {
            moveInstanceTo(dragPayload.id, target.list, target.afterId);
          }
        }
        dragPayload = null;
      });

      // Make the section itself draggable (reorder existing blocks), with a
      // visible grip affordance so it reads as a drag handle.
      var head = sec.querySelector(".editor-form-section-head");
      if (head) {
        if (!head.querySelector(".block-drag-grip")) {
          var grip = document.createElement("span");
          grip.className = "block-drag-grip";
          grip.setAttribute("title", "Drag to reorder");
          grip.setAttribute("aria-hidden", "true");
          grip.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';
          head.insertBefore(grip, head.firstChild);
        }
        head.setAttribute("draggable", "true");
        head.addEventListener("dragstart", function (e) {
          dragPayload = { kind: "move", id: sec.getAttribute("data-instance-id") };
          e.dataTransfer.effectAllowed = "move";
          try { e.dataTransfer.setData("text/plain", dragPayload.id); } catch (_) {}
          sec.classList.add("cms-dragging");
        });
        head.addEventListener("dragend", function () { dragPayload = null; clearDropHints(); sec.classList.remove("cms-dragging"); });
      }
  }

  function initFormDnD() {
    var panel = document.querySelector('.editor-tab-panel[data-panel="content"]');
    if (!panel) return;
    panel.querySelectorAll(".editor-form-section[data-instance-id]").forEach(bindFormSectionDnD);
  }

  // After a structural reload, restore the user's place: re-select the block
  // they added/moved (and open its panel for adds), or failing that just put
  // the canvas back to its previous scroll offset. Runs once, and only after
  // the preview is ready so its scroll/frame messages actually land.
  var pendingAfterReload = null;
  var pendingSavedScroll = null;
  var pendingConsumed = false;  // set once initBlocks has read the stash
  var pendingApplied = false;
  function applyPendingFocus() {
    // Wait until both the stash is read AND the preview can receive messages;
    // the iframe 'ready' can arrive before or after initBlocks runs.
    if (pendingApplied || !pendingConsumed || !previewReady) return;
    pendingApplied = true;
    if (pendingAfterReload && pendingAfterReload.id) {
      var pf = pendingAfterReload;
      if (pf.mode === "select") {
        // Reorder: keep it selected + in view, but don't force the panel open.
        currentBlockId = pf.id;
        document.querySelectorAll(".sidebar-link").forEach(function (l) {
          l.classList.toggle("active", l.dataset.jump === pf.id);
        });
        frameBlockInPreview(pf.id);
        scrollPreviewToSection(pf.id);
        var sec = document.querySelector('.editor-form-section[data-instance-id="' + pf.id + '"]');
        if (sec && sec.scrollIntoView) sec.scrollIntoView({ block: "nearest" });
      } else {
        openBlockSettings(pf.id);   // add/duplicate: open the block's panel
      }
    } else if (pendingSavedScroll != null && previewFrame && previewFrame.contentWindow) {
      previewFrame.contentWindow.postMessage(
        { source: "cms-editor", type: "restore-scroll", payload: { y: pendingSavedScroll } }, PREVIEW_ORIGIN);
    }
  }

  function initBlocks() {
    if (!BLOCKS) return;
    bindHeaderPanel();
    injectBlockControls();
    initPalette();
    initFormDnD();
    initUndo();
    // A block was just added/duplicated/moved — restore focus + scroll once the
    // preview is ready (see applyPendingFocus, also called from the ready msg).
    pendingAfterReload = consumePendingFocus();
    pendingSavedScroll = consumePreviewScroll();
    pendingConsumed = true;
    applyPendingFocus();
    bindBlockLayout();
    bindReplaceImage();
    document.querySelectorAll("[data-chrome-drawer]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        openBlockDrawer(btn.getAttribute("data-chrome-drawer"));
      });
    });
    syncCanvasStart();
    if (drawerEl && window.matchMedia && window.matchMedia("(min-width: 641px)").matches) {
      if (!currentBlockId) showDrawerIdle();
    }
  }

  function loadGhlForms(force) {
    if (force) ghlFormsRequest = null;
    if (ghlFormsRequest) return ghlFormsRequest;
    ghlFormsRequest = fetch(window.CMS.ghlFormsUrl, {
      method: "GET",
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || !data.ok) {
          return Promise.reject({ status: response.status, data: data });
        }
        return data.forms || [];
      });
    });
    return ghlFormsRequest;
  }

  function initGhlFormPicker(node, fieldId, current) {
    var select = node.querySelector("[data-ghl-picker]");
    var status = node.querySelector("[data-ghl-picker-status]");
    var retry = node.querySelector("[data-ghl-picker-retry]");
    if (!select || !status) return;

    function setPickerState(message, isError, canRetry) {
      status.textContent = message;
      status.classList.toggle("is-error", !!isError);
      if (retry) retry.hidden = !canRetry;
    }

    function populate(forms) {
      select.innerHTML = "";
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "No form selected";
      select.appendChild(empty);
      forms.forEach(function (form) {
        var option = document.createElement("option");
        option.value = form.value;
        option.textContent = form.name;
        option.title = form.name;
        select.appendChild(option);
      });
      var known = forms.some(function (form) { return form.value === current; });
      if (current && !known) {
        var stale = document.createElement("option");
        stale.value = current;
        stale.textContent = "Current form (no longer available)";
        select.appendChild(stale);
      }
      select.value = current;
      select.disabled = !!window.CMS.readOnly;
      if (current && !known) {
        setPickerState(
          "The selected form is no longer available. Choose another form.",
          true,
          false
        );
      } else {
        var availability = forms.length
          ? forms.length + (forms.length === 1 ? " form available" : " forms available")
          : "No forms are available in this GoHighLevel location.";
        setPickerState(
          availability,
          false,
          false
        );
      }
    }

    function start(force) {
      select.disabled = true;
      select.innerHTML = '<option value="">Loading forms…</option>';
      setPickerState("Connecting to GoHighLevel…", false, false);
      loadGhlForms(force).then(populate).catch(function (error) {
        select.innerHTML = '<option value="">Forms unavailable</option>';
        select.disabled = true;
        var message = error && error.data && error.data.error;
        setPickerState(message || "Forms could not be loaded. Try again.", true, true);
      });
    }

    select.addEventListener("change", function () {
      var previous = current;
      var next = select.value;
      if (!next && window.CMS.published) {
        select.value = previous;
        setPickerState("Unpublish this page before removing its form.", true, false);
        return;
      }
      if (!next && previous && !window.confirm(
        "Remove this form? This page will stop capturing leads."
      )) {
        select.value = previous;
        return;
      }
      current = next;
      setValue(fieldId, next);
      previewReloadAfterSave = true;
      setPickerState(next ? "Form selected. Saving and refreshing preview…" : "No form selected.", false, false);
      scheduleSave();
    });
    if (retry) retry.addEventListener("click", function () { start(true); });
    start(false);
  }

  // ---- bind fields -----------------------------------------------------
  function bindEditorFields(root) {
    if (!root) return;
    root.querySelectorAll("[data-field-id]").forEach(function (node) {
      if (node.getAttribute("data-cms-bound") === "1") return;
      node.setAttribute("data-cms-bound", "1");
      var fieldId = node.dataset.fieldId;
      var ftype = node.dataset.fieldType;
      var current = getValue(fieldId) || "";

      if (ftype === "ghl-embed") {
        initGhlFormPicker(node, fieldId, current);
      }

      if (ftype === "text") {
        var input = node.querySelector("[data-bind]");
        input.value = current;
        cmsAutoGrow(input);
        input.addEventListener("input", function () {
          cmsAutoGrow(input);
          setValue(fieldId, input.value);
          var p = {}; p[fieldId] = input.value;
          pushToPreview(p);
          scheduleSave();
        });
        // Keep a text field logically single-value: Enter shouldn't insert a
        // literal newline (the textarea only exists so the value can wrap).
        if (input.tagName === "TEXTAREA") {
          input.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); input.blur(); }
          });
        }
      }

      if (ftype === "embed" || ftype === "code") {
        var embedEl = node.querySelector("[data-bind]");
        if (embedEl) {
          embedEl.value = current;
          cmsAutoGrow(embedEl);
          embedEl.addEventListener("input", function () {
            cmsAutoGrow(embedEl);
            setValue(fieldId, embedEl.value);
            var p = {}; p[fieldId] = embedEl.value;
            pushToPreview(p);
            scheduleSave();
          });
          if (ftype === "embed" && embedEl.tagName === "TEXTAREA") {
            embedEl.addEventListener("keydown", function (e) {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); embedEl.blur(); }
            });
          }
        }
      }

      if (ftype === "select") {
        var selectEl = node.querySelector("[data-bind]");
        if (selectEl) {
          // Fall back to the first option if the stored value is unknown, so
          // the dropdown never shows a blank selection.
          var hasCurrent = false;
          for (var si = 0; si < selectEl.options.length; si++) {
            if (selectEl.options[si].value === current) { hasCurrent = true; break; }
          }
          selectEl.value = hasCurrent ? current : (selectEl.options[0] ? selectEl.options[0].value : "");
          if (!hasCurrent && selectEl.value) setValue(fieldId, selectEl.value);
          selectEl.addEventListener("change", function () {
            setValue(fieldId, selectEl.value);
            var p = {}; p[fieldId] = selectEl.value;
            pushToPreview(p);
            scheduleSave();
          });
        }
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
          var collapsed = String(v).replace(/\s/g, "").toLowerCase();
          if (collapsed.indexOf("javascript:") === 0 ||
              collapsed.indexOf("vbscript:") === 0 ||
              collapsed.indexOf("data:") === 0) return false;
          if (v.charAt(0) === "#" || v.charAt(0) === "/") return true;
          if (/^(mailto:|tel:)\S+/i.test(v)) return true;
          try {
            var u = new URL(v);
            return u.protocol === "http:" || u.protocol === "https:";
          }
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
            // Never open a scriptable scheme from the authenticated dashboard.
            var collapsed = v.replace(/\s/g, "").toLowerCase();
            if (collapsed.indexOf("javascript:") === 0 ||
                collapsed.indexOf("vbscript:") === 0 ||
                collapsed.indexOf("data:") === 0) return;
            if (v && linkLooksValid(v)) window.open(v, "_blank", "noopener");
          });
        }
      }

      if (ftype === "richtext") {
        var rt = node.querySelector("[data-bind]");
        var ignoreRtInput = true;
        rt.innerHTML = cmsScrub(current);
        // Empty contenteditables insert a <br> and fire `input`. Ignore that
        // so we don't save a blank over the designed paragraph.
        requestAnimationFrame(function () { ignoreRtInput = false; });
        rt.addEventListener("input", function () {
          if (ignoreRtInput) return;
          var html = rt.innerHTML;
          if (cmsIsBlankHtml(html)) {
            setValue(fieldId, "");
            return;
          }
          setValue(fieldId, html);
          var p = {}; p[fieldId] = html;
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
        var dropZone = node.querySelector(".cms-field-image") || node;
        if (current) {
          img.src = current;
          nameEl.textContent = "Current image";
        }

        // Shared upload for both the file picker and drag-and-drop. Runs through
        // the same server endpoint (validated + optimized before hitting the CDN).
        function uploadImageFile(file) {
          if (!file) return;
          if (file.type && file.type.indexOf("image/") !== 0) {
            nameEl.textContent = "Please choose an image file.";
            return;
          }
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
              nameEl.textContent = file.name || "Image";
              setValue(fieldId, res.data.url);
              var p = {}; p[fieldId] = res.data.url;
              pushToPreview(p);
              scheduleSave();
            })
            .catch(function () { nameEl.textContent = "Upload failed — please try again."; });
        }

        fileInput.addEventListener("change", function () {
          uploadImageFile(fileInput.files[0]);
        });

        // Drag-and-drop a file onto the field. Only react to OS file drags
        // (dataTransfer.types includes "Files"); internal block/palette drags
        // use custom MIME types and must pass through untouched.
        function isFileDrag(e) {
          var t = e.dataTransfer && e.dataTransfer.types;
          return !!t && Array.prototype.indexOf.call(t, "Files") !== -1;
        }
        ["dragenter", "dragover"].forEach(function (ev) {
          dropZone.addEventListener(ev, function (e) {
            if (!isFileDrag(e)) return;
            e.preventDefault();
            e.stopPropagation();
            if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
            dropZone.classList.add("cms-image-dragover");
          });
        });
        ["dragleave", "dragend"].forEach(function (ev) {
          dropZone.addEventListener(ev, function () {
            dropZone.classList.remove("cms-image-dragover");
          });
        });
        dropZone.addEventListener("drop", function (e) {
          if (!isFileDrag(e)) return;
          e.preventDefault();
          e.stopPropagation();
          dropZone.classList.remove("cms-image-dragover");
          var files = e.dataTransfer && e.dataTransfer.files;
          if (files && files.length) uploadImageFile(files[0]);
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
    root.querySelectorAll("[data-style-panel]").forEach(function (panel) {
      if (panel.getAttribute("data-cms-style-bound") === "1") return;
      panel.setAttribute("data-cms-style-bound", "1");
      var fieldId = panel.getAttribute("data-style-panel");
      var current = getStyle(fieldId);

      // Opening the style editor jumps the live preview to that element so the
      // client can see their colour / size / font changes take effect.
      if (panel.tagName.toLowerCase() === "details") {
        panel.addEventListener("toggle", function () {
          if (panel.open) revealInPreview(fieldId);
        });
      }

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
        bg.addEventListener("input", function () {
          commit("bgColor", bg.value);
          commit("bgImage", "");
          commit("bgMode", "color");
          syncFieldStyleBg(panel);
        });
      }
      panel.querySelectorAll("[data-style-bg-mode]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var next = btn.getAttribute("data-style-bg-mode");
          panel.querySelectorAll("[data-style-bg-mode]").forEach(function (b) {
            b.classList.toggle("is-active", b === btn);
          });
          panel.querySelectorAll("[data-style-bg-pane]").forEach(function (pane) {
            pane.hidden = pane.getAttribute("data-style-bg-pane") !== next;
          });
        });
      });
      var clearBg = panel.querySelector("[data-style-clear-bg]");
      if (clearBg) clearBg.addEventListener("click", function () {
        commit("bgColor", "");
        commit("bgImage", "");
        commit("bgMode", "");
        syncFieldStyleBg(panel);
      });
      panel.querySelectorAll("[data-style-clear]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var prop = btn.getAttribute("data-style-clear");
          commit(prop, "");
          if (prop === "bgImage") commit("bgMode", "");
        });
      });
      syncFieldStyleBg(panel);

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

      // Additional typography + layout controls (fixed-option selects).
      ["lineHeight", "letterSpacing", "textTransform", "padding", "borderRadius"].forEach(function (prop) {
        var sel = panel.querySelector('[data-style-bind="' + prop + '"]');
        if (!sel) return;
        if (current[prop]) sel.value = current[prop];
        sel.addEventListener("change", function () { commit(prop, sel.value); });
      });
      panel.querySelectorAll("[data-style-pick-image]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          if (openGalleryForStyle) openGalleryForStyle(btn.getAttribute("data-style-pick-image"));
        });
      });

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
  }

  function init() {
    // Tab switching (Content / Brand) is wired inline in editor.html so it is
    // immune to static-file caching; window.cmsSwitchTab is exposed there.
    bindEditorFields(document);

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

    // sidebar jump + drag a block onto another section in Layers
    document.querySelectorAll(".sidebar-link").forEach(bindLayerLink);
    __cmsContinueInit();
  }

  function bindLayerLink(link) {
    if (!link || link.getAttribute("data-cms-layer") === "1") return;
    link.setAttribute("data-cms-layer", "1");
    var isChrome = link.getAttribute("data-chrome") === "1";
      if (BLOCKS && !isChrome) {
        link.setAttribute("draggable", "true");
        link.setAttribute("title", "Drag onto another section to move it");
        link.addEventListener("dragstart", function (e) {
          var id = link.dataset.jump;
          if (!id) { e.preventDefault(); return; }
          skipLayerClick = true;
          dragPayload = { kind: "move", id: id };
          try {
            e.dataTransfer.setData("application/x-cms-moveblock", id);
            e.dataTransfer.setData("text/plain", id);
            var sec = document.querySelector('.editor-form-section[data-instance-id="' + id + '"]');
            if (sec && sec.getAttribute("data-is-layout") === "1") {
              e.dataTransfer.setData("application/x-cms-layout", "1");
            }
          } catch (_) {}
          e.dataTransfer.effectAllowed = "move";
          link.classList.add("cms-dragging");
          if (previewReady) previewFrame.contentWindow.postMessage(
            { source: "cms-editor", type: "canvas-drag-move", payload: { id: id } }, PREVIEW_ORIGIN);
        });
        link.addEventListener("dragend", function () {
          dragPayload = null;
          clearDropHints();
          document.querySelectorAll(".sidebar-link.cms-drop-target").forEach(function (n) {
            n.classList.remove("cms-drop-target");
          });
          link.classList.remove("cms-dragging");
          if (previewReady) previewFrame.contentWindow.postMessage(
            { source: "cms-editor", type: "canvas-drag-clear", payload: {} }, PREVIEW_ORIGIN);
        });
        link.addEventListener("dragover", function (e) {
          if (!dragPayload || dragPayload.kind !== "move") return;
          if (dragPayload.id === link.dataset.jump) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          document.querySelectorAll(".sidebar-link.cms-drop-target").forEach(function (n) {
            n.classList.remove("cms-drop-target");
          });
          link.classList.add("cms-drop-target");
        });
        link.addEventListener("dragleave", function () { link.classList.remove("cms-drop-target"); });
        link.addEventListener("drop", function (e) {
          if (!dragPayload || dragPayload.kind !== "move") return;
          e.preventDefault();
          e.stopPropagation();
          link.classList.remove("cms-drop-target");
          var fromId = dragPayload.id;
          var ontoId = link.dataset.jump;
          dragPayload = null;
          if (!fromId || !ontoId || fromId === ontoId) return;
          var onto = document.querySelector('.editor-form-section[data-instance-id="' + ontoId + '"]');
          var target = onto ? dropTargetFor(onto) : null;
          if (target && target.path) {
            moveInstanceTo(fromId, destinationList(target.path), null);
          } else if (target && target.list) {
            moveInstanceTo(fromId, target.list, target.afterId);
          }
        });
      } else if (BLOCKS && isChrome && CHROME_DEST[link.dataset.jump]) {
        link.addEventListener("dragover", function (e) {
          if (!dragPayload || dragPayload.kind !== "move") return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          link.classList.add("cms-drop-target");
        });
        link.addEventListener("dragleave", function () { link.classList.remove("cms-drop-target"); });
        link.addEventListener("drop", function (e) {
          if (!dragPayload || dragPayload.kind !== "move") return;
          e.preventDefault();
          e.stopPropagation();
          link.classList.remove("cms-drop-target");
          var fromId = dragPayload.id;
          var dest = CHROME_DEST[link.dataset.jump];
          dragPayload = null;
          if (fromId && dest) moveInstanceTo(fromId, destinationList(dest), null);
        });
      }
      link.addEventListener("click", function (e) {
        if (skipLayerClick) { skipLayerClick = false; e.preventDefault(); return; }
        var id = link.dataset.jump;
        document.querySelectorAll(".sidebar-link").forEach(function (l) {
          l.classList.remove("active");
        });
        link.classList.add("active");
        // Block instances and site chrome (nav/footer) open the properties rail.
        if (drawerEl && document.getElementById("section-" + id)) {
          openBlockDrawer(id);
          return;
        }
        if (window.cmsSwitchTab) window.cmsSwitchTab("content"); // sections live on the Content tab
        var target = document.getElementById("section-" + id);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
        scrollPreviewToSection(id); // mirror the jump in the live preview
      });
  }

  function __cmsContinueInit() {
    // sidebar search
    var search = document.getElementById("section-search");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.toLowerCase().trim();
        document.querySelectorAll(".sidebar-link").forEach(function (link) {
          var label = link.textContent.toLowerCase();
          link.style.display = !q || label.indexOf(q) !== -1 ? "" : "none";
        });
        // Hide group headers whose links all filtered out, so the tree stays tidy.
        document.querySelectorAll(".sidebar-group").forEach(function (grp) {
          var links = grp.querySelectorAll(".sidebar-link");
          var anyVisible = Array.prototype.some.call(links, function (l) {
            return l.style.display !== "none";
          });
          grp.style.display = links.length && !anyVisible ? "none" : "";
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

    // B1: fold repeated fields into collapsible groups (runs after style panels
    // are wired so the moved panels keep their bindings).
    groupSectionFields();

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
    // Expose the shared dialog helpers so the block palette drawer (wired
    // outside init) can reuse the same focus-trap + overlay behavior.
    window.__cmsOpenDialog = openDialog;
    window.__cmsCloseDialog = closeDialog;
    initBlocks();
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
    var galleryUploadBtn = document.getElementById("gallery-upload-btn");
    var galleryUploadInput = document.getElementById("gallery-upload-input");
    var galleryDrop = document.getElementById("gallery-drop");
    var galleryDropOverlay = document.getElementById("gallery-drop-overlay");
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
      if (galleryPickFieldId.indexOf("style:") === 0) {
        var styleId = galleryPickFieldId.slice(6);
        commitStyle(styleId, "bgImage", url);
        commitStyle(styleId, "bgMode", "image");
        commitStyle(styleId, "bgColor", "");
        var fieldPanel = document.querySelector('[data-style-panel="' + styleId + '"]');
        if (fieldPanel) syncFieldStyleBg(fieldPanel);
        if (currentBlockId && (styleId === blockStyleId() || styleId === layoutStyleId())) {
          syncBlockLayout(currentBlockId);
        }
        closeGallery();
        return;
      }
      if (galleryPickFieldId === "header-logo") {
        setHeaderLogo(url);
        closeGallery();
        return;
      }
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
      if (fieldId === currentReplaceFieldId) refreshReplacePreview();
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
        galleryGrid.querySelectorAll(".gallery-item:not(.gallery-upload-tile)").forEach(function (n) {
          n.classList.toggle("is-selected", n === el);
        });
      }
      showGalleryDetail(item);
    }

    function appendGalleryUploadTile() {
      if (!galleryGrid || (window.CMS && window.CMS.readOnly)) return;
      var tile = document.createElement("button");
      tile.type = "button";
      tile.className = "gallery-item gallery-upload-tile";
      tile.setAttribute("data-gallery-upload-tile", "1");
      tile.innerHTML = '<span class="gallery-upload-plus" aria-hidden="true">+</span><span class="gallery-item-meta">Upload</span>';
      tile.addEventListener("click", function () {
        if (galleryUploadInput) galleryUploadInput.click();
      });
      galleryGrid.appendChild(tile);
    }
    function renderGallery(assets) {
      galleryAssets = assets || [];
      if (!galleryGrid) return;
      galleryGrid.innerHTML = "";
      hideGalleryDetail();
      gallerySelected = null;
      if (useGalleryBtn) useGalleryBtn.hidden = true;
      appendGalleryUploadTile();
      if (!galleryAssets.length) {
        var empty = document.createElement("div");
        empty.className = "gallery-empty";
        empty.textContent = "No images yet. Upload or drop files here — page images will show up too.";
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
        img.addEventListener("error", function () {
          btn.remove();
          if (galleryCount && galleryGrid) {
            var n = galleryGrid.querySelectorAll(".gallery-item:not(.gallery-upload-tile)").length;
            galleryCount.textContent = n ? (n + " image" + (n === 1 ? "" : "s")) : "";
          }
        });
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
      if (!window.CMS.galleryUrl) {
        if (galleryStatus) galleryStatus.textContent = "Gallery isn't available on this page.";
        return;
      }
      if (galleryStatus) galleryStatus.textContent = "Loading…";
      return fetch(window.CMS.galleryUrl, {
        credentials: "same-origin",
        headers: { "X-CSRFToken": window.CMS.csrfToken },
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.data || !res.data.ok) {
            if (galleryStatus) {
              galleryStatus.textContent = (res.data && res.data.error) || "Couldn't load gallery.";
            }
            renderGallery([]);
            return;
          }
          if (galleryStatus) galleryStatus.textContent = "";
          renderGallery(res.data.assets || []);
        })
        .catch(function () {
          if (galleryStatus) galleryStatus.textContent = "Couldn't load gallery.";
        });
    }

    openGalleryForStyle = function (styleId) {
      openGallery(styleId ? ("style:" + styleId) : null);
    };
    openContentGallery = function (fieldId) {
      openGallery(fieldId || null);
    };
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

    function isGalleryImageFile(file) {
      if (!file) return false;
      if (!file.type || file.type.indexOf("image/") === 0) return true;
      return file.type === "application/octet-stream" &&
        /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(file.name || "");
    }
    function selectGalleryByUrl(url) {
      if (!url || !galleryGrid) return;
      var match = null;
      galleryAssets.forEach(function (asset) {
        if (!match && asset.url === url) match = asset;
      });
      if (!match) return;
      var buttons = galleryGrid.querySelectorAll(".gallery-item:not(.gallery-upload-tile)");
      for (var i = 0; i < buttons.length; i++) {
        if ((galleryAssets[i] || {}).url === url) {
          selectGalleryItem(match, buttons[i]);
          return;
        }
      }
    }
    function uploadGalleryFiles(fileList) {
      if (window.CMS && window.CMS.readOnly) return;
      var files = Array.prototype.filter.call(fileList || [], isGalleryImageFile);
      if (!files.length) {
        if (galleryStatus) galleryStatus.textContent = "Please choose image files.";
        return;
      }
      if (!window.CMS.uploadUrl) {
        if (galleryStatus) galleryStatus.textContent = "Image storage isn't configured.";
        return;
      }
      var i = 0;
      var lastUrl = "";
      function next() {
        if (i >= files.length) {
          var done = files.length + " image" + (files.length === 1 ? "" : "s") + " uploaded.";
          if (galleryStatus) galleryStatus.textContent = done;
          var reloaded = reloadGallery();
          if (reloaded && reloaded.then) reloaded.then(function () { selectGalleryByUrl(lastUrl); });
          return;
        }
        var file = files[i++];
        if (galleryStatus) {
          galleryStatus.textContent = files.length > 1
            ? "Uploading " + i + " of " + files.length + "…"
            : "Uploading " + (file.name || "image") + "…";
        }
        var fd = new FormData();
        fd.append("file", file);
        fetch(window.CMS.uploadUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRFToken": window.CMS.csrfToken },
          body: fd,
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            if (!res.ok || !res.data.ok) {
              if (galleryStatus) {
                galleryStatus.textContent = (res.data && res.data.error) || "Upload failed.";
              }
              return;
            }
            lastUrl = res.data.url || lastUrl;
            next();
          })
          .catch(function () {
            if (galleryStatus) galleryStatus.textContent = "Upload failed — try again.";
          });
      }
      next();
    }
    if (galleryUploadBtn && galleryUploadInput) {
      galleryUploadBtn.addEventListener("click", function () { galleryUploadInput.click(); });
      galleryUploadInput.addEventListener("change", function () {
        var chosen = Array.prototype.slice.call(galleryUploadInput.files || []);
        galleryUploadInput.value = "";
        if (!chosen.length) return;
        uploadGalleryFiles(chosen);
      });
    }
    if (galleryDrop) {
      var dragDepth = 0;
      function hasFiles(e) {
        var types = e.dataTransfer && e.dataTransfer.types;
        if (!types) return false;
        return types.indexOf ? types.indexOf("Files") !== -1 : types.contains("Files");
      }
      function showDrop(on) {
        galleryDrop.classList.toggle("is-dragover", on);
        if (galleryDropOverlay) galleryDropOverlay.hidden = !on;
      }
      galleryDrop.addEventListener("dragenter", function (e) {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth += 1;
        showDrop(true);
      });
      galleryDrop.addEventListener("dragover", function (e) {
        if (!hasFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      });
      galleryDrop.addEventListener("dragleave", function (e) {
        if (!hasFiles(e)) return;
        dragDepth = Math.max(0, dragDepth - 1);
        if (!dragDepth) showDrop(false);
      });
      galleryDrop.addEventListener("drop", function (e) {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth = 0;
        showDrop(false);
        uploadGalleryFiles(e.dataTransfer && e.dataTransfer.files);
      });
      document.addEventListener("dragend", function () {
        dragDepth = 0;
        showDrop(false);
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
