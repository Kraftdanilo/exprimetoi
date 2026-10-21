import { api } from "./api.js";
import { state } from "./store.js";
import { applyStoredSynthesis, applyStoredVoice, setMimoClip } from "./studio.js";
import {
  $,
  animateCards,
  confirmAction,
  debounce,
  emptyState,
  escapeHtml,
  formatBytes,
  formatDate,
  formatLabel,
  formatNumber,
  promptAction,
  providerLabel,
  setBusy,
  toast,
  truncate,
} from "./ui.js";

const dom = {
  stats: $("#history-stats"),
  search: $("#history-search"),
  syntheses: $("#syntheses-list"),
  voices: $("#voices-list"),
  clearSyntheses: $("#clear-syntheses"),
  clearVoices: $("#clear-voices"),
  refresh: $("#history-refresh"),
};

let previewAudio = null;

function playPreview(url) {
  if (!url) return;
  if (previewAudio) previewAudio.pause();
  previewAudio = new Audio(url);
  previewAudio.play().catch(() => toast("Lecture impossible pour ce fichier.", "error"));
}

function query() {
  return (dom.search?.value || "").trim().toLowerCase();
}

function matches(values) {
  const needle = query();
  if (!needle) return true;
  return values.join(" ").toLowerCase().includes(needle);
}

function renderStats(stats = {}) {
  if (!dom.stats) return;
  const cards = [
    [formatNumber(stats.syntheses), "synthèses"],
    [formatNumber(stats.voices), "clips de référence"],
    [formatBytes(stats.synthesisBytes), "audio généré"],
    [formatBytes(stats.voiceBytes), "clips importés"],
  ];
  dom.stats.innerHTML = cards
    .map(
      ([value, label]) =>
        `<div class="stat-card"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`,
    )
    .join("");
}

