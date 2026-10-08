"""Backend Flask de Fautexprimer (moteurs Gemini, Edge et MiMo).

L'application expose le studio web, les fichiers statiques HTML/CSS/JavaScript et
une API JSON/audio consommée par le frontend natif, sans framework ni bundler.

Routes principales :
- ``GET  /api/health``        état du service et des moteurs ;
- ``GET  /api/config``        voix, vitesses et formats d'export ;
- ``GET  /api/voices/edge``   catalogue de voix Edge recherchable ;
- ``POST /api/synthesize``    génération et retour d'un audio ou d'un ZIP multi-format ;
- ``GET  /api/history/syntheses/<id>/formats`` export multi-format d'une piste historique ;
- ``POST /api/upload-ref``    dépôt d'un clip de référence ;
- ``POST /api/test-endpoint`` test du service MiMo ;
- ``POST /api/settings``      application de paramètres à l'exécution.

Lancement : ``venv\\Scripts\\python.exe desktop_app.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
from dotenv import load_dotenv
from history_store import HistoryStore
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.serving import make_server
from werkzeug.wsgi import ClosingIterator

_SOURCE_DIR = Path(__file__).resolve().parent
_FROZEN = bool(getattr(sys, "frozen", False))
_RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", _SOURCE_DIR)).resolve()
_EXECUTABLE_DIR = Path(sys.executable).resolve().parent if _FROZEN else _SOURCE_DIR


def runtime_dir() -> Path:
    """Dossier inscriptible des donnees utilisateur de l'application."""
    override = os.environ.get("FAUTEXPRIMER_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    else:
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = local_app_data / "Fautexprimer"
    path.mkdir(parents=True, exist_ok=True)
    return path


_DATA_DIR = runtime_dir()
_UPLOAD_DIR = _DATA_DIR / "uploads"

# PyInstaller inclut le moteur comme module caché. En développement, le fichier
# racine est déjà importable via le chemin du script courant.

# Ne jamais mettre un .env dans le bundle. En développement il est lu depuis la
# racine ; dans l'EXE depuis les données utilisateur ou le dossier de l'exécutable.
_ENV_FILES = [_DATA_DIR / ".env"]
if _EXECUTABLE_DIR != _DATA_DIR:
    _ENV_FILES.append(_EXECUTABLE_DIR / ".env")
# Le moteur est charge apres le .env pour que sa configuration optimisee soit
# disponible avant la creation des singletons.
for _env_file in _ENV_FILES:
    load_dotenv(_env_file, override=False)
os.environ.setdefault("GEMINI_TTS_HOME", str(_DATA_DIR / "runtime"))

import Google_Gemini_Live_TTS as gltts  # noqa: E402

APP_NAME = "Fautexprimer"
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_history_store = HistoryStore(_DATA_DIR)

app = Flask(
    __name__,
    template_folder=str(_RESOURCE_DIR / "webapp" / "templates"),
    static_folder=str(_RESOURCE_DIR / "webapp" / "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_BYTES + 1024


def create_http_server(host: str = "127.0.0.1", port: int = 0):
    """Crée le serveur WSGI utilisé par l'application de bureau PyWebView."""
    return make_server(host, port, app, threaded=True)


class APIError(Exception):
    """Erreur applicative dont le message est destiné au navigateur."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(APIError)
def _api_error(exc: APIError):
    return jsonify(detail=exc.message), exc.status


@app.errorhandler(RequestEntityTooLarge)
def _upload_too_large(_exc: RequestEntityTooLarge):
    return jsonify(detail=f"Clip trop volumineux (max {_MAX_UPLOAD_BYTES // (1024 * 1024)} Mo)."), 413


@app.errorhandler(HTTPException)
def _http_error(exc: HTTPException):
    return jsonify(detail=exc.description), exc.code or 500


# ---------------------------------------------------------------------------
# Moteurs partagés et catalogue Edge
# ---------------------------------------------------------------------------
_gemini_engine = gltts.get_engine()
_gemini_engine.api_key = _gemini_engine.api_key or os.environ.get("GEMINI_API_KEY", "")

_edge_engine = gltts.get_edge_engine()

_mimo_engine = gltts.get_mimo_engine()
_mimo_engine.mimo_key = os.environ.get("MIMO_API_KEY", "")

# Le navigateur lit le résultat ; le serveur ne doit jamais ouvrir un périphérique
# audio local pendant une requête web.
_gemini_engine.set_playback_enabled(False)
_edge_engine.set_playback_enabled(False)
_mimo_engine.set_playback_enabled(False)

# Un rafraîchissement réseau peut ensuite remplacer les noms abrégés par les
# libellés complets du service.
_edge_voices: list = gltts.builtin_edge_voices()
_edge_voices_lock = threading.Lock()


def _load_edge_voices_background() -> None:
    try:
        voices = gltts.load_edge_voices()
    except Exception as exc:  # le catalogue local reste utilisable
        gltts.log.warning("Catalogue Edge indisponible : %s", exc)
        voices = gltts.builtin_edge_voices()
    if not voices:
        return
    with _edge_voices_lock:
        _edge_voices.clear()
        _edge_voices.extend(voices)


threading.Thread(target=_load_edge_voices_background, daemon=True).start()


def _edge_voice_view(voice) -> dict:
    return {
        "shortName": voice.short_name,
        "label": voice.label,
        "locale": voice.locale,
        "localeName": voice.locale_name,
        "gender": voice.gender,
        "categories": voice.categories_text,
        "personalities": voice.personalities_text,
    }


PROVIDERS = {
    "gemini": gltts.PROVIDER_GEMINI,
    "edge": gltts.PROVIDER_EDGE,
    "mimo": gltts.PROVIDER_MIMO,
}

# Plafonds de securite de l'application, et non limites universelles des API.
# Les tests de la version courante ont valide Eben jusqu'a 8 000 caracteres,
# Dane jusqu'a 20 000, et Michel jusqu'a 4 000. Les valeurs en mots et phrases
# servent de reperes d'interface ; le serveur bloque sur la longueur exacte.
TEXT_LIMITS = {
    "gemini": {
        "name": "Eben",
        "maxChars": 8_000,
        "maxWords": 1_400,
        "maxSentences": 200,
        "testedChars": 8_000,
    },
    "edge": {
        "name": "Dane",
        "maxChars": 20_000,
        "maxWords": 3_500,
        "maxSentences": 600,
        "testedChars": 20_000,
    },
    "mimo": {
        "name": "Michel",
        "maxChars": 4_000,
        "maxWords": 700,
        "maxSentences": 120,
        "testedChars": 4_000,
    },
}


def _text_metrics(value: str) -> dict[str, int]:
    """Compte les caracteres, mots et phrases sans modifier le texte source."""
    text = value or ""
    words = re.findall(r"[\wÀ-ÖØ-öø-ÿ]+(?:['’-][\wÀ-ÖØ-öø-ÿ]+)*", text, re.UNICODE)
    sentence_count = len(re.findall(r"[.!?…]+", text))
    if not sentence_count and text.strip():
        sentence_count = 1
    return {
        "chars": len(text),
        "words": len(words),
        "sentences": sentence_count,
    }


def _validate_text_limit(provider_id: str, text: str) -> None:
    limits = TEXT_LIMITS.get(provider_id)
    if not limits:
        return
    metrics = _text_metrics(text)
    exceeded = []
    if metrics["chars"] > limits["maxChars"]:
        exceeded.append(f"{metrics['chars']} / {limits['maxChars']} caracteres")
    if metrics["words"] > limits["maxWords"]:
        exceeded.append(f"{metrics['words']} / {limits['maxWords']} mots")
    if metrics["sentences"] > limits["maxSentences"]:
        exceeded.append(f"{metrics['sentences']} / {limits['maxSentences']} phrases")
    if exceeded:
        raise APIError(
            f"Texte trop long pour {limits['name']} ({'; '.join(exceeded)}). "
            "Ce plafond est le plus fiable pour une synthese complete ; "
            "decoupez le texte en plusieurs parties."
        )


def _json_body() -> dict[str, Any]:
    if not request.is_json:
        raise APIError("Le corps de la requete doit etre au format JSON.")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise APIError("Objet JSON invalide.")
    return data


def _text(data: dict[str, Any], key: str, default: str = "", max_length: int = 100_000) -> str:
    value = data.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise APIError(f"{key} doit etre une chaine.")
    if len(value) > max_length:
        raise APIError(f"{key} ne peut pas depasser {max_length} caracteres.")
    return value


def _number(
    data: dict[str, Any], key: str, default: float, minimum: float, maximum: float
) -> float:
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        raise APIError(f"{key} doit etre un nombre.") from None
    if not minimum <= value <= maximum:
        raise APIError(f"{key} doit etre compris entre {minimum} et {maximum}.")
    return value


def _reference_path(value: str) -> str:
    """Valide la compatibilité avec un ancien chemin de clip local."""
    upload_root = _UPLOAD_DIR.resolve()
    voice_root = _history_store.voice_dir.resolve()
    try:
        path = Path(value).resolve()
        if not path.is_relative_to(upload_root) and not path.is_relative_to(voice_root):
            raise ValueError
    except (OSError, ValueError):
        raise APIError("Chemin de clip de reference invalide.") from None
    if not path.is_file():
        raise APIError("Le clip audio de reference est introuvable.")
    return str(path)


# ---------------------------------------------------------------------------
# Pages et routes de consultation
# ---------------------------------------------------------------------------
def shutdown_engines() -> None:
    """Arrête les moteurs partagés sans interrompre la fermeture de l'interface."""
    for engine in (_gemini_engine, _edge_engine, _mimo_engine):
        try:
            engine.stop()
        except Exception as exc:  # la fermeture reste best-effort
            gltts.log.debug("Arret du moteur incomplet: %s", exc)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    with _edge_voices_lock:
        voice_count = len(_edge_voices)
    return jsonify(
        status="ok",
        app=APP_NAME,
        version=gltts.APP_VERSION,
        geminiKey=bool(_gemini_engine.api_key),
        edgeTTS=gltts.edge_tts is not None,
        edgeVoices=voice_count,
        ffmpeg=gltts.ffmpeg_available(),
    )


@app.get("/api/config")
def config():
    return jsonify(
        providers=[
            {
                "id": "gemini",
                "name": "Eben",
                "hint": "Narration naturelle et expressive • 30 voix",
                "textLimit": TEXT_LIMITS["gemini"],
            },
            {
                "id": "edge",
                "name": "Dane",
                "hint": "Sans configuration • 322 voix multilingues",
                "textLimit": TEXT_LIMITS["edge"],
            },
            {
                "id": "mimo",
                "name": "Michel",
                "hint": "Clonage de voix a partir d'un clip",
                "textLimit": TEXT_LIMITS["mimo"],
            },
        ],
        textLimits=TEXT_LIMITS,
        geminiVoices=[
            {"name": v[0], "pitch": v[1], "tone": v[2], "bestFor": v[3]}
            for v in gltts.VOICES
        ],
        defaultVoice=gltts.DEFAULT_VOICE,
        defaultEdgeVoice=gltts.DEFAULT_EDGE_VOICE,
        speeds=[{"label": label, "value": value} for label, value in gltts.SPEED_PRESETS],
        formats=[{"label": label, "ext": ext} for label, ext in gltts.EXPORT_FORMATS],
        mimo={
            "defaultMimoUrl": gltts.MIMO_DEFAULT_URL,
            "defaultMimoModel": gltts.MIMO_DEFAULT_MODEL,
        },
    )


@app.get("/api/voices/edge")
def edge_voices():
    if not _edge_voices:
        _load_edge_voices_background()
    needle = request.args.get("q", "").strip().lower()
    with _edge_voices_lock:
        voices = list(_edge_voices)
    if needle:
        voices = [
            voice for voice in voices
            if needle in (
                f"{voice.label} {voice.locale} {voice.locale_name} {voice.short_name} "
                f"{voice.gender} {voice.categories_text} {voice.personalities_text}"
            ).lower()
        ]
    return jsonify(count=len(voices), voices=[_edge_voice_view(voice) for voice in voices])


@app.post("/api/test-endpoint")
def test_endpoint():
    data = _json_body()
    _mimo_engine.mimo_url = _text(data, "mimoUrl", gltts.MIMO_DEFAULT_URL, 2_000).strip() or gltts.MIMO_DEFAULT_URL
    _mimo_engine.mimo_model = _text(data, "mimoModel", gltts.MIMO_DEFAULT_MODEL, 300).strip() or gltts.MIMO_DEFAULT_MODEL
    _mimo_engine.mimo_key = _text(data, "mimoKey", "", 2_000).strip() or os.environ.get("MIMO_API_KEY", "")
    _mimo_engine.ref_audio = _reference_audio_from_payload(data)
    ok, message = _mimo_engine.check_endpoint()
    return jsonify(ok=ok, message=message)


@app.post("/api/settings")
def save_settings():
    data = _json_body()
    gemini_key = _text(data, "geminiKey", "", 2_000).strip()
    mimo_key = _text(data, "mimoKey", "", 2_000).strip()
    ffmpeg_path = _text(data, "ffmpegPath", "", 4_000).strip()
    system_prompt = _text(data, "systemPrompt", "", 20_000).strip()

    if gemini_key:
        _gemini_engine.api_key = gemini_key
        os.environ["GEMINI_API_KEY"] = gemini_key
    if mimo_key:
        _mimo_engine.mimo_key = mimo_key
        os.environ["MIMO_API_KEY"] = mimo_key
    if ffmpeg_path:
        for engine in (_gemini_engine, _edge_engine, _mimo_engine):
            engine.ffmpeg_path = ffmpeg_path
    if system_prompt:
        gltts.DEFAULT_PROMPT = system_prompt
    return jsonify(ok=True)


def _reference_audio_from_payload(data: dict[str, Any]) -> str:
    """Résout une voix enregistrée par identifiant opaque ou ancien chemin limité."""
    voice_id = data.get("refVoiceId")
    if voice_id not in (None, ""):
        try:
            record_id = int(voice_id)
        except (TypeError, ValueError):
            raise APIError("Identifiant de voix invalide.") from None
        path = _history_store.voice_audio(record_id)
        if path is None:
            raise APIError("La voix selectionnee est introuvable.")
        return str(path)

    ref_audio = _text(data, "refAudio", "", 4_000).strip()
    if not ref_audio:
        raise APIError("Un clip audio de reference est requis pour ce moteur.")
    return _reference_path(ref_audio)


def _voice_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(record["id"]),
        "createdAt": record["created_at"],
        "name": record["name"],
        "originalName": record["original_name"],
        "source": record["source"],
        "format": record["format"],
        "audioUrl": f"/api/history/voices/{int(record['id'])}/audio",
    }


def _synthesis_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(record["id"]),
        "createdAt": record["created_at"],
        "provider": record["provider"],
        "voice": record["voice"],
        "text": record["text"],
        "speed": float(record["speed"]),
        "gain": float(record["gain"]),
        "format": record["format"],
        "transcript": record["transcript"],
        "referenceVoiceId": record["reference_voice_id"],
        "audioUrl": f"/api/history/syntheses/{int(record['id'])}/audio",
    }


@app.get("/api/history")
def history_index():
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(
        stats=_history_store.stats(),
        syntheses=[_synthesis_view(item) for item in _history_store.list_syntheses(limit, offset)],
        voices=[_voice_view(item) for item in _history_store.list_voices(limit, offset)],
    )


def _send_stored_audio(path: Path, download_name: str, as_attachment: bool = False):
    return send_file(
        path,
        mimetype=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        as_attachment=as_attachment,
        download_name=download_name,
        conditional=True,
    )


def _convert_audio_path(source: Path, target: Path, target_ext: str, ffmpeg_path: str = "") -> None:
    """Convertit un fichier audio existant vers une extension cible."""
    codec_by_ext = {
        ".wav": "pcm_s16le",
        ".mp3": "libmp3lame",
        ".ogg": "libvorbis",
        ".flac": "flac",
        ".m4a": "aac",
    }
    ffmpeg = gltts._resolve_ffmpeg(ffmpeg_path)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-ac", str(gltts.CHANNELS),
        "-ar", str(gltts.RECEIVE_SAMPLE_RATE),
        "-codec:a", codec_by_ext[target_ext],
    ]
    if target_ext in {".mp3", ".m4a"}:
        command += ["-b:a", "192k"]
    elif target_ext == ".ogg":
        command += ["-q:a", "3"]
    command.append(str(target))
    converted = subprocess.run(command, capture_output=True, text=True)
    if converted.returncode != 0:
        raise RuntimeError(converted.stderr.strip() or f"Conversion vers {target_ext} impossible.")


