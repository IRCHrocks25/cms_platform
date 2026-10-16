/* Locked CMS — live homepage-strip preview on the blog list screen.
 *
 * Loads the chosen blog style's strip fragment in an iframe, reflecting the
 * *unsaved* strip settings (style / heading / eyebrow / subtitle / count / on-off) via query
 * params, plus the current featured order. Reuses the same server-side strip
 * renderer the public homepage uses, so the preview matches the live strip.
 *
 * Exposes window.__cmsRefreshStrip so blog_reorder.js can re-render after a
 * drag-reorder persists the new order.
 */
(function () {
  "use strict";

  var frame = document.getElementById("strip-preview-frame");
  var statusEl = document.getElementById("strip-preview-status");
  if (!frame) return;

  var base = frame.dataset.base;
  if (!base) return;

  var stripStyleEl = document.getElementById("bs-strip-style");
  var headingEl = document.getElementById("bs-heading");
  var eyebrowEl = document.getElementById("bs-eyebrow");
  var subtitleEl = document.getElementById("bs-subtitle");
  var countEl = document.getElementById("bs-count");
  var enabledEl = document.getElementById("bs-enabled");
  var form = document.getElementById("blog-settings-form");
  var targetEl = document.getElementById("strip-preview-target");
  var markerEls = Array.from(document.querySelectorAll('select[name^="strip_layout_"]'));
  var activeMarker = markerEls.find(function (el) { return el.name === "strip_layout_home_0"; }) || markerEls[0] || null;

  function buildSrc() {
    var params = [];
    if (stripStyleEl && stripStyleEl.value) params.push("strip_style=" + encodeURIComponent(stripStyleEl.value));
    if (headingEl) params.push("heading=" + encodeURIComponent(headingEl.value || ""));
    if (eyebrowEl) params.push("eyebrow=" + encodeURIComponent(eyebrowEl.value || ""));
    if (subtitleEl) params.push("subtitle=" + encodeURIComponent(subtitleEl.value || ""));
    if (countEl && countEl.value) params.push("count=" + encodeURIComponent(countEl.value));
    if (enabledEl) params.push("enabled=" + (enabledEl.checked ? "1" : "0"));
    if (activeMarker) {
      params.push("marker=" + encodeURIComponent(activeMarker.name.slice("strip_layout_".length)));
      params.push(encodeURIComponent(activeMarker.name) + "=" + encodeURIComponent(activeMarker.value));
    }
    return base + (params.length ? "?" + params.join("&") : "");
  }

  function refresh() {
    if (statusEl) statusEl.textContent = "Updating…";
    if (targetEl) targetEl.textContent = activeMarker
      ? "Previewing " + (activeMarker.labels && activeMarker.labels[0] ? activeMarker.labels[0].textContent : activeMarker.name.replace("strip_layout_", "").replaceAll("_", " "))
      : "Previewing the homepage strip layout";
    frame.src = buildSrc();
  }
  frame.addEventListener("load", function () {
    if (statusEl) statusEl.textContent = "Homepage strip preview";
  });

  var timer = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  }

  [headingEl, eyebrowEl, subtitleEl, countEl].forEach(function (el) {
    if (el) el.addEventListener("input", schedule);
  });
  if (stripStyleEl) stripStyleEl.addEventListener("change", function () {
    activeMarker = null;
    schedule();
  });
  markerEls.forEach(function (el) {
    el.addEventListener("change", function () {
      activeMarker = el;
      schedule();
    });
  });
  if (enabledEl) enabledEl.addEventListener("change", schedule);
  if (form) form.addEventListener("reset", function () {
    activeMarker = markerEls.find(function (el) { return el.name === "strip_layout_home_0"; }) || markerEls[0] || null;
    schedule();
  });

  // Let the reorder module trigger a re-render once a new order is saved.
  window.__cmsRefreshStrip = refresh;

  // Initial render.
  refresh();
})();
