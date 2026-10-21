export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const numberFormatter = new Intl.NumberFormat("fr-FR");
const dateFormatter = new Intl.DateTimeFormat("fr-FR", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

let loadingCount = 0;

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function formatNumber(value) {
  return numberFormatter.format(Number(value) || 0);
}

export function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${formatNumber(bytes)} o`;
  const units = ["Ko", "Mo", "Go", "To"];
  let size = bytes / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} ${units[index]}`;
}

export function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date inconnue";
  return dateFormatter.format(date);
}

export function providerId(value) {
  const label = String(value || "").toLowerCase();
  if (label.includes("gemini")) return "gemini";
  if (label.includes("edge")) return "edge";
  if (label.includes("fish")) return "fish";
  if (label.includes("mimo")) return "mimo";
  return "edge";
}

export function providerLabel(value) {
  const id = providerId(value);
  return { gemini: "Gemini", edge: "Edge", mimo: "MiMo", fish: "Fish" }[id];
}

export function formatLabel(value) {
  const ext = String(value || "").replace(".", "").toLowerCase();
  return { wav: "WAV", mp3: "MP3", ogg: "OGG", flac: "FLAC", m4a: "M4A" }[ext] || ext.toUpperCase();
}

export function truncate(value, length = 140) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= length) return text;
  return `${text.slice(0, length - 1).trimEnd()}…`;
}

export function debounce(callback, delay = 250) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

export function setLoading(active) {
  loadingCount = Math.max(0, loadingCount + (active ? 1 : -1));
  $("#loading-bar")?.classList.toggle("is-active", loadingCount > 0);
}

export function setBusy(button, busy) {
  if (!button) return;
  if (busy) {
    if (!button.dataset.busyStored) {
      button.dataset.busyStored = "1";
      button.dataset.wasDisabled = button.disabled ? "1" : "";
    }
    button.disabled = true;
    button.classList.add("is-spinning");
  } else {
    button.disabled = button.dataset.wasDisabled === "1";
    button.classList.remove("is-spinning");
    delete button.dataset.busyStored;
    delete button.dataset.wasDisabled;
  }
}

export function showStatus(element, message, type = "") {
  if (!element) return;
  element.hidden = !message;
  element.textContent = message || "";
  element.classList.toggle("is-error", type === "error");
  element.classList.toggle("is-success", type === "success");
}

export function toast(message, type = "info", duration = 4200) {
  const region = $("#toast-region");
  if (!region) return;
  const item = document.createElement("div");
  item.className = `toast is-${type}`;
  item.textContent = message;
  item.addEventListener("click", () => dismissToast(item));
  region.append(item);

  /* requestAnimationFrame plutôt que getComputedStyle : la transition
     démarre dès la frame suivante, sans forcer de reflow synchrone. */
  requestAnimationFrame(() => item.classList.add("is-shown"));

  const timer = window.setTimeout(() => dismissToast(item), duration);
  toastEntries.set(item, { timer });
}

const toastEntries = new Map();

function dismissToast(item) {
  const entry = toastEntries.get(item);
  if (!entry || entry.dismissed) return;
  entry.dismissed = true;
  window.clearTimeout(entry.timer);
  item.classList.add("is-hiding");
  item.addEventListener(
    "transitionend",
    () => {
      toastEntries.delete(item);
      item.remove();
    },
    { once: true },
  );
  /* Sécurité si transitionend ne se déclenche pas (onglet en arrière-plan). */
  window.setTimeout(() => {
    if (item.isConnected) item.remove();
    toastEntries.delete(item);
  }, 600);
}

