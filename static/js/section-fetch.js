// Tiny vanilla helpers used by the dashboard partials:
//   1. Forms with `data-fetch-form` POST via fetch() and swap their
//      `data-target` element's innerHTML with the response body.
//      CSRF rides along inside the FormData (Django's hidden
//      csrfmiddlewaretoken).
//   2. Buttons with `data-copy="value"` copy that value to the clipboard
//      and briefly flash a "Copied" label. Event delegation on document,
//      so the handler survives partial swaps.
(function () {
  function swap(form, html) {
    var target = document.querySelector(form.getAttribute("data-target"));
    if (!target) return;
    target.innerHTML = html;
  }

  function flashCopied(btn) {
    var icon = btn.querySelector("svg");
    var feedback = btn.querySelector(".copy-btn-feedback");
    if (icon) icon.setAttribute("hidden", "");
    if (feedback) feedback.removeAttribute("hidden");
    clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(function () {
      if (icon) icon.removeAttribute("hidden");
      if (feedback) feedback.setAttribute("hidden", "");
    }, 1500);
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-copy]");
    if (!btn) return;
    event.preventDefault();
    var text = btn.getAttribute("data-copy") || "";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { flashCopied(btn); });
      return;
    }
    // Fallback for non-secure contexts / very old browsers.
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); flashCopied(btn); } catch (e) {}
    document.body.removeChild(ta);
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.hasAttribute("data-fetch-form")) return;
    event.preventDefault();

    var confirmMsg = form.getAttribute("data-confirm");
    if (confirmMsg && !window.confirm(confirmMsg)) return;

    var btns = form.querySelectorAll("button[type=submit]");
    btns.forEach(function (b) { b.disabled = true; });

    fetch(form.getAttribute("action"), {
      method: form.getAttribute("method") || "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    })
      .then(function (resp) { return resp.text(); })
      .then(function (html) { swap(form, html); })
      .catch(function () {
        btns.forEach(function (b) { b.disabled = false; });
        window.alert("Network error. Reload and try again.");
      });
  });
})();
