import Alpine from "@alpinejs/csp";

window.Alpine = Alpine;

document.addEventListener("click", (event) => {
  const toggle = event.target.closest("[data-password-toggle]");
  if (!toggle) return;

  const input = document.getElementById(toggle.getAttribute("aria-controls"));
  if (!input) return;

  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  toggle.textContent = reveal ? "Hide" : "Show";
  toggle.setAttribute("aria-pressed", String(reveal));
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-submit-state]");
  if (!form || !form.checkValidity()) return;

  const button = form.querySelector("[data-submit-button]");
  const label = button?.querySelector("[data-submit-text]");
  if (!button) return;

  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  if (label) label.textContent = form.dataset.submitLabel || "Working…";
});

document.querySelectorAll("[data-source-edit-form]").forEach((form) => {
  const textarea = form.querySelector("textarea[data-code-editor]");
  const indicator = document.querySelector("[data-source-unsaved-indicator]");
  if (!textarea || !indicator) return;

  const loadedSource = textarea.value;
  const syncUnsavedState = () => {
    indicator.hidden = textarea.value === loadedSource;
  };

  textarea.addEventListener("input", syncUnsavedState);
  form.addEventListener("submit", () => {
    indicator.hidden = true;
  });
  form.addEventListener("keydown", (event) => {
    if (
      event.defaultPrevented ||
      event.key !== "Enter" ||
      event.metaKey ||
      event.ctrlKey ||
      event.altKey ||
      event.shiftKey
    ) {
      return;
    }

    const input = event.target.closest?.("input");
    const nonSubmittingTypes = new Set([
      "button",
      "checkbox",
      "color",
      "file",
      "radio",
      "range",
      "reset",
      "submit",
    ]);
    if (!input || input.form !== form || nonSubmittingTypes.has(input.type)) return;

    event.preventDefault();
    form.requestSubmit();
  });
});

document.addEventListener(
  "keydown",
  (event) => {
    const isSaveShortcut =
      (event.metaKey || event.ctrlKey) &&
      !event.altKey &&
      !event.shiftKey &&
      event.key.toLowerCase() === "s";
    if (!isSaveShortcut) return;

    const form = document.querySelector("[data-source-edit-form]");
    if (!form) return;

    event.preventDefault();
    form.requestSubmit();
  },
  true,
);

function syncSelectedOptionTitle(select) {
  const option = select.selectedOptions[0];
  select.title = option?.textContent?.trim() || "";
}

document.querySelectorAll("[data-selected-option-title]").forEach((select) => {
  syncSelectedOptionTitle(select);
  select.addEventListener("change", () => syncSelectedOptionTitle(select));
});

const menus = [];

function closeMenu(menu, { returnFocus = false } = {}) {
  if (!menu.isOpen()) return;
  menu.popup.hidden = true;
  menu.trigger.setAttribute("aria-expanded", "false");
  if (returnFocus) menu.trigger.focus();
}

function positionMenu(menu) {
  if (menu.popup.dataset.menuPosition !== "fixed") return;
  const gutter = 12;
  const gap = 8;
  const triggerRect = menu.trigger.getBoundingClientRect();
  const popupRect = menu.popup.getBoundingClientRect();
  const left = Math.min(
    window.innerWidth - popupRect.width - gutter,
    Math.max(gutter, triggerRect.right - popupRect.width),
  );
  const fitsBelow = triggerRect.bottom + gap + popupRect.height <= window.innerHeight - gutter;
  const top = fitsBelow
    ? triggerRect.bottom + gap
    : Math.max(gutter, triggerRect.top - popupRect.height - gap);
  menu.popup.style.left = `${left}px`;
  menu.popup.style.top = `${top}px`;
}

function openMenu(menu, focusIndex = 0) {
  menus.forEach((candidate) => {
    if (candidate !== menu) closeMenu(candidate);
  });
  menu.popup.hidden = false;
  menu.trigger.setAttribute("aria-expanded", "true");
  positionMenu(menu);
  menu.items[focusIndex]?.focus();
}

