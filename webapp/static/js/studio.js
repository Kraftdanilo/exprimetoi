import { api } from "./api.js";
import { state, savePreferences, rememberVoice } from "./store.js";
import { saveGeminiModel } from "./settings.js";
import {
  startRecording,
  stopRecording,
  isRecording,
  recordingSupported,
} from "./audio.js";
import {
  $,
  $$,
  debounce,
  escapeHtml,
  formatBytes,
  formatLabel,
  formatNumber,
  providerId,
  providerLabel,
  setBusy,
  showStatus,
  toast,
  truncate,
} from "./ui.js";

const FALLBACK_PROVIDERS = [
  { id: "gemini", name: "Gemini", hint: "Narration naturelle et expressive", textLimit: 8000 },
  { id: "edge", name: "Edge", hint: "Sans configuration, 322 voix", textLimit: 20000 },
  { id: "mimo", name: "MiMo", hint: "Clonage de voix", textLimit: 4000 },
  { id: "fish", name: "Fish", hint: "Clonage gratuit · 83 langues", textLimit: 4000 },
];

const FALLBACK_LIMITS = {
  gemini: { maxChars: 8000, maxWords: 1400, maxSentences: 200 },
  edge: { maxChars: 20000, maxWords: 3500, maxSentences: 600 },
  mimo: { maxChars: 4000, maxWords: 700, maxSentences: 120 },
  fish: { maxChars: 4000, maxWords: 700, maxSentences: 120 },
};

const FALLBACK_FORMATS = [
  { label: "WAV (16-bit PCM)", ext: ".wav" },
  { label: "MP3 (128 kbps)", ext: ".mp3" },
  { label: "OGG Vorbis", ext: ".ogg" },
  { label: "FLAC", ext: ".flac" },
  { label: "M4A / AAC", ext: ".m4a" },
];

const dom = {
  engineList: $("#engine-list"),
  activeEngine: $("#active-engine"),
  providerNote: $("#provider-note"),
  text: $("#text"),
  counter: $("#text-counter"),
  chars: $("#text-chars"),
  words: $("#text-words"),
  sentences: $("#text-sentences"),
  limit: $("#text-limit"),
  limitMessage: $("#limit-message"),
  geminiVoiceField: $("#gemini-voice-field"),
  geminiVoice: $("#gemini-voice"),
  geminiModelField: $("#gemini-model-field"),
  geminiModel: $("#gemini-model"),
  refreshModels: $("#refresh-models"),
  edgeSearchField: $("#edge-search-field"),
  edgeSearch: $("#edge-search"),
  edgeVoiceField: $("#edge-voice-field"),
  edgeVoice: $("#edge-voice"),
  edgeCount: $("#edge-count"),
  speed: $("#speed"),
  speedValue: $("#speed-value"),
  gain: $("#gain"),
  gainValue: $("#gain-value"),
  formats: $("#format-options"),
  formatSummary: $("#format-summary"),
  mimoPanel: $("#mimo-panel"),
  fishModelField: $("#fish-model-field"),
  mimoModel: $("#mimo-model"),
  mimoUrl: $("#mimo-url"),
  fishModel: $("#fish-model"),
  clipStatus: $("#clip-status"),
  refFile: $("#ref-file"),
  dropzone: $("#ref-dropzone"),
  recordButton: $("#record-button"),
  recordHint: $("#record-hint"),
  synthesize: $("#synthesize"),
  studioStatus: $("#studio-status"),
  resultPanel: $("#result-panel"),
  resultMeta: $("#result-meta"),
  audioPlayer: $("#audio-player"),
  download: $("#download"),
  transcript: $("#result-transcript"),
  transcriptText: $("#transcript-text"),
};

let synthesizeBusy = false;
let uploadBusy = false;
let recordBusy = false;
let previousAudioUrl = "";

function providers() {
  return state.config?.providers?.length ? state.config.providers : FALLBACK_PROVIDERS;
}

