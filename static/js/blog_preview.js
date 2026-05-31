/* Locked CMS — blog live-preview bridge (runs INSIDE the preview iframe).
 *
 * Mirrors the main editor's preview bridge but scoped to a single blog post.
 * The dashboard (parent) sends `apply-content` with the post fields; this
 * patches the matching `[data-blog-edit]` nodes in place. Clicking an
 * editable region asks the parent to focus the matching form field.
 *
 * Messages use source "cms-blog-editor" (parent) / "cms-blog-preview" (here)
 * — keep these in lockstep with blog_editor.js.
 */
(function () {
  "use strict";

  var FROM_PARENT = "cms-blog-editor";
  var FROM_SELF = "cms-blog-preview";

  function toggle(el, show) {
    if (show) el.removeAttribute("hidden");
    else el.setAttribute("hidden", "");
  }

  function patchField(field, value) {
    var nodes = document.querySelectorAll('[data-blog-edit="' + field + '"]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (field === "body") {
        // `value` is already server-sanitized (see blog_editor.js round-trip),
        // so the preview body matches the public render exactly.
        el.innerHTML = value || "";
      } else if (field === "cover_image") {
        if (value) {
          el.setAttribute("src", value);
          toggle(el, true);
        } else {
          el.removeAttribute("src");
          toggle(el, false);
        }
      } else if (field === "author") {
        el.textContent = value ? "By " + value : "";
        toggle(el, !!value);
        // The separator dot only shows when there's an author beside the date.
        var dots = document.querySelectorAll('[data-blog-meta="sep"]');
        for (var d = 0; d < dots.length; d++) toggle(dots[d], !!value);
      } else if (field === "publish_date") {
        // `value` arrives pre-formatted to match Django's "F j, Y".
        el.textContent = value || "";
        toggle(el, !!value);
      } else {
        el.textContent = value || "";
      }
    }
  }

  function apply(content) {
    if (!content) return;
    Object.keys(content).forEach(function (field) {
      patchField(field, content[field]);
    });
  }

  window.addEventListener("message", function (e) {
    var data = e.data || {};
    if (data.source !== FROM_PARENT) return;
    if (data.type === "apply-content") apply(data.content);
  });

  // Click an editable region → ask the parent to focus that form field.
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-blog-edit]");
    if (!el) return;
    parent.postMessage(
      { source: FROM_SELF, type: "focus-field", field: el.getAttribute("data-blog-edit") },
      "*"
    );
  });

  // Tell the parent we're ready for an initial content push.
  parent.postMessage({ source: FROM_SELF, type: "ready" }, "*");
})();