function initializeMenus(scope = document) {
scope.querySelectorAll("[data-menu]").forEach((root) => {
  const trigger = root.querySelector("[data-menu-trigger]");
  const popup = root.querySelector('[role="menu"]');
  const items = Array.from(root.querySelectorAll("[data-menu-item]"));
  if (!trigger || !popup || items.length === 0) return;

  const menu = {
    root,
    trigger,
    popup,
    items,
    isOpen: () => trigger.getAttribute("aria-expanded") === "true",
  };
  menus.push(menu);

  trigger.addEventListener("click", () => {
    if (menu.isOpen()) closeMenu(menu, { returnFocus: true });
    else openMenu(menu);
  });

  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(menu, event.key === "ArrowUp" ? items.length - 1 : 0);
    } else if (event.key === "Escape" && menu.isOpen()) {
      event.preventDefault();
      closeMenu(menu, { returnFocus: true });
    }
  });

  popup.addEventListener("keydown", (event) => {
    const current = items.indexOf(document.activeElement);
    let next = current;
    if (event.key === "ArrowDown") next = (current + 1) % items.length;
    else if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(menu, { returnFocus: true });
      return;
    } else if (event.key === "Tab") {
      closeMenu(menu);
      return;
    } else return;

    event.preventDefault();
    items[next].focus();
  });

  popup.addEventListener("click", (event) => {
    if (event.target.closest("[data-menu-item]")) closeMenu(menu);
  });
});
}

initializeMenus();

document.querySelectorAll("[data-site-search]").forEach((form) => {
  const input = form.elements.q;
  const status = form.elements.status;
  const results = document.querySelector("[data-site-results]");
  const clear = form.querySelector("[data-site-clear]");
  if (!input || !results) return;
  let timer;
  let controller;
  let revision = 0;
  let useFallback = false;

  function searchUrl() {
    const url = new URL(form.action || window.location.href);
    url.searchParams.set("status", status.value);
    if (input.value.trim()) url.searchParams.set("q", input.value.trim());
    else url.searchParams.delete("q");
    return url;
  }

  function syncFilters() {
    clear.hidden = !input.value.trim() && status.value === "all";
    form.querySelectorAll("[data-site-status] a").forEach((link) => {
      const url = searchUrl();
      const value = new URL(link.href).searchParams.get("status");
      url.searchParams.set("status", value);
      link.href = url.href;
      link.classList.toggle("active", value === status.value);
    });
  }

  async function search(currentRevision) {
    const url = searchUrl();
    controller = new AbortController();
    results.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error("Search failed");
      const html = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = html.querySelector("[data-site-results]");
      if (!replacement) throw new Error("Search results missing");
      if (currentRevision !== revision) return;
      menus.forEach((menu) => closeMenu(menu));
      for (let index = menus.length - 1; index >= 0; index--) {
        if (results.contains(menus[index].root)) menus.splice(index, 1);
      }
      results.replaceChildren(...replacement.childNodes);
      initializeMenus(results);
      window.history.replaceState(null, "", url);
    } catch (error) {
      if (error.name !== "AbortError" && currentRevision === revision) {
        // Keep a working server-rendered fallback if the request fails.
        useFallback = true;
        form.querySelector("[data-site-search-submit]").hidden = false;
      }
    } finally {
      if (currentRevision === revision) results.removeAttribute("aria-busy");
    }
  }

  function schedule(delay = 250) {
    clearTimeout(timer);
    controller?.abort();
    const currentRevision = ++revision;
    syncFilters();
    timer = setTimeout(() => search(currentRevision), delay);
  }

  input.addEventListener("input", () => schedule(input.value ? 250 : 0));
  input.addEventListener("search", () => schedule(0));
  form.addEventListener("submit", (event) => {
    if (useFallback) return;
    event.preventDefault();
    schedule(0);
  });
  form.querySelector("[data-site-status]").addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    status.value = new URL(link.href).searchParams.get("status");
    schedule(0);
  });
  clear.addEventListener("click", (event) => {
    event.preventDefault();
    input.value = "";
    status.value = "all";
    schedule(0);
  });
  form.querySelector("[data-site-search-submit]").hidden = true;
  syncFilters();
});

document.addEventListener("click", (event) => {
  menus.forEach((menu) => {
    if (menu.isOpen() && !menu.root.contains(event.target)) closeMenu(menu);
  });
});

window.addEventListener("resize", () => menus.forEach((menu) => closeMenu(menu)));
window.addEventListener("scroll", () => menus.forEach((menu) => closeMenu(menu)), true);

document.addEventListener("focusin", (event) => {
  event.target.closest?.("[data-sidebar-label]")?.classList.add("is-label-visible");
});

document.addEventListener("focusout", (event) => {
  event.target.closest?.("[data-sidebar-label]")?.classList.remove("is-label-visible");
});

Alpine.start();
