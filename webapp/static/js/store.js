const STORAGE_KEY = "fautexprimer.preferences.v1";
const STORAGE_VERSION = 1;

const PROVIDER_IDS = ["gemini", "edge", "mimo", "fish"];
const FORMAT_IDS = ["wav", "mp3", "ogg", "flac", "m4a"];

const DEFAULT_PREFERENCES = {
  provider: "edge",
  formats: ["wav"],
  speed: 1,
  gain: 1,
  voices: {},
  mimoModel: "",
  mimoUrl: "",
  fishModel: "",
  systemPrompt: "",
};

export const state = {
  config: null,
  settings: null,
  health: null,
  catalog: null,
  history: null,
  edgeVoices: [],
  provider: "edge",
  formats: new Set(["wav"]),
  speed: 1,
  gain: 1,
  preferences: { ...DEFAULT_PREFERENCES, voices: {} },
  lastResult: null,
  historyLoaded: false,
  catalogLoaded: false,
};

export function loadPreferences() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== "object") return;
    const provider = PROVIDER_IDS.includes(saved.provider)
      ? saved.provider
      : DEFAULT_PREFERENCES.provider;
    const formats = Array.isArray(saved.formats)
      ? saved.formats.filter((item) => FORMAT_IDS.includes(String(item).replace(".", "").toLowerCase()))
      : [];
    state.preferences = {
      ...DEFAULT_PREFERENCES,
      ...saved,
      provider,
      formats: formats.length ? formats : DEFAULT_PREFERENCES.formats,
      voices: saved.voices && typeof saved.voices === "object" ? { ...saved.voices } : {},
      /* bornes de sécurité : une valeur corrompue du localStorage ne casse pas l'UI */
      speed: Math.min(2, Math.max(0.5, Number(saved.speed) || 1)),
      gain: Math.min(2, Math.max(0, Number(saved.gain) || 1)),
    };
    state.provider = provider;
    state.formats = new Set(state.preferences.formats);
    state.speed = state.preferences.speed;
    state.gain = state.preferences.gain;
  } catch {
    state.preferences = { ...DEFAULT_PREFERENCES, voices: {} };
  }
}

/* Écritures throttlées : les curseurs vitesse/volume déclenchent
   savePreferences à chaque frame d'input. Un seul accès localStorage toutes
   les 400 ms, le dernier état gagne (trailing edge). */
let saveTimer = 0;

function persistNow() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: STORAGE_VERSION, ...state.preferences }),
    );
  } catch {
    /* le stockage local peut être désactivé : les préférences restent en mémoire */
  }
}

export function savePreferences(patch = {}) {
  state.preferences = {
    ...state.preferences,
    ...patch,
    provider: state.provider,
    formats: [...state.formats],
    speed: state.speed,
    gain: state.gain,
    voices: { ...state.preferences.voices },
  };
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(persistNow, 400);
}

/* Sauvegarde immédiate avant la fermeture : aucune préférence modifiée dans
   les 400 dernières millisecondes n'est perdue. */
window.addEventListener("pagehide", () => {
  window.clearTimeout(saveTimer);
  persistNow();
});

export function rememberVoice(provider, voice) {
  if (!provider || !voice) return;
  state.preferences.voices[provider] = voice;
  savePreferences();
}