function icon(name) {
  const paths = {
    play: '<path d="m9 7 8 5-8 5V7Z"/>',
    download: '<path d="M12 4v11m0 0 4-4m-4 4-4-4M5 20h14"/>',
    reuse: '<path d="M4 10a8 8 0 0 1 13.5-5.5L20 7M20 4v3h-3"/><path d="M20 14a8 8 0 0 1-13.5 5.5L4 17M4 20v-3h3"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m14 6 4 4"/>',
    mic: '<path d="M12 4a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V7a3 3 0 0 0-3-3Z"/><path d="M6 11v1a6 6 0 0 0 12 0v-1M12 18v3"/>',
    audio: '<path d="M4 12h2l2-5 3 10 3-8 2 3h4"/>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.audio}</svg>`;
}

function synthesisCard(item) {
  const provider = providerLabel(item.provider);
  const id = Number(item.id);
  const text = item.text || "Sans texte";
  const transcript = item.transcript ? `« ${truncate(item.transcript, 180)} »` : "";
  return `
    <article class="history-card" data-id="${id}">
      <span class="history-icon">${icon("audio")}</span>
      <div class="history-main">
        <h3 class="history-title">${escapeHtml(truncate(text, 160))}</h3>
        <div class="history-meta">
          <span class="badge">${escapeHtml(provider)}</span>
          <span>${escapeHtml(item.voice || "Voix inconnue")}</span>
          <span>${escapeHtml(formatLabel(item.format))}</span>
          <span>${escapeHtml(formatDate(item.createdAt))}</span>
          <span>${Number(item.speed || 1).toFixed(2)}×</span>
        </div>
        ${transcript ? `<p class="transcript-preview">${escapeHtml(transcript)}</p>` : ""}
      </div>
      <div class="history-actions">
        <button class="icon-btn" type="button" data-action="preview" data-url="${escapeHtml(api.historyAudioUrl(id))}" title="Écouter" aria-label="Écouter">${icon("play")}</button>
        <button class="icon-btn" type="button" data-action="reuse" title="Réutiliser dans le studio" aria-label="Réutiliser">${icon("reuse")}</button>
        <button class="icon-btn" type="button" data-action="use-as-mimo" title="Utiliser comme voix MiMo" aria-label="Utiliser comme voix MiMo">${icon("mic")}</button>
        <a class="icon-btn" href="${escapeHtml(api.historyAudioUrl(id, true))}" download title="Télécharger" aria-label="Télécharger">${icon("download")}</a>
        <button class="icon-btn" type="button" data-action="delete" title="Supprimer" aria-label="Supprimer">${icon("trash")}</button>
      </div>
    </article>
  `;
}

function voiceCard(item) {
  const source = item.source === "recorded" ? "Enregistré" : "Importé";
  const id = Number(item.id);
  return `
    <article class="history-card" data-id="${id}">
      <span class="history-icon">${icon("mic")}</span>
      <div class="history-main">
        <h3 class="history-title">${escapeHtml(item.name || item.originalName || "Clip sans nom")}</h3>
        <div class="history-meta">
          <span class="badge">${escapeHtml(source)}</span>
          <span>${escapeHtml(formatLabel(item.format))}</span>
          <span>${escapeHtml(formatDate(item.createdAt))}</span>
        </div>
      </div>
      <div class="history-actions">
        <button class="icon-btn" type="button" data-action="preview" data-url="${escapeHtml(api.voiceAudioUrl(id))}" title="Écouter" aria-label="Écouter">${icon("play")}</button>
        <button class="icon-btn" type="button" data-action="use" title="Utiliser avec MiMo" aria-label="Utiliser">${icon("reuse")}</button>
        <button class="icon-btn" type="button" data-action="rename" title="Renommer" aria-label="Renommer">${icon("edit")}</button>
        <button class="icon-btn" type="button" data-action="delete" title="Supprimer" aria-label="Supprimer">${icon("trash")}</button>
      </div>
    </article>
  `;
}

function render() {
  if (!state.history) return;
  const history = state.history;
  renderStats(history.stats);

  const syntheses = (history.syntheses || []).filter((item) =>
    matches([item.text, item.transcript, item.voice, item.provider, item.format]),
  );
  const voices = (history.voices || []).filter((item) =>
    matches([item.name, item.originalName, item.source, item.format]),
  );

  if (dom.syntheses) {
    dom.syntheses.innerHTML = syntheses.length
      ? syntheses.map(synthesisCard).join("")
      : emptyState(
          query()
            ? "Aucune synthèse ne correspond à cette recherche."
            : "Aucune synthèse pour le moment. Générez votre première voix dans le studio.",
        );
  }
  if (dom.voices) {
    dom.voices.innerHTML = voices.length
      ? voices.map(voiceCard).join("")
      : emptyState(
          query()
            ? "Aucun clip ne correspond à cette recherche."
            : "Aucun clip de référence. Importez ou enregistrez un clip dans le studio.",
        );
  }

  if (dom.clearSyntheses) dom.clearSyntheses.disabled = !(history.syntheses || []).length;
  if (dom.clearVoices) dom.clearVoices.disabled = !(history.voices || []).length;

  /* Cascade d'entrée des cartes : seulement sur un rendu venant du réseau
     (pas à chaque frappe de recherche, pour rester fluide). */
  if (!dom.search?.value) {
    animateCards(dom.syntheses);
    animateCards(dom.voices);
  }
}

export async function loadHistory({ force = false } = {}) {
  if (state.historyLoaded && !force) {
    render();
    return;
  }
  setBusy(dom.refresh, true);
  try {
    state.history = await api.history();
    state.historyLoaded = true;
    render();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setBusy(dom.refresh, false);
  }
}

async function useSynthesisAsVoice(item) {
  const id = Number(item.id);
  setBusy(buttonInScope(id, "use-as-mimo"), true);
  try {
    const result = await api.synthesisAsVoice(id, item.voice ? `Voix ${item.voice}` : "Synthèse réutilisée");
    state.mimoClip = result.voice;
    applyStoredVoice(result.voice);
    toast("Synthèse ajoutée aux clips et sélectionnée pour MiMo.", "success");
    window.location.hash = "#/";
    await loadHistory({ force: true });
  } catch (error) {
    toast(error.message, "error", 7000);
  } finally {
    setBusy(buttonInScope(id, "use-as-mimo"), false);
  }
}

function buttonInScope(id, action) {
  return document.querySelector(
    `.history-card[data-id="${id}"] button[data-action="${action}"]`,
  );
}

async function deleteSynthesis(id) {
  const confirmed = await confirmAction({
    title: "Supprimer cette synthèse ?",
    message: "Le fichier audio sera supprimé du disque. Cette action est définitive.",
    acceptLabel: "Supprimer",
  });
  if (!confirmed) return;
  try {
    await api.deleteSynthesis(id);
    toast("Synthèse supprimée.", "success");
    await loadHistory({ force: true });
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteVoice(id) {
  const confirmed = await confirmAction({
    title: "Supprimer ce clip ?",
    message: "Le clip de référence sera supprimé du disque. Cette action est définitive.",
    acceptLabel: "Supprimer",
  });
  if (!confirmed) return;
  try {
    await api.deleteVoice(id);
    if (Number(state.mimoClip?.id) === Number(id)) {
      state.mimoClip = null;
      window.dispatchEvent(new Event("mimo-clip:cleared"));
    }
    toast("Clip supprimé.", "success");
    await loadHistory({ force: true });
  } catch (error) {
    toast(error.message, "error");
  }
}

function bindSyntheses() {
  dom.syntheses?.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = (state.history?.syntheses || []).find(
      (entry) => Number(entry.id) === Number(button.closest(".history-card")?.dataset.id),
    );
    if (!item) return;
    if (button.dataset.action === "preview") {
      playPreview(button.dataset.url);
    } else if (button.dataset.action === "reuse") {
      applyStoredSynthesis(item);
      window.location.hash = "#/";
    } else if (button.dataset.action === "use-as-mimo") {
      await useSynthesisAsVoice(item);
    } else if (button.dataset.action === "delete") {
      await deleteSynthesis(item.id);
    }
  });
}

function bindVoices() {
  dom.voices?.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = (state.history?.voices || []).find(
      (entry) => Number(entry.id) === Number(button.closest(".history-card")?.dataset.id),
    );
    if (!item) return;
    if (button.dataset.action === "preview") {
      playPreview(button.dataset.url);
    } else if (button.dataset.action === "use") {
      state.mimoClip = item;
      applyStoredVoice(item);
      window.location.hash = "#/";
    } else if (button.dataset.action === "rename") {
      const name = await promptAction({
        title: "Renommer le clip",
        message: "Choisissez un nom court et parlant.",
        label: "Nom du clip",
        value: item.name || item.originalName || "",
        acceptLabel: "Renommer",
      });
      if (!name) return;
      try {
        await api.renameVoice(item.id, name);
        if (Number(state.mimoClip?.id) === Number(item.id)) {
          setMimoClip({ ...state.mimoClip, name });
        }
        toast("Clip renommé.", "success");
        await loadHistory({ force: true });
      } catch (error) {
        toast(error.message, "error");
      }
    } else if (button.dataset.action === "delete") {
      await deleteVoice(item.id);
    }
  });
}