def _export_formats_from_file(
    source: Path,
    record_id: int,
    requested_formats: list[str],
):
    """Cree un ZIP de conversions a partir d'une piste deja archivee."""
    temp_dir = Path(tempfile.mkdtemp(prefix="fautexprimer_formats_"))
    archive = temp_dir / f"fautexprimer-{record_id}-formats.zip"
    codec_by_ext = {
        ".wav": "pcm_s16le",
        ".mp3": "libmp3lame",
        ".ogg": "libvorbis",
        ".flac": "flac",
        ".m4a": "aac",
    }

    def cleanup_archive() -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)

    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for target_ext in requested_formats:
                target = temp_dir / f"fautexprimer-{record_id}{target_ext}"
                if target_ext == source.suffix.lower():
                    shutil.copy2(source, target)
                else:
                    ffmpeg = gltts._resolve_ffmpeg("")
                    command = [
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(source), "-ac", str(gltts.CHANNELS),
                        "-ar", str(gltts.RECEIVE_SAMPLE_RATE),
                        "-codec:a", codec_by_ext[target_ext],
                    ]
                    if target_ext in {".mp3", ".m4a"}:
                        command += ["-b:a", "192k"]
                    elif target_ext == ".ogg":
                        command += ["-q:a", "3"]
                    command.append(str(target))
                    converted = subprocess.run(command, capture_output=True, text=True)
                    if converted.returncode != 0:
                        raise RuntimeError(
                            converted.stderr.strip() or f"Conversion vers {target_ext} impossible."
                        )
                bundle.write(target, arcname=target.name)
        response = send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name=archive.name,
            conditional=False,
        )
        response.response = ClosingIterator(response.response, [cleanup_archive])
        response.call_on_close(cleanup_archive)
        return response
    except Exception:
        cleanup_archive()
        raise