function currentProvider() {
  return providers().find((item) => item.id === state.provider) || providers()[0];
}

function currentLimits() {
  return state.config?.textLimits?.[state.provider] || FALLBACK_LIMITS[state.provider] || FALLBACK_LIMITS.edge;
}

function providerName(id) {
  return providers().find((item) => item.id === id)?.name || providerLabel(id);
}

function keyConfigured(id) {
  if (id === "edge") return state.health ? Boolean(state.health.edgeTTS) : true;
  if (id === "gemini") {
    return Boolean(state.health?.geminiKey || state.settings?.geminiKeyConfigured);
  }
  if (id === "fish") {
    return Boolean(state.health?.fishKey || state.settings?.fishKeyConfigured);
  }
  return Boolean(state.health?.mimoKey || state.settings?.mimoKeyConfigured);
}

function renderEngines() {
  if (!dom.engineList) return;
  dom.engineList.replaceChildren();
  for (const provider of providers()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "engine-card";
    button.dataset.engine = provider.id;
    button.setAttribute("aria-pressed", provider.id === state.provider ? "true" : "false");
    const ready = keyConfigured(provider.id);
    const dotClass = ready ? "is-ready" : "is-offline";
    button.innerHTML = `
      <span class="engine-mark" aria-hidden="true">${escapeHtml(provider.name.slice(0, 1))}</span>
      <span class="engine-card-body">
        <strong>${escapeHtml(provider.name)}</strong>
        <small>${escapeHtml(provider.hint || "")}</small>
      </span>
      <span class="status-dot ${dotClass}" title="${ready ? "Prêt" : "Configuration requise"}"></span>
    `;
    button.addEventListener("click", () => setProvider(provider.id));
    dom.engineList.append(button);
  }
}

function updateEngineCards() {
  for (const button of $$(".engine-card", dom.engineList || document)) {
    button.setAttribute("aria-pressed", button.dataset.engine === state.provider ? "true" : "false");
  }
}

export function setProvider(id, { persist = true } = {}) {
  const provider = providers().find((item) => item.id === id);
  if (!provider) return;
  state.provider = id;
  updateEngineCards();
  if (dom.activeEngine) dom.activeEngine.textContent = providerName(id);

  const isGemini = id === "gemini";
  const isEdge = id === "edge";
  const isMimo = id === "mimo";
  const isFish = id === "fish";
  if (dom.geminiVoiceField) dom.geminiVoiceField.hidden = !isGemini;
  if (dom.geminiModelField) dom.geminiModelField.hidden = !isGemini;
  if (dom.edgeSearchField) dom.edgeSearchField.hidden = !isEdge;
  if (dom.edgeVoiceField) dom.edgeVoiceField.hidden = !isEdge;
  if (dom.mimoPanel) dom.mimoPanel.hidden = !(isMimo || isFish);
  if (dom.fishModelField) dom.fishModelField.hidden = !isFish;
  if (dom.mimoModel?.closest(".field")) dom.mimoModel.closest(".field").hidden = !isMimo;
  if (dom.mimoUrl?.closest(".field")) dom.mimoUrl.closest(".field").hidden = !isMimo;

  const limits = currentLimits();
  if (dom.providerNote) {
    dom.providerNote.textContent = `${providerName(id)} · ${formatNumber(limits.maxChars)} caractères maximum${isMimo || isFish ? " · clip de référence requis" : ""}`;
  }
  updateMetrics();
  updateClipStatus();

  if (isGemini) {
    const chosen = state.preferences.voices.gemini || state.config?.defaultVoice || "Zephyr";
    ensureOption(dom.geminiVoice, chosen, chosen);
    if (dom.geminiVoice) dom.geminiVoice.value = chosen;
  }
  if (isEdge) {
    const chosen = state.preferences.voices.edge || state.config?.defaultEdgeVoice || "fr-FR-DeniseNeural";
    ensureOption(dom.edgeVoice, chosen, chosen);
    if (dom.edgeVoice) dom.edgeVoice.value = chosen;
  }
  if (persist) savePreferences();
}

