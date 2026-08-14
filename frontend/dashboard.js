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

document.querySelectorAll("[data-menu]").forEach((root) => {
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