@app.get("/api/history/syntheses/<int:record_id>/audio")
def history_synthesis_audio(record_id: int):
    path = _history_store.synthesis_audio(record_id)
    if path is None:
        raise APIError("Audio historique introuvable.", 404)
    return _send_stored_audio(
        path, f"fautexprimer-{record_id}{path.suffix.lower()}", request.args.get("download") == "1"
    )


@app.get("/api/history/syntheses/<int:record_id>/formats")
def history_synthesis_formats(record_id: int):
    record = _history_store.get_synthesis(record_id)
    path = _history_store.synthesis_audio(record_id)
    if record is None or path is None:
        raise APIError("Audio historique introuvable.", 404)
    raw_formats = request.args.get("formats", "").strip()
    values = [item.strip() for item in raw_formats.split(",") if item.strip()]
    if not values:
        values = [record["format"]]
    try:
        requested_formats = _requested_formats({"formats": values})
        return _export_formats_from_file(path, record_id, requested_formats)
    except (RuntimeError, gltts.AudioError) as exc:
        raise APIError(f"Conversion multi-format impossible : {exc}", 502) from None


@app.delete("/api/history/syntheses/<int:record_id>")
def delete_history_synthesis(record_id: int):
    if not _history_store.delete_synthesis(record_id):
        raise APIError("Synthèse introuvable.", 404)
    return jsonify(ok=True)