function ensureOption(select, value, label) {
  if (!select || !value) return;
  if (![...select.options].some((option) => option.value === value)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label || value;
    select.append(option);
  }
}

function updateClipStatus() {
  if (!dom.clipStatus) return;
  const clip = state.mimoClip;
  dom.clipStatus.textContent = clip ? truncate(clip.name || "Clip prêt", 34) : "Aucun clip";
  dom.clipStatus.title = clip?.name || "";
  dom.clipStatus.classList.toggle("is-ready", Boolean(clip));
  dom.clipStatus.classList.toggle("is-missing", !clip);
}

export function setMimoClip(voice) {
  state.mimoClip = voice || null;
  updateClipStatus();
}

function populateGeminiVoices() {
  if (!dom.geminiVoice) return;
  dom.geminiVoice.replaceChildren();
  for (const voice of state.config?.geminiVoices || []) {
    const option = document.createElement("option");
    option.value = voice.name;
    option.textContent = `${voice.name} — ${voice.tone || "neutre"}, ${(voice.pitch || "").toLowerCase()}`.replace(/, $/, "");
    dom.geminiVoice.append(option);
  }
}

function populateModels(models, selected) {
  if (!dom.geminiModel) return;
  const current = selected || dom.geminiModel.value || state.settings?.geminiModel;
  const seen = new Set();
  dom.geminiModel.replaceChildren();
  for (const model of models || []) {
    const id = String(model.id || model);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const option = document.createElement("option");
    option.value = id;
    option.textContent = model.label || id;
    dom.geminiModel.append(option);
  }
  if (current && !seen.has(current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current;
    dom.geminiModel.append(option);
  }
  const fallback = current || state.config?.defaultGeminiModel || "";
  if (fallback) dom.geminiModel.value = fallback;
}

function populateFormats() {
  if (!dom.formats) return;
  dom.formats.replaceChildren();
  const formats = state.config?.formats?.length ? state.config.formats : FALLBACK_FORMATS;
  const selected = [...state.formats];
  state.formats = new Set(
    selected.filter((ext) => formats.some((format) => format.ext === ext)),
  );
  if (!state.formats.size) state.formats.add(".wav");

  for (const format of formats) {
    const label = document.createElement("label");
    label.className = "format-chip";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = format.ext;
    input.checked = state.formats.has(format.ext);
    input.addEventListener("change", () => {
      if (input.checked) state.formats.add(input.value);
      else state.formats.delete(input.value);
      updateFormatSummary();
      savePreferences();
      updateGenerateState();
    });
    const span = document.createElement("span");
    span.textContent = formatLabel(format.ext);
    label.title = format.label;
    label.append(input, span);
    dom.formats.append(label);
  }
  updateFormatSummary();
  applyFfmpegAvailability();
}

function updateFormatSummary() {
  if (!dom.formatSummary) return;
  const count = state.formats.size;
  const ffmpegNote = state.health && !state.health.ffmpeg
    ? " · FFmpeg absent : WAV uniquement"
    : "";
  if (!count) {
    dom.formatSummary.textContent = `Aucun format sélectionné${ffmpegNote}`;
  } else if (count === 1) {
    dom.formatSummary.textContent = `1 format sélectionné${ffmpegNote}`;
  } else {
    dom.formatSummary.textContent = `${count} formats sélectionnés${ffmpegNote}`;
  }
  updateGenerateState();
}

function applyFfmpegAvailability() {
  if (!dom.formats) return;
  const available = state.health ? Boolean(state.health.ffmpeg) : true;
  for (const input of $$("input[type=checkbox]", dom.formats)) {
    const isWav = input.value === ".wav";
    input.disabled = !available && !isWav;
    if (input.disabled && input.checked) {
      input.checked = false;
      state.formats.delete(input.value);
    }
  }
  if (!state.formats.size) {
    const wav = $(`input[value=".wav"]`, dom.formats);
    if (wav) {
      wav.checked = true;
      state.formats.add(".wav");
    }
  }
  updateFormatSummary();
}

function matchCount(value, pattern) {
  return (value.match(pattern) || []).length;
}

function textMetrics(value) {
  const text = value || "";
  let words = 0;
  try {
    words = matchCount(text, /[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu);
  } catch {
    words = text.trim() ? text.trim().split(/\s+/).length : 0;
  }
  let sentences = matchCount(text, /[.!?…]+/g);
  if (!sentences && text.trim()) sentences = 1;
  return { chars: text.length, words, sentences };
}

function updateMetrics() {
  const text = dom.text?.value || "";
  const limits = currentLimits();
  const metrics = textMetrics(text);
  if (dom.chars) dom.chars.textContent = formatNumber(metrics.chars);
  if (dom.words) dom.words.textContent = formatNumber(metrics.words);
  if (dom.sentences) dom.sentences.textContent = formatNumber(metrics.sentences);
  if (dom.limit) dom.limit.textContent = formatNumber(limits.maxChars);
  if (dom.counter) {
    dom.counter.textContent = `${formatNumber(metrics.chars)} / ${formatNumber(limits.maxChars)}`;
    dom.counter.classList.toggle("is-over", metrics.chars > limits.maxChars);
  }

  const overChars = metrics.chars > limits.maxChars;
  const overWords = metrics.words > limits.maxWords;
  const overSentences = metrics.sentences > limits.maxSentences;
  dom.text?.classList.toggle("is-over", overChars);

  const flags = [
    [dom.chars, overChars],
    [dom.words, overWords],
    [dom.sentences, overSentences],
  ];
  for (const [element, over] of flags) {
    element?.closest(".metric")?.classList.toggle("is-over", over);
  }

  const problems = [];
  if (overChars) {
    problems.push(`${formatNumber(metrics.chars)} / ${formatNumber(limits.maxChars)} caractères`);
  }
  if (overWords) {
    problems.push(`${formatNumber(metrics.words)} / ${formatNumber(limits.maxWords)} mots`);
  }
  if (overSentences) {
    problems.push(`${formatNumber(metrics.sentences)} / ${formatNumber(limits.maxSentences)} phrases`);
  }
  if (dom.limitMessage) {
    dom.limitMessage.hidden = !problems.length;
    dom.limitMessage.textContent = problems.length
      ? `Texte trop long pour ${providerName(state.provider)} : ${problems.join(" ; ")}. Découpez le texte en plusieurs parties.`
      : "";
  }
  updateGenerateState();
}

function updateGenerateState() {
  if (!dom.synthesize) return;
  const over = !dom.limitMessage?.hidden;
  dom.synthesize.disabled = synthesizeBusy || over || state.formats.size === 0;
}

async function loadEdgeVoices() {
  if (!dom.edgeVoice) return;
  dom.edgeVoice.replaceChildren(new Option("Chargement des voix…", ""));
  try {
    const result = await api.edgeVoices();
    state.edgeVoices = result.voices || [];
    renderEdgeVoices();
  } catch (error) {
    dom.edgeVoice.replaceChildren(new Option("Voix Edge indisponibles", ""));
    dom.edgeCount.textContent = "";
    toast(`Voix Edge indisponibles : ${error.message}`, "warning", 7000);
  }
}

function renderEdgeVoices() {
  if (!dom.edgeVoice) return;
  const query = (dom.edgeSearch?.value || "").trim().toLowerCase();
  const all = state.edgeVoices;
  const filtered = query
    ? all.filter((voice) =>
        `${voice.label} ${voice.locale} ${voice.localeName} ${voice.shortName} ${voice.gender} ${voice.categories} ${voice.personalities}`
          .toLowerCase()
          .includes(query),
      )
    : all;
  const previous = dom.edgeVoice.value;
  dom.edgeVoice.replaceChildren();
  for (const voice of filtered) {
    const option = document.createElement("option");
    option.value = voice.shortName;
    option.textContent = `${voice.label} · ${voice.localeName || voice.locale} (${voice.gender})`;
    dom.edgeVoice.append(option);
  }
  if (!filtered.length) {
    dom.edgeVoice.append(new Option("Aucune voix trouvée", ""));
  } else if (filtered.some((voice) => voice.shortName === previous)) {
    dom.edgeVoice.value = previous;
  } else {
    const preferred = state.preferences.voices.edge;
    const match = filtered.find((voice) => voice.shortName === preferred) || filtered[0];
    dom.edgeVoice.value = match.shortName;
    rememberVoice("edge", match.shortName);
  }
  if (dom.edgeCount) {
    dom.edgeCount.textContent = query
      ? `${formatNumber(filtered.length)} / ${formatNumber(all.length)}`
      : `${formatNumber(all.length)} voix`;
  }
}

async function refreshModels() {
  if (!dom.refreshModels) return;
  setBusy(dom.refreshModels, true);
  showStatus(dom.studioStatus, "Recherche des modèles Gemini…");
  try {
    const result = await api.geminiModels();
    if (state.settings) state.settings.geminiModels = result.models;
    if (state.config) state.config.geminiModels = result.models;
    populateModels(result.models);
    showStatus(dom.studioStatus, "");
    toast(
      result.source === "api" ? "Modèles Gemini actualisés." : "Liste locale utilisée.",
      result.source === "api" ? "success" : "warning",
    );
  } catch (error) {
    showStatus(dom.studioStatus, error.message, "error");
    toast(error.message, "error");
  } finally {
    setBusy(dom.refreshModels, false);
  }
}

function selectFormat(ext) {
  const target = String(ext || "").replace(".", "").toLowerCase();
  if (!target) return;
  state.formats = new Set([`.${target}`]);
  for (const input of $$("input[type=checkbox]", dom.formats)) {
    input.checked = input.value === `.${target}`;
  }
  updateFormatSummary();
  savePreferences();
}

function setText(value) {
  if (!dom.text) return;
  dom.text.value = value || "";
  updateMetrics();
}

function setSpeedGain(speed, gain) {
  if (speed != null) {
    state.speed = Math.min(2, Math.max(0.5, Number(speed) || 1));
    if (dom.speed) dom.speed.value = state.speed;
    if (dom.speedValue) dom.speedValue.textContent = `${state.speed.toFixed(2)}×`;
  }
  if (gain != null) {
    state.gain = Math.min(2, Math.max(0, Number(gain) || 1));
    if (dom.gain) dom.gain.value = state.gain;
    if (dom.gainValue) dom.gainValue.textContent = `${Math.round(state.gain * 100)} %`;
  }
  savePreferences();
}

export function applyStoredSynthesis(item) {
  setText(item.text);
  setProvider(providerId(item.provider), { persist: false });
  selectFormat(item.format);
  setSpeedGain(item.speed, item.gain);
  if (providerId(item.provider) === "gemini" && item.voice) {
    ensureOption(dom.geminiVoice, item.voice, item.voice);
    dom.geminiVoice.value = item.voice;
    rememberVoice("gemini", item.voice);
  }
  if (providerId(item.provider) === "edge" && item.voice) {
    ensureOption(dom.edgeVoice, item.voice, item.voice);
    dom.edgeVoice.value = item.voice;
    rememberVoice("edge", item.voice);
  }
  if (providerId(item.provider) === "mimo") {
    const voice = (state.history?.voices || []).find(
      (entry) => Number(entry.id) === Number(item.referenceVoiceId),
    );
    setMimoClip(voice || null);
  }
  showStatus(dom.studioStatus, "Synthèse rechargée dans le studio.", "success");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

export function applyStoredVoice(voice) {
  setProvider("mimo");
  setMimoClip(voice);
  showStatus(dom.studioStatus, "Clip de référence sélectionné pour MiMo.", "success");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function resetRecordUi() {
  dom.recordButton?.classList.remove("is-spinning");
  if (dom.recordButton) {
    dom.recordButton.disabled = false;
    dom.recordButton.textContent = "Démarrer l'enregistrement";
  }
  if (dom.recordHint) dom.recordHint.textContent = "Jusqu'à 30 secondes";
  dom.recordButton?.closest(".record-card")?.classList.remove("is-recording");
}

async function uploadReference(file, { source = "uploaded", name = "" } = {}) {
  if (!file || uploadBusy) return;
  if (!file.size) {
    toast("Le fichier audio est vide.", "error");
    return;
  }
  uploadBusy = true;
  dom.clipStatus.textContent = "Envoi…";
  try {
    const result = await api.uploadRef(file, { source, name });
    setMimoClip(result.voice);
    setProvider("mimo");
    toast("Clip de référence ajouté.", "success");
    window.dispatchEvent(new Event("history:invalidate"));
  } catch (error) {
    updateClipStatus();
    toast(error.message, "error", 7000);
  } finally {
    uploadBusy = false;
    if (dom.refFile) dom.refFile.value = "";
  }
}

async function finishRecording() {
  if (!isRecording() || recordBusy) return;
  recordBusy = true;
  const blob = await stopRecording();
  resetRecordUi();
  recordBusy = false;
  if (!blob || !blob.size) {
    toast("Aucun audio n'a été enregistré.", "warning");
    return;
  }
  const extension = blob.type.includes("mp4")
    ? "m4a"
    : blob.type.includes("ogg")
      ? "ogg"
      : "webm";
  const file = new File([blob], `enregistrement-micro.${extension}`, { type: blob.type });
  await uploadReference(file, { source: "recorded", name: "Enregistrement micro" });
}

async function toggleRecording() {
  if (recordBusy) return;
  if (isRecording()) {
    await finishRecording();
    return;
  }
  if (!recordingSupported()) {
    toast("L'enregistrement micro n'est pas disponible dans ce navigateur.", "warning");
    return;
  }
  try {
    await startRecording({
      onTick: (seconds) => {
        if (dom.recordHint) dom.recordHint.textContent = `Enregistrement… ${seconds} s / 30 s`;
        if (dom.recordButton) dom.recordButton.textContent = `Arrêter (${seconds} s)`;
      },
      onLimit: () => {
        toast("Durée maximale atteinte : 30 secondes.", "warning");
        finishRecording();
      },
    });
    dom.recordButton.classList.add("is-spinning");
    dom.recordButton.textContent = "Arrêter (0 s)";
    dom.recordButton.closest(".record-card")?.classList.add("is-recording");
    dom.recordHint.textContent = "Enregistrement… 0 s / 30 s";
  } catch (error) {
    resetRecordUi();
    const message = error?.name === "NotAllowedError"
      ? "Accès au micro refusé. Autorisez le micro dans le navigateur."
      : `Enregistrement impossible : ${error.message}`;
    toast(message, "error", 7000);
  }
}

function filenameFor(result) {
  if (result.disposition) return result.disposition;
  const primary = String(result.primaryFormat || "wav").replace(/^\./, "");
  const extension = result.formats.length > 1 ? "zip" : primary;
  const id = result.historyId ? `-${result.historyId}` : `-${Date.now()}`;
  return `fautexprimer${id}.${extension}`;
}

async function synthesize() {
  if (synthesizeBusy) return;
  const text = (dom.text?.value || "").trim();
  if (!text) {
    toast("Saisissez un texte à convertir.", "warning");
    dom.text?.focus();
    return;
  }
  if (dom.limitMessage && !dom.limitMessage.hidden) {
    toast("Le texte dépasse la limite du moteur sélectionné.", "error");
    return;
  }
  if (!state.formats.size) {
    toast("Sélectionnez au moins un format d'export.", "warning");
    return;
  }
  if (state.provider === "gemini" && !keyConfigured("gemini")) {
    toast("Ajoutez une clé Gemini dans la configuration.", "warning", 6000);
    return;
  }
  if (state.provider === "mimo" && !keyConfigured("mimo")) {
    toast("Ajoutez une clé MiMo dans la configuration.", "warning", 6000);
    return;
  }
  if (state.provider === "fish" && !keyConfigured("fish")) {
    toast("Ajoutez une clé Fish Audio (gratuite) dans la configuration.", "warning", 6000);
    return;
  }
  if ((state.provider === "mimo" || state.provider === "fish") && !state.mimoClip?.id) {
    toast("Importez ou enregistrez un clip de référence pour MiMo.", "warning", 6000);
    return;
  }

  const payload = {
    text,
    provider: state.provider,
    speed: state.speed,
    gain: state.gain,
    formats: [...state.formats],
  };
  if (state.provider === "gemini") {
    payload.voice = dom.geminiVoice?.value || state.config?.defaultVoice;
    payload.model = dom.geminiModel?.value || state.settings?.geminiModel;
  } else if (state.provider === "edge") {
    payload.voice = dom.edgeVoice?.value || state.config?.defaultEdgeVoice;
  } else {
    payload.mimoClip = null;
    payload.refVoiceId = state.mimoClip.id;
    if (state.provider === "mimo") {
      payload.mimoUrl = dom.mimoUrl?.value.trim() || undefined;
      payload.mimoModel = dom.mimoModel?.value.trim() || undefined;
    } else if (state.provider === "fish") {
      payload.fishModel = dom.fishModel?.value.trim() || undefined;
    }
  }

  synthesizeBusy = true;
  setBusy(dom.synthesize, true);
  showStatus(dom.studioStatus, `Génération avec ${providerName(state.provider)}…`);
  try {
    const result = await api.synthesize(payload);
    if (previousAudioUrl) URL.revokeObjectURL(previousAudioUrl);
    previousAudioUrl = URL.createObjectURL(result.blob);
    state.lastResult = result;

    if (dom.audioPlayer) {
      dom.audioPlayer.src = previousAudioUrl;
      dom.audioPlayer.load();
    }
    if (dom.download) {
      dom.download.href = previousAudioUrl;
      dom.download.download = filenameFor(result);
    }
    if (dom.resultMeta) {
      const spacing = result.elapsed ? ` · ${result.elapsed.toFixed(1)} s` : "";
      dom.resultMeta.textContent = `${providerName(state.provider)} · ${result.formats.map(formatLabel).join(", ")}${spacing} · ${formatBytes(result.blob.size)}`;
    }
    if (dom.transcriptText) dom.transcriptText.textContent = result.transcript || "";
    if (dom.transcript) dom.transcript.hidden = !result.transcript;
    if (dom.resultPanel) {
      dom.resultPanel.hidden = false;
      dom.resultPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    showStatus(dom.studioStatus, "Voix générée.", "success");
    toast("Voix générée.", "success");
    window.dispatchEvent(new Event("history:invalidate"));
  } catch (error) {
    showStatus(dom.studioStatus, error.message, "error");
    toast(error.message, "error", 8000);
  } finally {
    synthesizeBusy = false;
    setBusy(dom.synthesize, false);
    updateGenerateState();
  }
}

export function initStudio() {
  renderEngines();
  populateGeminiVoices();
  populateModels(modelCatalogForStudio(), state.settings?.geminiModel);
  populateFormats();

  if (dom.speed) dom.speed.value = state.speed;
  if (dom.speedValue) dom.speedValue.textContent = `${state.speed.toFixed(2)}×`;
  if (dom.gain) dom.gain.value = state.gain;
  if (dom.gainValue) dom.gainValue.textContent = `${Math.round(state.gain * 100)} %`;
  if (dom.mimoModel) dom.mimoModel.value = state.preferences.mimoModel || state.settings?.mimoModel || state.config?.mimo?.defaultMimoModel || "";
  if (dom.mimoUrl) dom.mimoUrl.value = state.preferences.mimoUrl || state.settings?.mimoUrl || state.config?.mimo?.defaultMimoUrl || "";
  if (dom.fishModel) dom.fishModel.value = state.settings?.fishModel || state.config?.mimo?.defaultFishModel || "s2.1-pro-free";
  const fishOptions = document.getElementById("fish-model-options");
  if (fishOptions) {
    fishOptions.replaceChildren();
    for (const model of state.config?.mimo?.fishModels || state.config?.mimo?.models || []) {
      const option = document.createElement("option");
      option.value = model;
      fishOptions.append(option);
    }
  }

  setProvider(state.provider, { persist: false });
  updateMetrics();
  loadEdgeVoices();

  dom.text?.addEventListener("input", updateMetrics);

  dom.speed?.addEventListener("input", () => {
    state.speed = Number(dom.speed.value);
    dom.speedValue.textContent = `${state.speed.toFixed(2)}×`;
    savePreferences();
  });
  dom.gain?.addEventListener("input", () => {
    state.gain = Number(dom.gain.value);
    dom.gainValue.textContent = `${Math.round(state.gain * 100)} %`;
    savePreferences();
  });

  dom.geminiVoice?.addEventListener("change", () => {
    rememberVoice("gemini", dom.geminiVoice.value);
  });
  dom.edgeVoice?.addEventListener("change", () => {
    rememberVoice("edge", dom.edgeVoice.value);
  });

  dom.geminiModel?.addEventListener("change", async () => {
    try {
      await saveGeminiModel(dom.geminiModel.value);
      toast("Modèle Gemini enregistré.", "success", 2200);
    } catch (error) {
      toast(error.message, "error");
    }
  });

  dom.refreshModels?.addEventListener("click", refreshModels);

  const filterEdge = debounce(() => renderEdgeVoices(), 140);
  dom.edgeSearch?.addEventListener("input", filterEdge);

  dom.refFile?.addEventListener("change", () => {
    const file = dom.refFile.files?.[0];
    if (file) uploadReference(file);
  });

  dom.dropzone?.addEventListener("dragover", (event) => {
    event.preventDefault();
    dom.dropzone.classList.add("is-dragover");
  });
  dom.dropzone?.addEventListener("dragleave", () => dom.dropzone.classList.remove("is-dragover"));
  dom.dropzone?.addEventListener("drop", (event) => {
    event.preventDefault();
    dom.dropzone.classList.remove("is-dragover");
    const file = event.dataTransfer?.files?.[0];
    if (file) uploadReference(file);
  });

  dom.recordButton?.addEventListener("click", toggleRecording);
  dom.synthesize?.addEventListener("click", synthesize);
  window.addEventListener("mimo-clip:cleared", () => setMimoClip(null));

  const saveMimoFields = debounce(() => {
    savePreferences({
      mimoModel: dom.mimoModel?.value || "",
      mimoUrl: dom.mimoUrl?.value || "",
    });
  }, 400);
  dom.mimoModel?.addEventListener("input", saveMimoFields);
  dom.mimoUrl?.addEventListener("input", saveMimoFields);

  window.addEventListener("health:updated", (event) => {
    state.health = event.detail;
    renderEngines();
    applyFfmpegAvailability();
    updateClipStatus();
  });

  window.addEventListener("settings:updated", (event) => {
    state.settings = event.detail;
    renderEngines();
    populateModels(event.detail.geminiModels || modelCatalogForStudio(), event.detail.geminiModel);
    if (dom.mimoModel && document.activeElement !== dom.mimoModel) {
      dom.mimoModel.value = event.detail.mimoModel || dom.mimoModel.value;
    }
    if (dom.mimoUrl && document.activeElement !== dom.mimoUrl) {
      dom.mimoUrl.value = event.detail.mimoUrl || dom.mimoUrl.value;
    }
  });

  window.addEventListener("gemini-models:updated", (event) => {
    populateModels(event.detail.models, event.detail.selected);
  });
}

function modelCatalogForStudio() {
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