export function initHistory() {
  bindSyntheses();
  bindVoices();

  dom.search?.addEventListener("input", debounce(render, 140));
  dom.refresh?.addEventListener("click", () => loadHistory({ force: true }));

  dom.clearSyntheses?.addEventListener("click", async () => {
    const confirmed = await confirmAction({
      title: "Supprimer toutes les synthèses ?",
      message: "Tous les audios générés seront supprimés du disque. Cette action est définitive.",
      acceptLabel: "Tout supprimer",
    });
    if (!confirmed) return;
    try {
      await api.clearSyntheses();
      toast("Historique des synthèses vidé.", "success");
      await loadHistory({ force: true });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  dom.clearVoices?.addEventListener("click", async () => {
    const confirmed = await confirmAction({
      title: "Supprimer tous les clips ?",
      message: "Tous les clips de référence seront supprimés du disque. Cette action est définitive.",
      acceptLabel: "Tout supprimer",
    });
    if (!confirmed) return;
    try {
      await api.clearVoices();
      if (state.mimoClip) {
        state.mimoClip = null;
        window.dispatchEvent(new Event("mimo-clip:cleared"));
      }
      toast("Bibliothèque de clips vidée.", "success");
      await loadHistory({ force: true });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  window.addEventListener("history:invalidate", () => {
    state.historyLoaded = false;
  });
}