@app.delete("/api/history/syntheses")
def clear_history_syntheses():
    return jsonify(ok=True, deleted=_history_store.clear_syntheses())


@app.get("/api/history/voices/<int:record_id>/audio")
def history_voice_audio(record_id: int):
    path = _history_store.voice_audio(record_id)
    if path is None:
        raise APIError("Voix historique introuvable.", 404)
    return _send_stored_audio(
        path, f"voix-{record_id}{path.suffix.lower()}", request.args.get("download") == "1"
    )


@app.patch("/api/history/voices/<int:record_id>")
def rename_history_voice(record_id: int):
    name = _text(_json_body(), "name", "", 120).strip()
    try:
        record = _history_store.rename_voice(record_id, name)
    except ValueError as exc:
        raise APIError(str(exc)) from None
    if record is None:
        raise APIError("Voix historique introuvable.", 404)
    return jsonify(ok=True, voice=_voice_view(record))


@app.delete("/api/history/voices/<int:record_id>")
def delete_history_voice(record_id: int):
    if not _history_store.delete_voice(record_id):
        raise APIError("Voix historique introuvable.", 404)
    return jsonify(ok=True)


@app.delete("/api/history/voices")
def clear_history_voices():
    return jsonify(ok=True, deleted=_history_store.clear_voices())


