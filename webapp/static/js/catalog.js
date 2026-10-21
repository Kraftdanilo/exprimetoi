import { api } from "./api.js";
import { state } from "./store.js";
import { openSettings } from "./settings.js";
import {
  $,
  animateCards,
  escapeHtml,
  setBusy,
  toast,
} from "./ui.js";

const list = $("#catalog-list");
const refresh = $("#catalog-refresh");

function badge(entry) {
  if (!entry.needsKey) return '<span class="status-badge is-ready">Sans clé</span>';
  return entry.keyConfigured
    ? '<span class="status-badge is-ready">Clé configurée</span>'
    : '<span class="status-badge is-missing">Clé requise</span>';
}

function card(entry) {
  const models = (entry.models || [])
    .map((model) => `<span>${escapeHtml(model)}</span>`)
    .join("");
  const site = entry.site
    ? `<a href="${escapeHtml(entry.site)}" target="_blank" rel="noopener noreferrer">${escapeHtml(entry.siteLabel || entry.site)}</a>`
    : "—";
  return `
    <article class="catalog-card">
      <header>
        <div>
          <h3>${escapeHtml(entry.name)}</h3>
          <p class="catalog-desc">${escapeHtml(entry.provider || "")}</p>
        </div>
        ${badge(entry)}
      </header>
      <div class="catalog-row">
        <span>Site</span>
        ${site}
      </div>
      <div class="catalog-row">
        <span>Endpoint</span>
        <code>${escapeHtml(entry.endpoint || "—")}</code>
      </div>
      <div class="catalog-row">
        <span>Modèles</span>
        <div class="catalog-tags">${models || "<span>Aucun modèle</span>"}</div>
      </div>
      <div class="catalog-row">
        <span>Clé d'accès</span>
        <code>${escapeHtml(entry.apiKeyName || "Aucune clé nécessaire")}</code>
      </div>
      <div class="catalog-row">
        <span>Voix</span>
        <code>${escapeHtml(entry.voices || "—")}</code>
      </div>
      <div class="catalog-row">
        <span>Notes</span>
        <code>${escapeHtml(entry.notes || "")}</code>
      </div>
      ${
        entry.needsKey
          ? `<button class="btn btn-secondary" type="button" data-configure>${entry.keyConfigured ? "Modifier la configuration" : "Configurer"}</button>`
          : ""
      }
    </article>
  `;
}

function render() {
  if (!list || !state.catalog) return;
  const catalog = state.catalog.catalog || [];
  list.innerHTML = catalog.length
    ? catalog.map(card).join("")
    : '<div class="empty-state">Catalogue indisponible.</div>';
  animateCards(list, ".catalog-card");
}

export async function loadCatalog({ force = false } = {}) {
  if (state.catalogLoaded && !force) {
    render();
    return;
  }
  setBusy(refresh, true);
  try {
    state.catalog = await api.catalog();
    state.catalogLoaded = true;
    render();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(refresh, false);
  }
}

export function initCatalog() {
  refresh?.addEventListener("click", () => loadCatalog({ force: true }));
  list?.addEventListener("click", (event) => {
    if (event.target.closest("[data-configure]")) openSettings();
  });
  window.addEventListener("settings:updated", () => {
    state.catalogLoaded = false;
  });
}
