/* Locked CMS — live homepage-strip preview on the blog list screen.
 *
 * Loads the chosen blog style's strip fragment in an iframe, reflecting the
 * *unsaved* strip settings (style / heading / count / on-off) via query
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
  var countEl = document.getElementById("bs-count");
  var enabledEl = document.getElementById("bs-enabled");

  function buildSrc() {
    var params = [];
    if (stripStyleEl && stripStyleEl.value) params.push("strip_style=" + encodeURIComponent(stripStyleEl.value));
    if (headingEl) params.push("heading=" + encodeURIComponent(headingEl.value || ""));
    if (countEl && countEl.value) params.push("count=" + encodeURIComponent(countEl.value));
    if (enabledEl) params.push("enabled=" + (enabledEl.checked ? "1" : "0"));
    return base + (params.length ? "?" + params.join("&") : "");
  }

  function refresh() {
    if (statusEl) statusEl.textContent = "Updating…";
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

  [headingEl, countEl].forEach(function (el) {
    if (el) el.addEventListener("input", schedule);
  });
  if (stripStyleEl) stripStyleEl.addEventListener("change", schedule);
  if (enabledEl) enabledEl.addEventListener("change", schedule);

  // Let the reorder module trigger a re-render once a new order is saved.
  window.__cmsRefreshStrip = refresh;

  // Initial render.
  refresh();
})();