export function emptyState(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

/* Animation d'entrée en cascade des cartes après un rendu de liste.
   Une seule écriture de style par carte, sur une classe prédéfinie. */
export function animateCards(container, selector = ".history-card, .catalog-card") {
  if (!container) return;
  const cards = container.querySelectorAll(selector);
  let index = 0;
  for (const card of cards) {
    card.style.setProperty("--stagger-index", String(index++));
    card.classList.add("card-in");
  }
  /* Le nettoyage évite de garder des classes d'animation sur les re-rendus
     suivants (innerHTML remplace les nœuds, mais un render partiel non). */
  if (cards.length) {
    window.setTimeout(() => {
      for (const card of cards) card.classList.remove("card-in");
    }, 1200);
  }
}

/* Dialogs ---------------------------------------------------------------- */

const confirmDialog = $("#confirm-dialog");
const confirmTitle = $("#confirm-title");
const confirmMessage = $("#confirm-message");
const confirmAccept = $("#confirm-accept");
const confirmCancel = $("#confirm-cancel");

let confirmResolve = null;

function settleConfirm(value) {
  if (!confirmResolve) return;
  const resolve = confirmResolve;
  confirmResolve = null;
  if (confirmDialog?.open) confirmDialog.close();
  resolve(value);
}

export function confirmAction({
  title = "Confirmer",
  message = "",
  acceptLabel = "Confirmer",
  danger = true,
} = {}) {
  if (!confirmDialog) return Promise.resolve(false);
  if (confirmDialog.open) settleConfirm(false);
  confirmTitle.textContent = title;
  confirmMessage.textContent = message;
  confirmAccept.textContent = acceptLabel;
  confirmAccept.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
  confirmDialog.showModal();
  return new Promise((resolve) => {
    confirmResolve = resolve;
  });
}

confirmAccept?.addEventListener("click", () => settleConfirm(true));
confirmCancel?.addEventListener("click", () => settleConfirm(false));
confirmDialog?.addEventListener("cancel", (event) => {
  event.preventDefault();
  settleConfirm(false);
});
confirmDialog?.addEventListener("click", (event) => {
  if (event.target === confirmDialog) settleConfirm(false);
});

const promptDialog = $("#prompt-dialog");
const promptForm = $("#prompt-form");
const promptTitle = $("#prompt-title");
const promptMessage = $("#prompt-message");
const promptLabel = $("#prompt-label");
const promptInput = $("#prompt-input");
const promptCancel = $("#prompt-cancel");
const promptAccept = $("#prompt-accept");

let promptResolve = null;

function settlePrompt(value) {
  if (!promptResolve) return;
  const resolve = promptResolve;
  promptResolve = null;
  if (promptDialog?.open) promptDialog.close();
  resolve(value);
}

export function promptAction({
  title = "Renommer",
  message = "",
  label = "Nom",
  value = "",
  acceptLabel = "Valider",
} = {}) {
  if (!promptDialog) return Promise.resolve(null);
  if (promptDialog.open) settlePrompt(null);
  promptTitle.textContent = title;
  promptMessage.textContent = message;
  promptLabel.textContent = label;
  promptInput.value = value;
  promptAccept.textContent = acceptLabel;
  promptDialog.showModal();
  window.setTimeout(() => {
    promptInput.focus();
    promptInput.select();
  }, 30);
  return new Promise((resolve) => {
    promptResolve = resolve;
  });
}

promptForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  const value = promptInput.value.trim();
  if (!value) {
    promptInput.focus();
    return;
  }
  settlePrompt(value);
});
promptCancel?.addEventListener("click", () => settlePrompt(null));
promptDialog?.addEventListener("cancel", (event) => {
  event.preventDefault();
  settlePrompt(null);
});
promptDialog?.addEventListener("click", (event) => {
  if (event.target === promptDialog) settlePrompt(null);
});

/* Sidebar ---------------------------------------------------------------- */

export function openSidebar() {
  $("#sidebar")?.classList.add("is-open");
  const scrim = $("#scrim");
  if (scrim) scrim.hidden = false;
  document.body.classList.add("no-scroll");
}

export function closeSidebar() {
  $("#sidebar")?.classList.remove("is-open");
  const scrim = $("#scrim");
  if (scrim) scrim.hidden = true;
  document.body.classList.remove("no-scroll");
}
