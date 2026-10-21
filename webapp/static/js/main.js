import { api } from "./api.js";
import { state, loadPreferences } from "./store.js";
import { initSettings, hydrateSettings, updateSetupBanner } from "./settings.js";
import { initStudio } from "./studio.js";
import { initHistory, loadHistory } from "./history.js";
import { initCatalog, loadCatalog } from "./catalog.js";
import {
  $,
  $$,
  closeSidebar,
  openSidebar,
  setLoading,
  toast,
} from "./ui.js";

const ROUTES = {
  studio: { page: "page-studio", title: "Studio vocal" },
  history: { page: "page-history", title: "Historique" },
  catalog: { page: "page-catalog", title: "Catalogue API" },
};

function currentRoute() {
  const hash = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  return ROUTES[hash] ? hash : "studio";
}

function navigate(route) {
  const key = ROUTES[route] ? route : "studio";
  const target = ROUTES[key];
  for (const [name, meta] of Object.entries(ROUTES)) {
    const page = document.getElementById(meta.page);
    if (page) page.hidden = name !== key;
  }
  for (const link of $$(".nav-link")) {
    const active = link.dataset.route === key;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${target.title} — Fautexprimer`;
  closeSidebar();
  window.scrollTo({ top: 0, behavior: "auto" });
  /* Relance l'animation d'entrée CSS (page-in) : on retire puis on rejette
     la classe au frame suivant, sans styles inline ni reflow forcé. */
  const page = document.getElementById(target.page);
  if (page) {
    page.classList.remove("page-enter");
    requestAnimationFrame(() => page.classList.add("page-enter"));
  }
  if (key === "history") loadHistory();
  if (key === "catalog") loadCatalog();
}

function applyHealth(health) {
  state.health = health;
  const dot = $("#health-dot");
  const label = $("#health-label");
  const title = $("#health-title");
  if (dot) dot.className = "status-dot is-ready";
  if (title) title.textContent = "Service local";
  if (label) {
    const voices = Number(health.edgeVoices || 0);
    label.textContent = voices ? `Prêt · ${voices} voix Edge` : "Prêt";
    label.title = health.version ? `Version ${health.version}` : "";
  }
  window.dispatchEvent(new CustomEvent("health:updated", { detail: health }));
}

function markHealthOffline() {
  const dot = $("#health-dot");
  const label = $("#health-label");
  if (dot) dot.className = "status-dot is-offline";
  if (label) label.textContent = "Service injoignable";
  toast("Le service local ne répond pas.", "error", 7000);
}

async function refreshHealth() {
  try {
    applyHealth(await api.health());
  } catch {
    markHealthOffline();
  }
}

function bindNavigation() {
  $("#sidebar-toggle")?.addEventListener("click", openSidebar);
  $("#sidebar-close")?.addEventListener("click", closeSidebar);
  $("#scrim")?.addEventListener("click", closeSidebar);
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });
  window.addEventListener("hashchange", () => navigate(currentRoute()));
  window.addEventListener("health:refresh", refreshHealth);
  window.addEventListener("settings:updated", () => {
    updateSetupBanner(state.settings);
  });
}

async function boot() {
  loadPreferences();
  bindNavigation();
  initSettings();
  initHistory();
  initCatalog();

  setLoading(true);
  const [configResult, settingsResult, healthResult] = await Promise.allSettled([
    api.config(),
    api.settings(),
    api.health(),
  ]);

  if (configResult.status === "fulfilled") {
    state.config = configResult.value;
  } else {
    toast(`Configuration indisponible : ${configResult.reason?.message || "erreur inconnue"}`, "error", 8000);
  }

  if (settingsResult.status === "fulfilled") {
    state.settings = settingsResult.value;
  } else {
    toast(`Paramètres indisponibles : ${settingsResult.reason?.message || "erreur inconnue"}`, "error", 8000);
  }

  if (healthResult.status === "fulfilled") {
    applyHealth(healthResult.value);
  } else {
    markHealthOffline();
  }

  initStudio();
  hydrateSettings();
  if (!state.settings && state.health) {
    updateSetupBanner({
      geminiKeyConfigured: state.health.geminiKey,
      mimoKeyConfigured: state.health.mimoKey,
    });
  }

  navigate(currentRoute());
  setLoading(false);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}