# ---------------------------------------------------------------------------
# Dépôt du clip de référence
# ---------------------------------------------------------------------------
_AUDIO_SIGNATURES = (
    (b"RIFF", ".wav"),
    (b"ID3", ".mp3"),
    (b"fLaC", ".flac"),
    (b"OggS", ".ogg"),
    (b"\x1a\x45\xdf\xa3", ".webm"),
)


def _sniff_audio(data: bytes) -> str:
    if not data:
        return ""
    for magic, ext in _AUDIO_SIGNATURES:
        if data.startswith(magic):
            return ext
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return ".mp3"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ".m4a"
    return ""


@app.post("/api/upload-ref")
def upload_ref():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise APIError("Aucun fichier audio n'a ete fourni.")
    raw = uploaded.stream.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise APIError(f"Clip trop volumineux (max {_MAX_UPLOAD_BYTES // (1024 * 1024)} Mo).", 413)
    detected = _sniff_audio(raw)
    if not detected:
        raise APIError(
            "Format audio non reconnu. Utilisez un fichier WAV, MP3, FLAC, "
            "OGG, M4A ou WebM (un clip de quelques secondes de parole suffit)."
        )
    source = request.form.get("source", "uploaded")
    name = request.form.get("name", "").strip()
    try:
        record = _history_store.add_voice_bytes(
            raw,
            name=name,
            original_name=uploaded.filename,
            source=source,
            audio_format=detected.lstrip("."),
        )
    except ValueError as exc:
        raise APIError(str(exc)) from None
    return jsonify(ok=True, voice=_voice_view(record), bytes=len(raw))


