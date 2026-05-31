/* Locked CMS — featured-posts drag-to-reorder on the blog list page.
 * Posts the new order to the reorder endpoint as JSON.
 */
(function () {
  "use strict";

  var list = document.getElementById("reorder-list");
  var status = document.getElementById("reorder-status");
  var cfg = window.CMS_BLOG || {};
  if (!list) return;

  var dragEl = null;

  function items() {
    return Array.prototype.slice.call(list.querySelectorAll(".reorder-item"));
  }

  list.addEventListener("dragstart", function (e) {
    var li = e.target.closest(".reorder-item");
    if (!li) return;
    dragEl = li;
    li.classList.add("is-dragging");
    e.dataTransfer.effectAllowed = "move";
  });

  list.addEventListener("dragend", function () {
    if (dragEl) dragEl.classList.remove("is-dragging");
    dragEl = null;
    save();
  });

  list.addEventListener("dragover", function (e) {
    e.preventDefault();
    if (!dragEl) return;
    var after = afterElement(e.clientY);
    if (after == null) {
      list.appendChild(dragEl);
    } else {
      list.insertBefore(dragEl, after);
    }
  });

  function afterElement(y) {
    var els = items().filter(function (el) { return el !== dragEl; });
    var closest = null;
    var closestOffset = Number.NEGATIVE_INFINITY;
    els.forEach(function (el) {
      var box = el.getBoundingClientRect();
      var offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closestOffset) {
        closestOffset = offset;
        closest = el;
      }
    });
    return closest;
  }

  function save() {
    var order = items().map(function (el) { return parseInt(el.dataset.pk, 10); });
    if (status) status.textContent = "Saving order…";
    fetch(list.dataset.reorderUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": cfg.csrfToken,
      },
      body: JSON.stringify({ order: order }),
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
      .then(function () {
        if (status) status.textContent = "Order saved.";
        // Re-render the live strip preview with the new order.
        if (typeof window.__cmsRefreshStrip === "function") window.__cmsRefreshStrip();
      })
      .catch(function () { if (status) status.textContent = "Couldn't save order — reload and retry."; });
  }
})();
