export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, { method = "GET", body, timeout = 30000, raw = false } = {}) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  const headers = { Accept: "application/json" };
  let payload = body;
  if (body !== undefined && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: payload,
      signal: controller.signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new ApiError("Le service local met trop de temps à répondre.", 0);
    }
    throw new ApiError("Le service local est injoignable. Vérifiez qu'il est démarré.", 0);
  } finally {
    window.clearTimeout(timer);
  }

  if (!response.ok) {
    let message = `Erreur ${response.status}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === "string") message = data.detail;
      else if (data?.detail) message = JSON.stringify(data.detail);
      else if (typeof data?.message === "string") message = data.message;
    } catch {
      try {
        const text = (await response.text()).trim();
        if (text) message = text.slice(0, 500);
      } catch {
        /* réponse illisible */
      }
    }
    throw new ApiError(message, response.status);
  }

  if (raw) return response;
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) return response.text();
  return response.json();
}

function filenameFromDisposition(header) {
  if (!header) return "";
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      return encoded[1].trim();
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1].trim() : "";
}

export const api = {
  health() {
    return request("/api/health", { timeout: 8000 });
  },

  config() {
    return request("/api/config");
  },

  catalog() {
    return request("/api/api-catalog");
  },

  edgeVoices(query = "") {
    const suffix = query ? `?q=${encodeURIComponent(query)}` : "";
    return request(`/api/voices/edge${suffix}`, { timeout: 30000 });
  },

  history() {
    return request("/api/history?limit=200");
  },

  settings() {
    return request("/api/settings");
  },

  saveSettings(payload) {
    return request("/api/settings", { method: "POST", body: payload });
  },

  geminiModels() {
    return request("/api/models/gemini", { timeout: 45000 });
  },

  testEndpoint(payload) {
    return request("/api/test-endpoint", { method: "POST", body: payload, timeout: 120000 });
  },

  uploadRef(file, { source = "uploaded", name = "" } = {}) {
    const form = new FormData();
    form.append("file", file, file.name || `clip.${(file.type || "").split("/")[1] || "webm"}`);
    form.append("source", source);
    form.append("name", name);
    return request("/api/upload-ref", { method: "POST", body: form, timeout: 90000 });
  },

  async synthesize(payload) {
    const response = await request("/api/synthesize", {
      method: "POST",
      body: payload,
      timeout: 300000,
      raw: true,
    });
    return {
      blob: await response.blob(),
      disposition: filenameFromDisposition(response.headers.get("content-disposition")),
      transcript: response.headers.get("x-transcript") || "",
      elapsed: Number(response.headers.get("x-elapsed") || 0),
      formats: (response.headers.get("x-formats") || "").split(",").filter(Boolean),
      primaryFormat: response.headers.get("x-primary-format") || "",
      historyId: response.headers.get("x-history-id") || "",
      contentType: response.headers.get("content-type") || "application/octet-stream",
    };
  },

  historyAudioUrl(id, download = false) {
    return `/api/history/syntheses/${encodeURIComponent(id)}/audio${download ? "?download=1" : ""}`;
  },

  historyFormatsUrl(id, formats) {
    const list = Array.isArray(formats) ? formats.join(",") : String(formats || "");
    return `/api/history/syntheses/${encodeURIComponent(id)}/formats?formats=${encodeURIComponent(list)}`;
  },

  voiceAudioUrl(id) {
    return `/api/history/voices/${encodeURIComponent(id)}/audio`;
  },

  deleteSynthesis(id) {
    return request(`/api/history/syntheses/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  clearSyntheses() {
    return request("/api/history/syntheses", { method: "DELETE" });
  },

  renameVoice(id, name) {
    return request(`/api/history/voices/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: { name },
    });
  },

  deleteVoice(id) {
    return request(`/api/history/voices/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  synthesisAsVoice(id, name = "") {
    return request(`/api/history/syntheses/${encodeURIComponent(id)}/as-voice`, {
      method: "POST",
      body: { name },
      timeout: 120000,
    });
  },

  clearVoices() {
    return request("/api/history/voices", { method: "DELETE" });
  },
};
