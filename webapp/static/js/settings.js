import { api } from "./api.js";
import { state } from "./store.js";
import {
  $,
  setBusy,
  showStatus,
  toast,
} from "./ui.js";

const dialog = $("#settings-dialog");
const form = $("#settings-form");
const status = $("#settings-status");
const keyState = {
  gemini: $("#gemini-key-state"),
  mimo: $("#mimo-key-state"),
};

const fields = {
  geminiKey: $("#set-gemini-key"),
  geminiModel: $("#set-gemini-model"),
  mimoKey: $("#set-mimo-key"),
  mimoModel: $("#set-mimo-model"),
  mimoUrl: $("#set-mimo-url"),
  ffmpeg: $("#set-ffmpeg-path"),
  prompt: $("#set-system-prompt"),
  storage: $("#settings-storage"),
  modelsHint: $("#models-hint"),
};

function fillSelect(select, models, selected) {
  if (!select) return;
  const current = selected || select.value;
  const seen = new Set();
  select.replaceChildren();
  for (const model of models || []) {
    const id = String(model.id || model);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const option = document.createElement("option");
    option.value = id;
    option.textContent = model.label || id;
    select.append(option);
  }
  if (current && !seen.has(current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current;
    select.append(option);
  }
  if (current) select.value = current;
}

export function modelCatalog() {
  const models = [
    ...(state.settings?.geminiModels || []),
    ...(state.config?.geminiModels || []),
  ];
  const seen = new Set();
  return models.filter((model) => {
    const id = String(model.id || "");
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function broadcastModels(models, selected) {
  fillSelect(fields.geminiModel, models, selected);
  window.dispatchEvent(
    new CustomEvent("gemini-models:updated", { detail: { models, selected } }),
  );
}

function fillDatalist(id, values) {
  const list = document.getElementById(id);
  if (!list) return;
  list.replaceChildren();
  for (const value of values || []) {
    const option = document.createElement("option");
    option.value = value;
    list.append(option);
  }
}

function setKeyBadge(element, configured) {
  if (!element) return;
  element.textContent = configured ? "Configurée" : "Non configurée";
  element.classList.toggle("is-ready", configured);
  element.classList.toggle("is-missing", !configured);
}

function updateKeyBadges(settings) {
  setKeyBadge(keyState.gemini, Boolean(settings?.geminiKeyConfigured));
  setKeyBadge(keyState.mimo, Boolean(settings?.mimoKeyConfigured));
}

export function updateSetupBanner(settings = state.settings) {
  const banner = $("#setup-banner");
  if (!banner) return;
  const configured = Boolean(settings?.geminiKeyConfigured || settings?.mimoKeyConfigured);
  banner.hidden = configured;
}

export function populateSettings(settings) {
  if (!settings) return;
  fields.geminiKey.value = "";
  fields.mimoKey.value = "";
  fields.geminiKey.placeholder = settings.geminiKeyConfigured
    ? "Clé enregistrée — laisser vide pour la conserver"
    : "Coller la clé Google AI Studio";
  fields.mimoKey.placeholder = settings.mimoKeyConfigured
    ? "Clé enregistrée — laisser vide pour la conserver"
    : "Coller la clé Xiaomi MiMo";
  fields.mimoUrl.value = settings.mimoUrl || "";
  fields.mimoModel.value = settings.mimoModel || "";
  fields.ffmpeg.value = settings.ffmpegPath || "";
  fields.ffmpeg.placeholder = "Détection automatique";
  fields.prompt.value = settings.systemPrompt || "";
  fields.storage.textContent = settings.storageFile
    ? `Clés et configuration enregistrées dans ${settings.storageFile}`
    : "Les clés sont enregistrées dans le dossier utilisateur local.";
  fillDatalist("mimo-url-options", settings.mimoUrls || state.config?.mimo?.urls);
  fillDatalist("mimo-model-options", settings.mimoModels || state.config?.mimo?.models);
  broadcastModels(settings.geminiModels || state.config?.geminiModels || [], settings.geminiModel);
  updateKeyBadges(settings);
  updateSetupBanner(settings);
  if (fields.modelsHint) {
    fields.modelsHint.textContent = settings.geminiKeyConfigured
      ? "Le modèle est appliqué automatiquement après changement."
      : "Ajoutez une clé Gemini pour découvrir les modèles disponibles.";
  }
}

export function hydrateSettings() {
  populateSettings(state.settings);
}

export async function openSettings() {
  showStatus(status, "");
  if (!state.settings) {
    try {
      state.settings = await api.settings();
    } catch (error) {
      toast(error.message, "error");
      return;
    }
  }
  populateSettings(state.settings);
  if (!dialog.open) dialog.showModal();
}

function closeSettings() {
  if (dialog.open) dialog.close();
}

export async function saveGeminiModel(model) {
  if (!model) return;
  await api.saveSettings({ geminiModel: model });
  state.settings = { ...(state.settings || {}), geminiModel: model };
  window.dispatchEvent(
    new CustomEvent("gemini-models:updated", {
      detail: { models: modelCatalog(), selected: model },
    }),
  );
  fillSelect(fields.geminiModel, modelCatalog(), model);
}

async function refreshModels() {
  const button = $("#settings-refresh-models");
  setBusy(button, true);
  showStatus(status, "Recherche des modèles Gemini…");
  try {
    const result = await api.geminiModels();
    if (state.settings) state.settings.geminiModels = result.models;
    broadcastModels(result.models, state.settings?.geminiModel);
    showStatus(
      status,
      result.source === "api"
        ? "Liste actualisée depuis Google."
        : result.message || "Liste locale utilisée.",
      result.source === "api" ? "success" : "",
    );
    toast(
      result.source === "api" ? "Modèles actualisés." : "Liste locale utilisée.",
      result.source === "api" ? "success" : "warning",
    );
  } catch (error) {
    showStatus(status, error.message, "error");
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function toggleKey(input, button) {
  if (!input || !button) return;
  const visible = input.type === "text";
  input.type = visible ? "password" : "text";
  button.textContent = visible ? "Afficher" : "Masquer";
}

async function testEndpoint() {
  const button = $("#test-endpoint");
  if (!state.mimoClip?.id) {
    showStatus(
      status,
      "Sélectionnez ou importez un clip de référence dans le studio avant de tester MiMo.",
      "error",
    );
    toast("Un clip de référence est nécessaire pour tester MiMo.", "warning");
    return;
  }
  setBusy(button, true);
  showStatus(status, "Test du service MiMo en cours…");
  try {
    const result = await api.testEndpoint({
      mimoKey: fields.mimoKey.value.trim(),
      mimoUrl: fields.mimoUrl.value.trim(),
      mimoModel: fields.mimoModel.value.trim(),
      refVoiceId: state.mimoClip.id,
    });
    showStatus(status, result.message, result.ok ? "success" : "error");
    toast(result.message, result.ok ? "success" : "error", 7000);
  } catch (error) {
    showStatus(status, error.message, "error");
    toast(error.message, "error", 7000);
  } finally {
    setBusy(button, false);
  }
}

async function submitSettings(event) {
  event.preventDefault();
  return saveSettings({
    geminiKey: fields.geminiKey.value.trim(),
    geminiModel: fields.geminiModel.value,
    mimoKey: fields.mimoKey.value.trim(),
    mimoUrl: fields.mimoUrl.value.trim(),
    mimoModel: fields.mimoModel.value.trim(),
    ffmpegPath: fields.ffmpeg.value.trim(),
    systemPrompt: fields.prompt.value.trim(),
  });
}

async function saveGeminiOnly() {
  return saveSettings({
    geminiKey: fields.geminiKey.value.trim(),
    geminiModel: fields.geminiModel.value,
  });
}

async function saveMimoOnly() {
  return saveSettings({
    mimoKey: fields.mimoKey.value.trim(),
    mimoUrl: fields.mimoUrl.value.trim(),
    mimoModel: fields.mimoModel.value.trim(),
  });
}

async function clearKey(engine) {
  const field = engine === "gemini" ? fields.geminiKey : fields.mimoKey;
  field.value = "";
  return saveSettings({ [engine === "gemini" ? "clearGeminiKey" : "clearMimoKey"]: true });
}

async function saveSettings(payload) {
  const button = $("#save-settings");
  setBusy(button, true);
  showStatus(status, "Enregistrement…");
  try {
    const result = await api.saveSettings(payload);
    state.settings = result.settings;
    populateSettings(result.settings);
    window.dispatchEvent(
      new CustomEvent("settings:updated", { detail: result.settings }),
    );
    window.dispatchEvent(new Event("health:refresh"));
    toast("Configuration enregistrée.", "success");
    return result;
  } catch (error) {
    showStatus(status, error.message, "error");
    toast(error.message, "error", 7000);
    throw error;
  } finally {
    setBusy(button, false);
  }
}

export function initSettings() {
  $("#open-settings")?.addEventListener("click", openSettings);
  $("#open-settings-mobile")?.addEventListener("click", openSettings);
  $("#setup-open-settings")?.addEventListener("click", openSettings);
  $("#close-settings")?.addEventListener("click", closeSettings);
  $("#cancel-settings")?.addEventListener("click", closeSettings);
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) closeSettings();
  });
  form?.addEventListener("submit", submitSettings);

  $("#toggle-gemini-key")?.addEventListener("click", () =>
    toggleKey(fields.geminiKey, $("#toggle-gemini-key")),
  );
  $("#toggle-mimo-key")?.addEventListener("click", () =>
    toggleKey(fields.mimoKey, $("#toggle-mimo-key")),
  );

  $("#settings-refresh-models")?.addEventListener("click", refreshModels);
  $("#test-endpoint")?.addEventListener("click", testEndpoint);

  const savePart = async (action, label) => {
    try {
      await action();
      showStatus(status, `${label} enregistrés.`, "success");
    } catch {
      /* message déjà affiché par saveSettings */
    }
  };
  $("#save-gemini")?.addEventListener("click", () => savePart(saveGeminiOnly, "Paramètres Gemini"));
  $("#save-mimo")?.addEventListener("click", () => savePart(saveMimoOnly, "Paramètres MiMo"));
  $("#save-gemini-inline")?.addEventListener("click", () => savePart(saveGeminiOnly, "Paramètres Gemini"));
  $("#save-mimo-inline")?.addEventListener("click", () => savePart(saveMimoOnly, "Paramètres MiMo"));
  $("#clear-gemini-key")?.addEventListener("click", () => savePart(() => clearKey("gemini"), "Clé Gemini"));
  $("#clear-mimo-key")?.addEventListener("click", () => savePart(() => clearKey("mimo"), "Clé MiMo"));

  fields.geminiModel?.addEventListener("change", async () => {
    try {
      await saveGeminiModel(fields.geminiModel.value);
      toast("Modèle Gemini enregistré.", "success", 2500);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  window.addEventListener("settings:updated", (event) => {
    updateKeyBadges(event.detail);
    updateSetupBanner(event.detail);
  });
}
