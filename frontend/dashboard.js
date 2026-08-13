import Alpine from "@alpinejs/csp";

window.Alpine = Alpine;
Alpine.start();

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