# ---------------------------------------------------------------------------
# Synthèse : le fichier généré est renvoyé puis supprimé après la réponse.
# ---------------------------------------------------------------------------
MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".zip": "application/zip",
}

_FORMAT_EXTENSIONS = {
    ext.lstrip(".").lower(): ext for _, ext in gltts.EXPORT_FORMATS
}


def _requested_formats(data: dict[str, Any]) -> list[str]:
    """Valide et normalise une ou plusieurs extensions d'export."""
    raw_formats = data.get("formats")
    if raw_formats is None:
        raw_formats = [data.get("format", "wav")]
    if not isinstance(raw_formats, list) or not raw_formats:
        raise APIError("formats doit contenir au moins un format audio.")

    requested: list[str] = []
    for value in raw_formats:
        if not isinstance(value, str):
            raise APIError("Chaque format audio doit etre une chaine.")
        token = value.strip().lower()
        ext = _FORMAT_EXTENSIONS.get(token.lstrip("."))
        if ext is None:
            label_ext = gltts.FORMAT_BY_LABEL.get(value.strip())
            ext = label_ext
        if ext is None:
            allowed = ", ".join(_FORMAT_EXTENSIONS)
            raise APIError(f"Format audio inconnu : {value}. Formats autorises : {allowed}.")
        if ext not in requested:
            requested.append(ext)

    if len(requested) > len(_FORMAT_EXTENSIONS):
        raise APIError("Trop de formats demandes.")
    if any(ext != ".wav" for ext in requested) and not gltts.ffmpeg_available():
        raise APIError(
            "FFmpeg est necessaire pour produire MP3, OGG, FLAC ou M4A.",
            503,
        )
    return requested


