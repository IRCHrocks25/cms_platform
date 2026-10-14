/* Locked CMS — live blog-page (index) preview on the blog list screen.
 *
 * Loads the public blog index renderer in an iframe with the *unsaved* blog
 * title, page style, and post-list layout as query params, so the client
 * sees a layout before saving. The Desktop/Mobile toggle narrows the frame
 * to check how each layout stacks on phones.
 */
(function () {
  "use strict";

  var frame = document.getElementById("index-preview-frame");
  var statusEl = document.getElementById("index-preview-status");
  if (!frame) return;

  var base = frame.dataset.base;
  if (!base) return;

  var titleEl = document.getElementById("bs-title");
  var templateEl = document.getElementById("bs-template");
  var layoutEl = document.getElementById("bs-index-layout");

  function buildSrc() {
    var params = [];
    if (templateEl && templateEl.value) params.push("template=" + encodeURIComponent(templateEl.value));
    if (layoutEl && layoutEl.value) params.push("index_layout=" + encodeURIComponent(layoutEl.value));
    if (titleEl && titleEl.value.trim()) params.push("title=" + encodeURIComponent(titleEl.value.trim()));
    return base + (params.length ? "?" + params.join("&") : "");
  }

  function refresh() {
    if (statusEl) statusEl.textContent = "Updating…";
    frame.src = buildSrc();
  }
  frame.addEventListener("load", function () {
    if (statusEl) statusEl.textContent = "Blog page preview";
  });

  var timer = null;
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  }

  if (titleEl) titleEl.addEventListener("input", schedule);
  if (templateEl) templateEl.addEventListener("change", refresh);
  if (layoutEl) layoutEl.addEventListener("change", refresh);

  var widthToggle = document.getElementById("index-viewport-toggle");
  if (widthToggle) {
    widthToggle.addEventListener("click", function (e) {
      var btn = e.target.closest("button");
      if (!btn) return;
      frame.classList.toggle("is-mobile", btn.dataset.viewport === "mobile");
      widthToggle.querySelectorAll("button").forEach(function (b) {
        var on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    });
  }

  refresh();
})();
