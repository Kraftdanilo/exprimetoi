const MAX_SECONDS = 30;
const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
];

let recorder = null;
let stream = null;
let chunks = [];
let ticker = 0;
let startedAt = 0;
let stopResolve = null;
let limitCallback = null;

function supportedMime() {
  if (typeof MediaRecorder === "undefined") return "";
  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
}

export function recordingSupported() {
  return Boolean(navigator.mediaDevices?.getUserMedia && supportedMime());
}

export function isRecording() {
  return recorder?.state === "recording";
}

export function recordingElapsed() {
  return startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0;
}

function cleanup() {
  window.clearInterval(ticker);
  ticker = 0;
  startedAt = 0;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  recorder = null;
  chunks = [];
  limitCallback = null;
}

export async function startRecording({ onTick, onLimit } = {}) {
  if (isRecording()) throw new Error("Un enregistrement est déjà en cours.");
  if (!recordingSupported()) {
    throw new Error("L'enregistrement micro n'est pas disponible dans ce navigateur.");
  }

  stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
  });
  const mimeType = supportedMime();
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  chunks = [];
  startedAt = Date.now();
  limitCallback = onLimit || null;

  recorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });

  recorder.addEventListener("stop", () => {
    const type = recorder?.mimeType || mimeType || "audio/webm";
    const blob = new Blob(chunks, { type });
    const resolve = stopResolve;
    stopResolve = null;
    cleanup();
    resolve?.(blob);
  });

  recorder.start(250);

  ticker = window.setInterval(() => {
    const elapsed = recordingElapsed();
    onTick?.(elapsed);
    if (elapsed >= MAX_SECONDS) {
      const callback = limitCallback;
      limitCallback = null;
      callback?.();
    }
  }, 250);

  return recorder.mimeType || mimeType;
}

export function stopRecording() {
  if (!recorder || recorder.state === "inactive") {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    stopResolve = resolve;
    recorder.stop();
  });
}

export async function cancelRecording() {
  if (!recorder || recorder.state === "inactive") {
    cleanup();
    return;
  }
  stopResolve = null;
  recorder.stop();
}