@app.post("/api/synthesize")
def synthesize():
    data = _json_body()
    text = _text(data, "text", max_length=100_000).strip()
    if not text:
        raise APIError("Le texte est vide.")

    provider_id = _text(data, "provider", "gemini", 30).lower()
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise APIError(f"Moteur inconnu : {provider_id}")
    _validate_text_limit(provider_id, text)

    speed = _number(data, "speed", 1.0, 0.25, 4.0)
    gain = _number(data, "gain", 1.0, 0.0, 4.0)
    voice = _text(data, "voice", "", 300).strip()
    requested_formats = _requested_formats(data)
    ext = requested_formats[0]

    if provider == gltts.PROVIDER_GEMINI:
        engine = _gemini_engine
        voice = voice or gltts.DEFAULT_VOICE
        engine.clear_cancel()
        engine.start()  # idempotent
    elif provider == gltts.PROVIDER_EDGE:
        engine = _edge_engine
        voice = voice or gltts.DEFAULT_EDGE_VOICE
        engine.clear_cancel()
    else:
        engine = _mimo_engine
        engine.clear_cancel()
        engine.ref_audio = _reference_audio_from_payload(data)
        engine.ref_text = _text(data, "refText", "", 20_000)
        engine.mimo_url = _text(data, "mimoUrl", gltts.MIMO_DEFAULT_URL, 2_000).strip() or gltts.MIMO_DEFAULT_URL
        engine.mimo_model = _text(data, "mimoModel", gltts.MIMO_DEFAULT_MODEL, 300).strip() or gltts.MIMO_DEFAULT_MODEL
        engine.mimo_key = _text(data, "mimoKey", "", 2_000).strip() or os.environ.get("MIMO_API_KEY", "")

    output_paths: list[Path] = []
    handle, out_path = tempfile.mkstemp(suffix=ext, prefix="gltts_api_")
    os.close(handle)
    out = Path(out_path)
    output_paths.append(out)
    out.unlink(missing_ok=True)  # certains moteurs créent eux-mêmes le fichier

    def cleanup_output() -> None:
        # Windows peut garder le handle pendant la fermeture du flux ou quelques
        # instants après l'arrêt du worker.
        for path in reversed(output_paths):
            for _attempt in range(5):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.05)

    def make_output_path(suffix: str) -> Path:
        handle, path = tempfile.mkstemp(suffix=suffix, prefix="gltts_api_")
        os.close(handle)
        result = Path(path)
        output_paths.append(result)
        result.unlink(missing_ok=True)
        return result

    result: dict[str, Any] = {}
    done = threading.Event()
    timed_out = threading.Event()

    def worker() -> None:
        started = time.monotonic()
        try:
            job = engine.speak(
                text,
                voice,
                collect_audio=True,
                save_path=out_path,
                speed=speed,
                gain=gain,
            )
            if job.audio and not (out.exists() and out.stat().st_size > 0):
                gltts.export_audio(bytes(job.audio), out_path, ffmpeg_path=engine.ffmpeg_path)
            result["job"] = job
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            result["elapsed"] = round(time.monotonic() - started, 2)
            done.set()
            if timed_out.is_set():
                cleanup_output()

    threading.Thread(target=worker, daemon=True, name="fautexprimer-synthesis").start()
    if not done.wait(timeout=gltts.TURN_TIMEOUT_MAX + 60):
        timed_out.set()
        # Couvre le cas où le worker se termine entre la fin du wait et le flag.
        done.wait(timeout=1)
        cleanup_output()
        raise APIError("Delai depasse pendant la synthese.", 502)
    if "error" in result:
        cleanup_output()
        raise APIError(f"Echec de la synthese : {result['error']}", 502)

    job = result["job"]
    if job.error:
        cleanup_output()
        raise APIError(f"Synthese interrompue : {job.error}", 502)
    if not out.exists() or out.stat().st_size == 0:
        cleanup_output()
        raise APIError("Le moteur n'a produit aucun audio.", 502)

    history_id = None
    try:
        history_record = _history_store.add_synthesis(
            out,
            provider=provider_id,
            voice=voice or provider,
            text=text,
            speed=speed,
            gain=gain,
            audio_format=ext.lstrip("."),
            transcript=job.transcript,
            reference_voice_id=(int(data["refVoiceId"]) if data.get("refVoiceId") not in (None, "") else None),
        )
        history_id = int(history_record["id"])
    except Exception as exc:
        gltts.log.warning("Audio genere mais historique indisponible: %s", exc)

    response_path = out
    response_mimetype = MEDIA_TYPES.get(ext, "application/octet-stream")
    response_download_name = f"tts_{int(time.time())}{ext}"
    response_attachment = False
    if len(requested_formats) > 1:
        archive_path = make_output_path(".zip")
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for target_ext in requested_formats:
                    target = out if target_ext == ext else make_output_path(target_ext)
                    if target != out:
                        if job.audio:
                            gltts.export_audio(
                                bytes(job.audio),
                                str(target),
                                ffmpeg_path=engine.ffmpeg_path,
                            )
                        else:
                            _convert_audio_path(out, target, target_ext, engine.ffmpeg_path)
                    bundle.write(target, arcname=f"synthese{target_ext}")
        except Exception as exc:
            cleanup_output()
            raise APIError(f"Conversion multi-format impossible : {exc}", 502) from None
        response_path = archive_path
        response_mimetype = "application/zip"
        response_download_name = f"tts_{int(time.time())}_{len(requested_formats)}_formats.zip"
        response_attachment = True

    response = send_file(
        response_path,
        mimetype=response_mimetype,
        as_attachment=response_attachment,
        download_name=response_download_name,
        conditional=False,
    )
    response.headers["X-Transcript"] = job.transcript.replace("\n", " ")[:500]
    response.headers["X-Elapsed"] = str(result.get("elapsed", 0))
    response.headers["X-Formats"] = ",".join(requested_formats)
    response.headers["X-Primary-Format"] = ext
    if history_id is not None:
        response.headers["X-History-Id"] = str(history_id)

    response.response = ClosingIterator(response.response, [cleanup_output])
    response.call_on_close(cleanup_output)
    return response

