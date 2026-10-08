"""
Gemini Live Unlimited Free TTS — Engine library
===============================================

Text-to-speech engines for Google's Gemini Live, Microsoft Edge TTS and
Xiaomi MiMo (voice cloning). This reusable engine module is consumed by the
Flask/PyWebView application in ``server.py``.

Highlights
---------
* Three interchangeable engines: Gemini Live, Edge TTS, and MiMo.
* MiMo talks to a configurable HTTP endpoint with optional voice cloning.
* Thread-safe async engine with automatic reconnect/backoff and clean cancel.
* Offline (batch) rendering to WAV/MP3/OGG/FLAC/M4A.
* Per-utterance speed control (0.25x–4.0x) at constant pitch.
* Rotating logs, API-key handling, batch queue.
* Graceful degradation: no ffmpeg -> WAV still works; Python 3.13 -> rate fallback.

Requirements: pip install google-genai python-dotenv edge-tts miniaudio requests
"""

from __future__ import annotations

import importlib
import os
import sys
import base64
import queue
import time
import wave
import asyncio
import logging
import shutil
import tempfile
import threading
import subprocess
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional


# ``audioop`` was removed from the standard library in Python 3.13. Import it
# defensively and fall back to a pure-Python implementation so the app keeps
# working on newer interpreters.
try:
    import audioop as _audioop
except ImportError:  # Python 3.13+
    _audioop = None


if _audioop is None:
    import array as _array

    class _AudioOpFallback:
        """Minimal drop-in for ``audioop.ratecv`` (16-bit, N-channel, linear)."""

        @staticmethod
        def ratecv(fragment, width, nchannels, inrate, outrate, state):
            if inrate == outrate or not fragment:
                return fragment, state
            samples = _array.array("h")
            samples.frombytes(fragment[: len(fragment) - (len(fragment) % 2)])
            n_in = len(samples) // nchannels
            if n_in == 0:
                return b"", state
            n_out = max(1, int(round(n_in * outrate / inrate)))
            out = _array.array("h")
            for ch in range(nchannels):
                chan = samples[ch::nchannels]
                for i in range(n_out):
                    pos = i * (n_in - 1) / (n_out - 1) if n_out > 1 else 0
                    lo = int(pos)
                    hi = min(lo + 1, n_in - 1)
                    frac = pos - lo
                    val = int(chan[lo] * (1 - frac) + chan[hi] * frac)
                    out.append(max(-32768, min(32767, val)))
            if nchannels == 1:
                return out.tobytes(), state
            inter = _array.array("h")
            for i in range(n_out):
                for ch in range(nchannels):
                    inter.append(out[i + ch * n_out])
            return inter.tobytes(), state

    _audioop = _AudioOpFallback()


# Lecture locale facultative pour l utilisation directe du module. L'application
# de bureau lit l'audio dans WebView et ne depend donc pas de PyAudio.
pyaudio: Any = None
try:
    # L'import dynamique évite une erreur Pylance alors que PyAudio reste optionnel.
    pyaudio = importlib.import_module("pyaudio")
except (ImportError, OSError):
    pass

try:
    from google import genai
    from google.genai import types
except ImportError as exc:
    raise RuntimeError(
        "Le paquet 'google-genai' est introuvable. Installez les dépendances avec "
        "'venv\\Scripts\\python.exe -m pip install -r requirements.txt'."
    ) from exc

# Edge TTS reste facultatif : sans ce paquet, l'application utilise les autres moteurs.
try:
    import edge_tts  # type: ignore
except ImportError:
    edge_tts = None

# ``miniaudio`` ships an embedded MP3 decoder, so Edge TTS audio can be decoded
# to PCM with a plain ``pip install`` and no external ffmpeg binary.
try:
    import miniaudio  # type: ignore
except ImportError:
    miniaudio = None

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "GeminiLiveTTS"
APP_VERSION = "2.0.0"
APP_DIR = Path(os.environ.get("GEMINI_TTS_HOME", Path.home() / f".{APP_NAME.lower()}"))
LOG_PATH = APP_DIR / "app.log"


# ---------------------------------------------------------------------------
# Logging — fichier rotatif, configuré une seule fois.
# ---------------------------------------------------------------------------
def _setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr) if sys.stderr is not None else logging.NullHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    return logger


log = _setup_logging()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PA_INT16 = 8  # pyaudio.paInt16, kept as a literal so pyaudio stays optional.
FORMAT = pyaudio.paInt16 if pyaudio is not None else PA_INT16
CHANNELS = 1
RECEIVE_SAMPLE_RATE = 24000
MODEL = "models/gemini-3.1-flash-live-preview"
SAMPLE_WIDTH = 2  # 16-bit PCM -> 2 bytes per sample
CONNECT_TIMEOUT = 30.0
# Timeout for waiting on a full Gemini turn (whole audio generated). This is NOT
# a connection timeout: a long text legitimately takes far longer than the time
# to open the socket, and Gemini streams the audio as it synthesises. A single
# flat value used to be shared with CONNECT_TIMEOUT (30s), which made any text
# longer than a couple of sentences fail with "Delai depasse". The base is
# generous and grows with the text length on top of it.
TURN_TIMEOUT_BASE = 60.0
# Extra seconds of budget per 100 characters, on top of the base.
TURN_TIMEOUT_PER_100_CHARS = 6.0
# Absolute ceiling, so a runaway job cannot block the worker forever.
TURN_TIMEOUT_MAX = 600.0
MAX_RETRIES = 5
RETRY_BACKOFF = 1.6


def turn_timeout_for(text: str) -> float:
    """Scale the turn timeout with the text length (long text = longer synth)."""
    n = len(text or "")
    return min(TURN_TIMEOUT_MAX, TURN_TIMEOUT_BASE + (n / 100.0) * TURN_TIMEOUT_PER_100_CHARS)

# Identifiants techniques des moteurs, partagés par l'API et les objets Job.
PROVIDER_GEMINI = "Gemini Live"
PROVIDER_EDGE = "Edge TTS"
PROVIDER_MIMO = "MiMo TTS"

# Xiaomi MiMo (voice cloning) is driven through its Chat Completions endpoint.
# The model only exists for voice cloning: a reference audio clip is required
# and defines the voice, so there is no separate preset-voice mode.
MIMO_DEFAULT_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_DEFAULT_MODEL = "mimo-v2.5-tts-voiceclone"

# Edge TTS returns MP3 at 24 kHz mono; we decode it to the same 16-bit mono
# PCM shape the rest of the pipeline already understands.
EDGE_SAMPLE_RATE = 24000
DEFAULT_EDGE_VOICE = "fr-FR-DeniseNeural"


# Les 30 voix Gemini Live prédéfinies avec leurs métadonnées.
VOICES = [
    ("Zephyr", "Higher", "Bright", "Upbeat announcements & alerts"),
    ("Leda", "Higher", "Youthful", "Gaming & interactive apps"),
    ("Laomedeia", "Higher", "Upbeat", "Casual conversation & socials"),
    ("Achernar", "Higher", "Soft", "Bedtime stories & relaxation"),
    ("Puck", "Middle", "Upbeat", "Virtual assistants & chatbots"),
    ("Kore", "Middle", "Firm", "Tutorials & tech documentation"),
    ("Aoede", "Middle", "Breezy", "Podcasts & storytelling"),
    ("Callirrhoe", "Middle", "Easy-going", "Vlogs & casual explanations"),
    ("Autonoe", "Middle", "Bright", "Product demos & marketing"),
    ("Despina", "Middle", "Smooth", "Customer service bots"),
    ("Erinome", "Middle", "Clear", "Educational content & e-learning"),
    ("Rasalgethi", "Middle", "Informative", "News updates & reports"),
    ("Gacrux", "Middle", "Mature", "Business presentations"),
    ("Pulcherrima", "Middle", "Forward", "Direct instructions & navigation"),
    ("Vindemiatrix", "Middle", "Gentle", "Guided meditation & wellness"),
    ("Sadaltager", "Middle", "Knowledgeable", "Audiobooks & documentaries"),
    ("Sulafat", "Middle", "Warm", "Friendly companion AI"),
    ("Fenrir", "Lower Middle", "Excitable", "Gaming commentary & trailers"),
    ("Orus", "Lower Middle", "Firm", "Security announcements"),
    ("Iapetus", "Lower Middle", "Clear", "Audio guides & explainer videos"),
    ("Umbriel", "Lower Middle", "Easy-going", "Late-night radio & chill podcasts"),
    ("Alnilam", "Lower Middle", "Firm", "Corporate training materials"),
    ("Schedar", "Lower Middle", "Even", "Neutral narration"),
    ("Achird", "Lower Middle", "Friendly", "Onboarding guides"),
    ("Zubenelgenubi", "Lower Middle", "Casual", "Daily updates & notes"),
    ("Charon", "Lower", "Informative", "Scientific & historical docs"),
    ("Enceladus", "Lower", "Breathy", "Atmospheric storytelling"),
    ("Algieba", "Lower", "Smooth", "Professional voiceover"),
    ("Algenib", "Lower", "Gravelly", "Character acting & gaming"),
    ("Sadachbia", "Lower", "Lively", "Dynamic commercials"),
]
DEFAULT_VOICE = "Zephyr"
DEFAULT_PROMPT = "Repeat the following text exactly. Output nothing else:"

# Vitesses proposées par l'interface.
SPEED_PRESETS = [
    ("0.5x (tres lent)", 0.5),
    ("0.75x (lent)", 0.75),
    ("1.0x (normal)", 1.0),
    ("1.25x (rapide)", 1.25),
    ("1.5x (tres rapide)", 1.5),
    ("2.0x (extreme)", 2.0),
]
SPEED_BY_LABEL = dict(SPEED_PRESETS)

# Formats d'export proposés par l'interface.
EXPORT_FORMATS = [
    ("WAV (16-bit PCM)", ".wav"),
    ("MP3 (128 kbps)", ".mp3"),
    ("OGG Vorbis", ".ogg"),
    ("FLAC", ".flac"),
    ("M4A / AAC", ".m4a"),
]
FORMAT_BY_LABEL = dict(EXPORT_FORMATS)

# Map each container to the codec used by ffmpeg for re-encoding.
FFMPEG_CODECS = {
    ".mp3": "libmp3lame",
    ".ogg": "libvorbis",
    ".flac": "flac",
    ".m4a": "aac",
}

# Codecs that reject the engine's native 24 kHz rate and need a resample first.
# libvorbis only accepts 8/11.025/16/22.05/32/44.1/48/88.2/96/176.4/192 kHz, so
# a 24 kHz input makes it fail with "encoder setup failed".
FFMPEG_SAMPLE_RATES = {
    ".ogg": 44100,
}


def ffmpeg_available() -> bool:
    """Return True when an ``ffmpeg`` binary is reachable on this machine."""
    return shutil.which("ffmpeg") is not None


def ffmpeg_version() -> str:
    """Return the first line of ``ffmpeg -version`` or an empty string."""
    if not ffmpeg_available():
        return ""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5
        )
        return proc.stdout.splitlines()[0] if proc.stdout else ""
    except Exception:
        return ""


# ===========================================================================
# Utilitaires audio — vitesse et export multi-format.
# ===========================================================================
class AudioError(Exception):
    """Raised when an audio post-processing step fails."""


def apply_speed(pcm: bytes, speed: float) -> bytes:
    """
    Time-stretch / compress 16-bit mono PCM by ``speed`` at constant pitch.

    A factor > 1 speeds the audio up, < 1 slows it down. The clip is always
    played at the fixed 24 kHz receive rate, so we resample the *input* rate
    (keeping the output rate constant) via ``audioop.ratecv``.
    """
    if not pcm or speed == 1.0:
        return bytes(pcm)
    speed = max(0.25, min(4.0, float(speed)))
    in_rate = int(RECEIVE_SAMPLE_RATE * speed)
    converted, _ = _audioop.ratecv(
        bytes(pcm), SAMPLE_WIDTH, CHANNELS, in_rate, RECEIVE_SAMPLE_RATE, None
    )
    return converted


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Apply a linear volume ``gain`` (1.0 = unchanged) with clipping guard."""
    if not pcm or gain == 1.0:
        return bytes(pcm)
    gain = max(0.0, min(4.0, float(gain)))
    try:
        return _audioop.mul(bytes(pcm), SAMPLE_WIDTH, gain)
    except Exception:  # pragma: no cover - defensive
        return bytes(pcm)


def write_wav(path: str, pcm: bytes, sample_rate: int = RECEIVE_SAMPLE_RATE):
    """Write raw 16-bit mono PCM to a ``.wav`` file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))


def decode_mp3_to_pcm(mp3: bytes, *, ffmpeg_path: str = "") -> bytes:
    """
    Decode an MP3 byte buffer to 16-bit mono PCM at :data:`RECEIVE_SAMPLE_RATE`.

    Edge TTS streams MP3; the rest of the pipeline (speed control, gain, WAV /
    ffmpeg export) works on raw PCM, so we normalise here. Prefers ``miniaudio``
    (embedded decoder, no external binary); falls back to ffmpeg when only that
    is available.
    """
    if not mp3:
        return b""

    if miniaudio is not None:
        try:
            import array as _arr
            decoded = miniaudio.decode(
                mp3,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=CHANNELS,
                sample_rate=RECEIVE_SAMPLE_RATE,
            )
            return _arr.array("h", decoded.samples).tobytes()
        except Exception as exc:  # fall through to ffmpeg
            log.debug("miniaudio a echoue (%s), tentative via ffmpeg.", exc)

    exe = _resolve_ffmpeg(ffmpeg_path)
    cmd = [
        exe, "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ac", str(CHANNELS), "-ar", str(RECEIVE_SAMPLE_RATE),
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=mp3, capture_output=True)
    if proc.returncode != 0:
        raise AudioError(
            (proc.stderr or b"").decode("utf-8", "replace").strip()
            or "Impossible de decoder l'audio Edge TTS (installez 'miniaudio' ou 'ffmpeg')."
        )
    return proc.stdout


def _resolve_ffmpeg(ffmpeg_path: str = "") -> str:
    """Return a usable ffmpeg command (explicit path first, then PATH)."""
    if ffmpeg_path and Path(ffmpeg_path).exists():
        return ffmpeg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise AudioError(
        "ffmpeg est introuvable. Installez-le (https://ffmpeg.org) puis ajoutez-le "
        "au PATH, ou renseignez son chemin dans Parametres."
    )


def export_audio(pcm: bytes, path: str, *, ffmpeg_path: str = "", bitrate: str = "192k"):
    """
    Export raw 16-bit mono PCM to ``path``, choosing the encoder from the file
    extension. WAV is written natively; MP3/OGG/FLAC/M4A are transcoded through
    ffmpeg (raising :class:`AudioError` if ffmpeg is unavailable).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".wav", ""):
        write_wav(path, pcm)
        return

    exe = _resolve_ffmpeg(ffmpeg_path)
    codec = FFMPEG_CODECS.get(ext, "libmp3lame")
    rate = FFMPEG_SAMPLE_RATES.get(ext, RECEIVE_SAMPLE_RATE)

    # Write the intermediate WAV to a proper temp file (never next to output).
    fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="gltts_")
    os.close(fd)
    write_wav(tmp_wav, pcm)
    try:
        cmd = [
            exe, "-y", "-hide_banner", "-loglevel", "error",
            "-i", tmp_wav,
            "-ac", str(CHANNELS),
            "-ar", str(rate),
            "-codec:a", codec,
        ]
        if ext in (".mp3", ".ogg", ".m4a"):
            cmd += ["-b:a", bitrate]
        cmd.append(path)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AudioError(proc.stderr.strip() or "ffmpeg a echoue.")
        log.info("Exporte %s (%s)", path, ext)
    finally:
        try:
            os.remove(tmp_wav)
        except OSError:
            pass


# ===========================================================================
# Moteur principal — backend TTS thread-safe.
# ===========================================================================
@dataclass
class Job:
    """A single text-to-speech request travelling through the pipeline."""
    text: str
    voice: str = DEFAULT_VOICE
    system_prompt: str = DEFAULT_PROMPT
    save_path: str = ""          # optional destination (.wav/.mp3/...)
    tag: str = ""                # caller-supplied identifier (e.g. chunk index)
    speed: float = 1.0           # playback / export speed factor
    gain: float = 1.0            # playback / export volume factor
    capture: bool = False        # keep PCM on the job (for export)
    transcript: str = ""         # filled in by the worker when done
    error: str = ""              # filled in by the worker on failure
    audio: bytearray = field(default_factory=bytearray)  # captured PCM


class EngineError(Exception):
    """Raised when the background engine cannot reach or talk to Gemini."""


class TTSEngine:
    """
    Thread-safe facade around the async Gemini Live connection.

    The public surface mirrors the original module-level ``TTS`` function so
    existing callers keep working, but adds explicit lifecycle control,
    structured job objects, audio capture, playback toggling, cancellation and
    automatic reconnection with exponential backoff.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.ffmpeg_path = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._input_queue: Optional[asyncio.Queue] = None
        self._output_queue: queue.Queue = queue.Queue()
        self._playback_queue: queue.Queue = queue.Queue()
        self._playback_enabled = threading.Event()
        self._playback_enabled.set()
        self._live_speed = 1.0
        self._live_gain = 1.0
        self._bg: Optional[threading.Thread] = None
        self._playback_thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._shutdown = False
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._error_callback: Optional[Callable[[Exception], None]] = None
        self._status_callback: Optional[Callable[[str], None]] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        """Spin up the background event-loop thread and the audio player."""
        with self._lock:
            if self._bg is not None and self._bg.is_alive():
                return
            self._shutdown = False
            self._cancel.clear()
            self._ready.clear()
            self._bg = threading.Thread(target=self._run_loop, daemon=True)
            self._bg.start()
            self._playback_thread = threading.Thread(
                target=self._playback_worker, daemon=True
            )
            self._playback_thread.start()
        if not self._ready.wait(timeout=10):
            raise EngineError("Le moteur TTS n'a pas demarre (timeout).")
        log.info("Moteur TTS pret (modele=%s)", MODEL)

    def stop(self):
        """Request a graceful shutdown of the background loop."""
        self._shutdown = True
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        log.info("Arret du moteur demande.")

    def cancel_pending(self):
        """Ask the worker to stop after the in-flight segment (best effort)."""
        self._cancel.set()
        self._flush_playback()

    def clear_cancel(self):
        self._cancel.clear()

    def set_error_callback(self, callback: Optional[Callable[[Exception], None]]):
        """Register a callback invoked with an exception when the loop fails."""
        self._error_callback = callback

    def set_status_callback(self, callback: Optional[Callable[[str], None]]):
        """Register a callback invoked with human-readable status updates."""
        self._status_callback = callback

    def _emit_status(self, message: str):
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                pass

    def set_playback_enabled(self, enabled: bool):
        """Enable or mute on-device audio playback (capture still works)."""
        if enabled:
            self._playback_enabled.set()
        else:
            self._playback_enabled.clear()

    # -- public TTS API ----------------------------------------------------
    def speak(self, text: str, voice: str = DEFAULT_VOICE, *,
              collect_audio: bool = False, save_path: str = "",
              system_prompt: Optional[str] = None, tag: str = "",
              speed: float = 1.0, gain: float = 1.0) -> Job:
        """
        Queue a TTS job and block until Gemini finishes speaking.

        ``speed`` time-stretches the audio at constant pitch (1.0 = normal);
        ``gain`` applies linear volume. Returns the completed :class:`Job`.
        Its ``audio`` bytearray holds the captured (speed/gain-adjusted) PCM
        whenever ``collect_audio`` is True (or ``save_path`` is set), and its
        ``transcript`` carries the recognised text. A non-empty ``error`` field
        signals a soft failure without raising. Raises :class:`EngineError` on
        a hard backend failure or when the user cancels.
        """
        job = Job(
            text=text,
            voice=voice,
            tag=tag,
            speed=float(speed),
            gain=float(gain),
            capture=bool(collect_audio or save_path),
            save_path=save_path if (save_path or collect_audio) else "",
            system_prompt=system_prompt or DEFAULT_PROMPT,
        )
        if not text or not text.strip():
            return job
        if not self.api_key:
            raise EngineError(
                "Aucune cle d'acces. Renseignez la cle d'acces "
                "dans le .env ou via le bouton 'Parametres'."
            )

        self.start()  # idempotent
        if not (self._loop and self._input_queue):
            raise EngineError("Le moteur n'est pas encore connecte.")

        self._loop.call_soon_threadsafe(self._input_queue.put_nowait, job)
        result = self._output_queue.get()
        if isinstance(result, Exception):
            raise EngineError(str(result)) from result

        job.transcript = result
        if save_path and job.audio:
            export_audio(bytes(job.audio), save_path, ffmpeg_path=self.ffmpeg_path)
        return job

    # -- background worker plumbing ---------------------------------------
    def _run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main())
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Boucle asyncio terminee sur erreur")
            if self._error_callback:
                self._error_callback(exc)
        finally:
            loop.close()

    async def _async_main(self):
        self._input_queue = asyncio.Queue()
        self._ready.set()

        client = genai.Client(
            http_options={"api_version": "v1beta"},
            api_key=self.api_key,
        )

        current_voice = DEFAULT_VOICE
        pending_job: Optional[Job] = None  # job to replay after a voice switch
        failures = 0

        while not self._shutdown:
            config = types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                output_audio_transcription=types.AudioTranscriptionConfig(),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=current_voice
                        )
                    )
                ),
            )

            current_job: Optional[Job] = pending_job
            pending_job = None
            turn_text = ""
            turn_audio = bytearray()
            turn_done = asyncio.Event()
            receive_task = None

            try:
                self._emit_status(f"Connexion (voix={current_voice})...")
                async with client.aio.live.connect(model=MODEL, config=config) as session:
                    failures = 0
                    self._emit_status("Connecte.")

                    async def receiver():
                        nonlocal turn_text
                        async for response in session.receive():
                            sc = response.server_content
                            if not sc:
                                continue
                            if sc.output_transcription and sc.output_transcription.text:
                                turn_text += sc.output_transcription.text
                            if sc.model_turn:
                                for part in sc.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        data = part.inline_data.data
                                        turn_audio.extend(data)
                                        if self._playback_enabled.is_set():
                                            self._playback_queue.put(data)
                            if sc.turn_complete:
                                turn_done.set()

                    # NOTE: ``session.receive()`` is a *per-turn* iterator here: it
                    # stops right after the first ``turn_complete`` (verified against
                    # google-genai 2.x). The receiver task must therefore be (re)created
                    # for every job — a single session-long receiver would serve the
                    # first turn and leave every later one to time out.
                    receive_task: Optional[asyncio.Task] = None

                    while not self._shutdown:
                        if current_job is None:
                            current_job = await self._input_queue.get()

                        # User cancellation: drain queued jobs quickly.
                        if self._cancel.is_set():
                            self._cancel.clear()
                            current_job.error = "annule"
                            current_job.transcript = ""
                            self._output_queue.put(current_job.transcript)
                            self._input_queue.task_done()
                            current_job = None
                            self._flush_playback()
                            continue

                        # Voice switch requires reconnecting the session.
                        if current_job.voice != current_voice:
                            current_voice = current_job.voice
                            pending_job = current_job
                            self._emit_status(f"Bascule vers la voix {current_voice}...")
                            break  # exit context -> reconnect

                        self._flush_playback()
                        turn_text = ""
                        turn_audio = bytearray()
                        turn_done.clear()
                        self._live_speed = max(0.25, min(4.0, float(current_job.speed)))
                        self._live_gain = max(0.0, min(4.0, float(current_job.gain)))

                        # Start a fresh receive loop for this turn; retire the
                        # previous one (already ended, but cancel defensively).
                        if receive_task is not None and not receive_task.done():
                            receive_task.cancel()
                            try:
                                await receive_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        receive_task = asyncio.create_task(receiver())

                        prompt = f"{current_job.system_prompt}\n{current_job.text}"
                        # Text-only turn: ``send_realtime_input`` has no explicit
                        # end-of-turn signal for text (it is derived from speech
                        # end), so on a reused session the 2nd+ turn could hang
                        # until the timeout. ``send_client_content`` with
                        # ``turn_complete=True`` marks the turn done explicitly,
                        # which makes every turn on a live session reliable.
                        await session.send_client_content(
                            turns=types.Content(
                                role="user", parts=[types.Part(text=prompt or ".")]
                            ),
                            turn_complete=True,
                        )
                        # Wait for the full turn; the budget scales with the
                        # text length (a long text takes much longer than the
                        # socket handshake ever did).
                        turn_budget = turn_timeout_for(current_job.text)
                        try:
                            await asyncio.wait_for(turn_done.wait(), timeout=turn_budget)
                        except asyncio.TimeoutError:
                            raise EngineError(
                                f"Delai depasse en attendant la reponse "
                                f"({turn_budget:.0f}s pour {len(current_job.text)} caracteres). "
                                "Le texte est peut-etre trop long : essayez de le "
                                "reduire ou de le decouper en plusieurs syntheses."
                            )
                        await asyncio.sleep(0.05)  # let trailing audio drain

                        current_job.transcript = turn_text
                        if current_job.capture:
                            pcm = apply_speed(bytes(turn_audio), current_job.speed)
                            pcm = apply_gain(pcm, current_job.gain)
                            current_job.audio = bytearray(pcm)
                        self._output_queue.put(turn_text)
                        self._input_queue.task_done()
                        current_job = None

            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception as exc:
                # Surface an error for the in-flight job, then retry with backoff.
                log.warning("Erreur de connexion Gemini : %s", exc)
                if current_job is not None:
                    current_job.error = str(exc)
                    self._output_queue.put(exc)
                    try:
                        self._input_queue.task_done()
                    except ValueError:
                        pass
                    current_job = None
                failures += 1
                delay = min(RETRY_BACKOFF ** failures, 15.0)
                self._emit_status(
                    f"Reconnexion dans {delay:.0f}s (echec {failures}/{MAX_RETRIES})..."
                )
                if failures >= MAX_RETRIES and self._error_callback:
                    self._error_callback(exc)
                    failures = 0
                await asyncio.sleep(delay)
            finally:
                if receive_task:
                    receive_task.cancel()
                    try:
                        await receive_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass

    # -- playback worker ---------------------------------------------------
    def _playback_worker(self):
        # Local playback is best-effort: a missing pyaudio or an audio-device
        # failure must never kill the worker thread with a traceback.
        pya = None
        stream = None
        try:
            if pyaudio is None:
                log.warning("Worker audio inactif : le module 'pyaudio' est absent.")
                return
            pya = pyaudio.PyAudio()
            stream = pya.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RECEIVE_SAMPLE_RATE,
                output=True,
                frames_per_buffer=1024,
            )
            while not self._shutdown:
                chunks = []
                while not self._playback_queue.empty():
                    try:
                        chunks.append(self._playback_queue.get_nowait())
                    except queue.Empty:
                        break
                if chunks:
                    if self._playback_enabled.is_set():
                        data = b"".join(chunks)
                        speed = getattr(self, "_live_speed", 1.0)
                        gain = getattr(self, "_live_gain", 1.0)
                        try:
                            if speed and speed != 1.0:
                                data = apply_speed(data, speed)
                            if gain and gain != 1.0:
                                data = apply_gain(data, gain)
                        except Exception:
                            pass
                        stream.write(data)
                    for _ in range(len(chunks)):
                        self._playback_queue.task_done()
                else:
                    time.sleep(0.001)
        except Exception as exc:
            log.warning("Worker audio arrete: %s", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pya is not None:
                try:
                    pya.terminate()
                except Exception:
                    pass

    def _flush_playback(self):
        while not self._playback_queue.empty():
            try:
                self._playback_queue.get_nowait()
                self._playback_queue.task_done()
            except queue.Empty:
                break


# ===========================================================================
# Moteur Edge TTS — second moteur sans authentification.
# ===========================================================================
@dataclass
class EdgeVoice:
    """One Microsoft Edge neural voice, as returned by ``edge_tts.list_voices``."""
    short_name: str                  # e.g. "fr-FR-DeniseNeural" (used in requests)
    locale: str                      # e.g. "fr-FR"
    locale_name: str                 # e.g. "French (France)"
    gender: str                      # "Female" / "Male" / ""
    friendly_name: str = ""          # human label
    categories: tuple[str, ...] = ()      # e.g. ("News", "Novel")
    personalities: tuple[str, ...] = ()   # e.g. ("Positive", "Confident")

    @property
    def label(self) -> str:
        """Compact label shown in the voice tree, e.g. 'Denise (fr-FR, Female)'."""
        pretty = self.short_name.split("-")[-1].replace("Neural", "").strip()
        return f"{pretty} ({self.locale}, {self.gender or '?'})"

    @property
    def categories_text(self) -> str:
        return ", ".join(self.categories) if self.categories else "General"

    @property
    def personalities_text(self) -> str:
        return ", ".join(self.personalities) if self.personalities else ""


def _flatten_voice_tag(raw_voice: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract (ContentCategories, VoicePersonalities) from an API voice dict."""
    tag = raw_voice.get("VoiceTag") or {}
    cats = tuple(str(c) for c in (tag.get("ContentCategories") or []))
    pers = tuple(str(p) for p in (tag.get("VoicePersonalities") or []))
    return cats, pers


# Offline catalogue: every Microsoft Edge neural voice, encoded as one compact
# ``ShortName|Gender|Categories|Personalities`` line each. Used when the network
# request to the voice list fails so the application still offers the full selection.
_EDGE_VOICES_DATA = """\
af-ZA-AdriNeural|Female|General|Friendly, Positive
af-ZA-WillemNeural|Male|General|Friendly, Positive
am-ET-AmehaNeural|Male|General|Friendly, Positive
am-ET-MekdesNeural|Female|General|Friendly, Positive
ar-AE-FatimaNeural|Female|General|Friendly, Positive
ar-AE-HamdanNeural|Male|General|Friendly, Positive
ar-BH-AliNeural|Male|General|Friendly, Positive
ar-BH-LailaNeural|Female|General|Friendly, Positive
ar-DZ-AminaNeural|Female|General|Friendly, Positive
ar-DZ-IsmaelNeural|Male|General|Friendly, Positive
ar-EG-SalmaNeural|Female|General|Friendly, Positive
ar-EG-ShakirNeural|Male|General|Friendly, Positive
ar-IQ-BasselNeural|Male|General|Friendly, Positive
ar-IQ-RanaNeural|Female|General|Friendly, Positive
ar-JO-SanaNeural|Female|General|Friendly, Positive
ar-JO-TaimNeural|Male|General|Friendly, Positive
ar-KW-FahedNeural|Male|General|Friendly, Positive
ar-KW-NouraNeural|Female|General|Friendly, Positive
ar-LB-LaylaNeural|Female|General|Friendly, Positive
ar-LB-RamiNeural|Male|General|Friendly, Positive
ar-LY-ImanNeural|Female|General|Friendly, Positive
ar-LY-OmarNeural|Male|General|Friendly, Positive
ar-MA-JamalNeural|Male|General|Friendly, Positive
ar-MA-MounaNeural|Female|General|Friendly, Positive
ar-OM-AbdullahNeural|Male|General|Friendly, Positive
ar-OM-AyshaNeural|Female|General|Friendly, Positive
ar-QA-AmalNeural|Female|General|Friendly, Positive
ar-QA-MoazNeural|Male|General|Friendly, Positive
ar-SA-HamedNeural|Male|General|Friendly, Positive
ar-SA-ZariyahNeural|Female|General|Friendly, Positive
ar-SY-AmanyNeural|Female|General|Friendly, Positive
ar-SY-LaithNeural|Male|General|Friendly, Positive
ar-TN-HediNeural|Male|General|Friendly, Positive
ar-TN-ReemNeural|Female|General|Friendly, Positive
ar-YE-MaryamNeural|Female|General|Friendly, Positive
ar-YE-SalehNeural|Male|General|Friendly, Positive
az-AZ-BabekNeural|Male|General|Friendly, Positive
az-AZ-BanuNeural|Female|General|Friendly, Positive
bg-BG-BorislavNeural|Male|General|Friendly, Positive
bg-BG-KalinaNeural|Female|General|Friendly, Positive
bn-BD-NabanitaNeural|Female|General|Friendly, Positive
bn-BD-PradeepNeural|Male|General|Friendly, Positive
bn-IN-BashkarNeural|Male|General|Friendly, Positive
bn-IN-TanishaaNeural|Female|General|Friendly, Positive
bs-BA-GoranNeural|Male|General|Friendly, Positive
bs-BA-VesnaNeural|Female|General|Friendly, Positive
ca-ES-EnricNeural|Male|General|Friendly, Positive
ca-ES-JoanaNeural|Female|General|Friendly, Positive
cs-CZ-AntoninNeural|Male|General|Friendly, Positive
cs-CZ-VlastaNeural|Female|General|Friendly, Positive
cy-GB-AledNeural|Male|General|Friendly, Positive
cy-GB-NiaNeural|Female|General|Friendly, Positive
da-DK-ChristelNeural|Female|General|Friendly, Positive
da-DK-JeppeNeural|Male|General|Friendly, Positive
de-AT-IngridNeural|Female|General|Friendly, Positive
de-AT-JonasNeural|Male|General|Friendly, Positive
de-CH-JanNeural|Male|General|Friendly, Positive
de-CH-LeniNeural|Female|General|Friendly, Positive
de-DE-AmalaNeural|Female|General|Friendly, Positive
de-DE-ConradNeural|Male|General|Friendly, Positive
de-DE-FlorianMultilingualNeural|Male|General|Friendly, Positive
de-DE-KatjaNeural|Female|General|Friendly, Positive
de-DE-KillianNeural|Male|General|Friendly, Positive
de-DE-SeraphinaMultilingualNeural|Female|General|Friendly, Positive
el-GR-AthinaNeural|Female|General|Friendly, Positive
el-GR-NestorasNeural|Male|General|Friendly, Positive
en-AU-NatashaNeural|Female|General|Friendly, Positive
en-AU-WilliamMultilingualNeural|Male|General|Friendly, Positive
en-CA-ClaraNeural|Female|General|Friendly, Positive
en-CA-LiamNeural|Male|General|Friendly, Positive
en-GB-LibbyNeural|Female|General|Friendly, Positive
en-GB-MaisieNeural|Female|General|Friendly, Positive
en-GB-RyanNeural|Male|General|Friendly, Positive
en-GB-SoniaNeural|Female|General|Friendly, Positive
en-GB-ThomasNeural|Male|General|Friendly, Positive
en-HK-SamNeural|Male|General|Friendly, Positive
en-HK-YanNeural|Female|General|Friendly, Positive
en-IE-ConnorNeural|Male|General|Friendly, Positive
en-IE-EmilyNeural|Female|General|Friendly, Positive
en-IN-NeerjaExpressiveNeural|Female|General|Friendly, Positive
en-IN-NeerjaNeural|Female|General|Friendly, Positive
en-IN-PrabhatNeural|Male|General|Friendly, Positive
en-KE-AsiliaNeural|Female|General|Friendly, Positive
en-KE-ChilembaNeural|Male|General|Friendly, Positive
en-NG-AbeoNeural|Male|General|Friendly, Positive
en-NG-EzinneNeural|Female|General|Friendly, Positive
en-NZ-MitchellNeural|Male|General|Friendly, Positive
en-NZ-MollyNeural|Female|General|Friendly, Positive
en-PH-JamesNeural|Male|General|Friendly, Positive
en-PH-RosaNeural|Female|General|Friendly, Positive
en-SG-LunaNeural|Female|General|Friendly, Positive
en-SG-WayneNeural|Male|General|Friendly, Positive
en-TZ-ElimuNeural|Male|General|Friendly, Positive
en-TZ-ImaniNeural|Female|General|Friendly, Positive
en-US-AnaNeural|Female|Cartoon, Conversation|Cute
en-US-AndrewMultilingualNeural|Male|Conversation, Copilot|Warm, Confident, Authentic, Honest
en-US-AndrewNeural|Male|Conversation, Copilot|Warm, Confident, Authentic, Honest
en-US-AriaNeural|Female|News, Novel|Positive, Confident
en-US-AvaMultilingualNeural|Female|Conversation, Copilot|Expressive, Caring, Pleasant, Friendly
en-US-AvaNeural|Female|Conversation, Copilot|Expressive, Caring, Pleasant, Friendly
en-US-BrianMultilingualNeural|Male|Conversation, Copilot|Approachable, Casual, Sincere
en-US-BrianNeural|Male|Conversation, Copilot|Approachable, Casual, Sincere
en-US-ChristopherNeural|Male|News, Novel|Reliable, Authority
en-US-EmmaMultilingualNeural|Female|Conversation, Copilot|Cheerful, Clear, Conversational
en-US-EmmaNeural|Female|Conversation, Copilot|Cheerful, Clear, Conversational
en-US-EricNeural|Male|News, Novel|Rational
en-US-GuyNeural|Male|News, Novel|Passion
en-US-JennyNeural|Female|General|Friendly, Considerate, Comfort
en-US-MichelleNeural|Female|News, Novel|Friendly, Pleasant
en-US-RogerNeural|Male|News, Novel|Lively
en-US-SteffanNeural|Male|News, Novel|Rational
en-ZA-LeahNeural|Female|General|Friendly, Positive
en-ZA-LukeNeural|Male|General|Friendly, Positive
es-AR-ElenaNeural|Female|General|Friendly, Positive
es-AR-TomasNeural|Male|General|Friendly, Positive
es-BO-MarceloNeural|Male|General|Friendly, Positive
es-BO-SofiaNeural|Female|General|Friendly, Positive
es-CL-CatalinaNeural|Female|General|Friendly, Positive
es-CL-LorenzoNeural|Male|General|Friendly, Positive
es-CO-GonzaloNeural|Male|General|Friendly, Positive
es-CO-SalomeNeural|Female|General|Friendly, Positive
es-CR-JuanNeural|Male|General|Friendly, Positive
es-CR-MariaNeural|Female|General|Friendly, Positive
es-CU-BelkysNeural|Female|General|Friendly, Positive
es-CU-ManuelNeural|Male|General|Friendly, Positive
es-DO-EmilioNeural|Male|General|Friendly, Positive
es-DO-RamonaNeural|Female|General|Friendly, Positive
es-EC-AndreaNeural|Female|General|Friendly, Positive
es-EC-LuisNeural|Male|General|Friendly, Positive
es-ES-AlvaroNeural|Male|General|Friendly, Positive
es-ES-ElviraNeural|Female|General|Friendly, Positive
es-ES-XimenaNeural|Female|General|Friendly, Positive
es-GQ-JavierNeural|Male|General|Friendly, Positive
es-GQ-TeresaNeural|Female|General|Friendly, Positive
es-GT-AndresNeural|Male|General|Friendly, Positive
es-GT-MartaNeural|Female|General|Friendly, Positive
es-HN-CarlosNeural|Male|General|Friendly, Positive
es-HN-KarlaNeural|Female|General|Friendly, Positive
es-MX-DaliaNeural|Female|General|Friendly, Positive
es-MX-JorgeNeural|Male|General|Friendly, Positive
es-NI-FedericoNeural|Male|General|Friendly, Positive
es-NI-YolandaNeural|Female|General|Friendly, Positive
es-PA-MargaritaNeural|Female|General|Friendly, Positive
es-PA-RobertoNeural|Male|General|Friendly, Positive
es-PE-AlexNeural|Male|General|Friendly, Positive
es-PE-CamilaNeural|Female|General|Friendly, Positive
es-PR-KarinaNeural|Female|General|Friendly, Positive
es-PR-VictorNeural|Male|General|Friendly, Positive
es-PY-MarioNeural|Male|General|Friendly, Positive
es-PY-TaniaNeural|Female|General|Friendly, Positive
es-SV-LorenaNeural|Female|General|Friendly, Positive
es-SV-RodrigoNeural|Male|General|Friendly, Positive
es-US-AlonsoNeural|Male|General|Friendly, Positive
es-US-PalomaNeural|Female|General|Friendly, Positive
es-UY-MateoNeural|Male|General|Friendly, Positive
es-UY-ValentinaNeural|Female|General|Friendly, Positive
es-VE-PaolaNeural|Female|General|Friendly, Positive
es-VE-SebastianNeural|Male|General|Friendly, Positive
et-EE-AnuNeural|Female|General|Friendly, Positive
et-EE-KertNeural|Male|General|Friendly, Positive
fa-IR-DilaraNeural|Female|General|Friendly, Positive
fa-IR-FaridNeural|Male|General|Friendly, Positive
fi-FI-HarriNeural|Male|General|Friendly, Positive
fi-FI-NooraNeural|Female|General|Friendly, Positive
fil-PH-AngeloNeural|Male|General|Friendly, Positive
fil-PH-BlessicaNeural|Female|General|Friendly, Positive
fr-BE-CharlineNeural|Female|General|Friendly, Positive
fr-BE-GerardNeural|Male|General|Friendly, Positive
fr-CA-AntoineNeural|Male|General|Friendly, Positive
fr-CA-JeanNeural|Male|General|Friendly, Positive
fr-CA-SylvieNeural|Female|General|Friendly, Positive
fr-CA-ThierryNeural|Male|General|Friendly, Positive
fr-CH-ArianeNeural|Female|General|Friendly, Positive
fr-CH-FabriceNeural|Male|General|Friendly, Positive
fr-FR-DeniseNeural|Female|General|Friendly, Positive
fr-FR-EloiseNeural|Female|General|Friendly, Positive
fr-FR-HenriNeural|Male|General|Friendly, Positive
fr-FR-RemyMultilingualNeural|Male|General|Friendly, Positive
fr-FR-VivienneMultilingualNeural|Female|General|Friendly, Positive
ga-IE-ColmNeural|Male|General|Friendly, Positive
ga-IE-OrlaNeural|Female|General|Friendly, Positive
gl-ES-RoiNeural|Male|General|Friendly, Positive
gl-ES-SabelaNeural|Female|General|Friendly, Positive
gu-IN-DhwaniNeural|Female|General|Friendly, Positive
gu-IN-NiranjanNeural|Male|General|Friendly, Positive
he-IL-AvriNeural|Male|General|Friendly, Positive
he-IL-HilaNeural|Female|General|Friendly, Positive
hi-IN-MadhurNeural|Male|General|Friendly, Positive
hi-IN-SwaraNeural|Female|General|Friendly, Positive
hr-HR-GabrijelaNeural|Female|General|Friendly, Positive
hr-HR-SreckoNeural|Male|General|Friendly, Positive
hu-HU-NoemiNeural|Female|General|Friendly, Positive
hu-HU-TamasNeural|Male|General|Friendly, Positive
id-ID-ArdiNeural|Male|General|Friendly, Positive
id-ID-GadisNeural|Female|General|Friendly, Positive
is-IS-GudrunNeural|Female|General|Friendly, Positive
is-IS-GunnarNeural|Male|General|Friendly, Positive
it-IT-DiegoNeural|Male|General|Friendly, Positive
it-IT-ElsaNeural|Female|General|Friendly, Positive
it-IT-GiuseppeMultilingualNeural|Male|General|Friendly, Positive
it-IT-IsabellaNeural|Female|General|Friendly, Positive
iu-Cans-CA-SiqiniqNeural|Female|General|Friendly, Positive
iu-Cans-CA-TaqqiqNeural|Male|General|Friendly, Positive
iu-Latn-CA-SiqiniqNeural|Female|General|Friendly, Positive
iu-Latn-CA-TaqqiqNeural|Male|General|Friendly, Positive
ja-JP-KeitaNeural|Male|General|Friendly, Positive
ja-JP-NanamiNeural|Female|General|Friendly, Positive
jv-ID-DimasNeural|Male|General|Friendly, Positive
jv-ID-SitiNeural|Female|General|Friendly, Positive
ka-GE-EkaNeural|Female|General|Friendly, Positive
ka-GE-GiorgiNeural|Male|General|Friendly, Positive
kk-KZ-AigulNeural|Female|General|Friendly, Positive
kk-KZ-DauletNeural|Male|General|Friendly, Positive
km-KH-PisethNeural|Male|General|Friendly, Positive
km-KH-SreymomNeural|Female|General|Friendly, Positive
kn-IN-GaganNeural|Male|General|Friendly, Positive
kn-IN-SapnaNeural|Female|General|Friendly, Positive
ko-KR-HyunsuMultilingualNeural|Male|General|Friendly, Positive
ko-KR-InJoonNeural|Male|General|Friendly, Positive
ko-KR-SunHiNeural|Female|General|Friendly, Positive
lo-LA-ChanthavongNeural|Male|General|Friendly, Positive
lo-LA-KeomanyNeural|Female|General|Friendly, Positive
lt-LT-LeonasNeural|Male|General|Friendly, Positive
lt-LT-OnaNeural|Female|General|Friendly, Positive
lv-LV-EveritaNeural|Female|General|Friendly, Positive
lv-LV-NilsNeural|Male|General|Friendly, Positive
mk-MK-AleksandarNeural|Male|General|Friendly, Positive
mk-MK-MarijaNeural|Female|General|Friendly, Positive
ml-IN-MidhunNeural|Male|General|Friendly, Positive
ml-IN-SobhanaNeural|Female|General|Friendly, Positive
mn-MN-BataaNeural|Male|General|Friendly, Positive
mn-MN-YesuiNeural|Female|General|Friendly, Positive
mr-IN-AarohiNeural|Female|General|Friendly, Positive
mr-IN-ManoharNeural|Male|General|Friendly, Positive
ms-MY-OsmanNeural|Male|General|Friendly, Positive
ms-MY-YasminNeural|Female|General|Friendly, Positive
mt-MT-GraceNeural|Female|General|Friendly, Positive
mt-MT-JosephNeural|Male|General|Friendly, Positive
my-MM-NilarNeural|Female|General|Friendly, Positive
my-MM-ThihaNeural|Male|General|Friendly, Positive
nb-NO-FinnNeural|Male|General|Friendly, Positive
nb-NO-PernilleNeural|Female|General|Friendly, Positive
ne-NP-HemkalaNeural|Female|General|Friendly, Positive
ne-NP-SagarNeural|Male|General|Friendly, Positive
nl-BE-ArnaudNeural|Male|General|Friendly, Positive
nl-BE-DenaNeural|Female|General|Friendly, Positive
nl-NL-ColetteNeural|Female|General|Friendly, Positive
nl-NL-FennaNeural|Female|General|Friendly, Positive
nl-NL-MaartenNeural|Male|General|Friendly, Positive
pl-PL-MarekNeural|Male|General|Friendly, Positive
pl-PL-ZofiaNeural|Female|General|Friendly, Positive
ps-AF-GulNawazNeural|Male|General|Friendly, Positive
ps-AF-LatifaNeural|Female|General|Friendly, Positive
pt-BR-AntonioNeural|Male|General|Friendly, Positive
pt-BR-FranciscaNeural|Female|General|Friendly, Positive
pt-BR-ThalitaMultilingualNeural|Female|General|Friendly, Positive
pt-PT-DuarteNeural|Male|General|Friendly, Positive
pt-PT-RaquelNeural|Female|General|Friendly, Positive
ro-RO-AlinaNeural|Female|General|Friendly, Positive
ro-RO-EmilNeural|Male|General|Friendly, Positive
ru-RU-DmitryNeural|Male|General|Friendly, Positive
ru-RU-SvetlanaNeural|Female|General|Friendly, Positive
si-LK-SameeraNeural|Male|General|Friendly, Positive
si-LK-ThiliniNeural|Female|General|Friendly, Positive
sk-SK-LukasNeural|Male|General|Friendly, Positive
sk-SK-ViktoriaNeural|Female|General|Friendly, Positive
sl-SI-PetraNeural|Female|General|Friendly, Positive
sl-SI-RokNeural|Male|General|Friendly, Positive
so-SO-MuuseNeural|Male|General|Friendly, Positive
so-SO-UbaxNeural|Female|General|Friendly, Positive
sq-AL-AnilaNeural|Female|General|Friendly, Positive
sq-AL-IlirNeural|Male|General|Friendly, Positive
sr-RS-NicholasNeural|Male|General|Friendly, Positive
sr-RS-SophieNeural|Female|General|Friendly, Positive
su-ID-JajangNeural|Male|General|Friendly, Positive
su-ID-TutiNeural|Female|General|Friendly, Positive
sv-SE-MattiasNeural|Male|General|Friendly, Positive
sv-SE-SofieNeural|Female|General|Friendly, Positive
sw-KE-RafikiNeural|Male|General|Friendly, Positive
sw-KE-ZuriNeural|Female|General|Friendly, Positive
sw-TZ-DaudiNeural|Male|General|Friendly, Positive
sw-TZ-RehemaNeural|Female|General|Friendly, Positive
ta-IN-PallaviNeural|Female|General|Friendly, Positive
ta-IN-ValluvarNeural|Male|General|Friendly, Positive
ta-LK-KumarNeural|Male|General|Friendly, Positive
ta-LK-SaranyaNeural|Female|General|Friendly, Positive
ta-MY-KaniNeural|Female|General|Friendly, Positive
ta-MY-SuryaNeural|Male|General|Friendly, Positive
ta-SG-AnbuNeural|Male|General|Friendly, Positive
ta-SG-VenbaNeural|Female|General|Friendly, Positive
te-IN-MohanNeural|Male|General|Friendly, Positive
te-IN-ShrutiNeural|Female|General|Friendly, Positive
th-TH-NiwatNeural|Male|General|Friendly, Positive
th-TH-PremwadeeNeural|Female|General|Friendly, Positive
tr-TR-AhmetNeural|Male|General|Friendly, Positive
tr-TR-EmelNeural|Female|General|Friendly, Positive
uk-UA-OstapNeural|Male|General|Friendly, Positive
uk-UA-PolinaNeural|Female|General|Friendly, Positive
ur-IN-GulNeural|Female|General|Friendly, Positive
ur-IN-SalmanNeural|Male|General|Friendly, Positive
ur-PK-AsadNeural|Male|General|Friendly, Positive
ur-PK-UzmaNeural|Female|General|Friendly, Positive
uz-UZ-MadinaNeural|Female|General|Friendly, Positive
uz-UZ-SardorNeural|Male|General|Friendly, Positive
vi-VN-HoaiMyNeural|Female|General|Friendly, Positive
vi-VN-NamMinhNeural|Male|General|Friendly, Positive
zh-CN-XiaoxiaoNeural|Female|News, Novel|Warm
zh-CN-XiaoyiNeural|Female|Cartoon, Novel|Lively
zh-CN-YunjianNeural|Male|Sports, Novel|Passion
zh-CN-YunxiNeural|Male|Novel|Lively, Sunshine
zh-CN-YunxiaNeural|Male|Cartoon, Novel|Cute
zh-CN-YunyangNeural|Male|News|Professional, Reliable
zh-CN-liaoning-XiaobeiNeural|Female|Dialect|Humorous
zh-CN-shaanxi-XiaoniNeural|Female|Dialect|Bright
zh-HK-HiuGaaiNeural|Female|General|Friendly, Positive
zh-HK-HiuMaanNeural|Female|General|Friendly, Positive
zh-HK-WanLungNeural|Male|General|Friendly, Positive
zh-TW-HsiaoChenNeural|Female|General|Friendly, Positive
zh-TW-HsiaoYuNeural|Female|General|Friendly, Positive
zh-TW-YunJheNeural|Male|General|Friendly, Positive
zu-ZA-ThandoNeural|Female|General|Friendly, Positive
zu-ZA-ThembaNeural|Male|General|Friendly, Positive
"""

# Language subtag -> English name, used to build readable locale names for the
# bundled (offline) catalogue. Covers every language present in
# :data:`_EDGE_VOICES_DATA`.
_LANG_NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "az": "Azerbaijani",
    "bg": "Bulgarian", "bn": "Bengali", "bs": "Bosnian", "ca": "Catalan",
    "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian",
    "fa": "Persian", "fi": "Finnish", "fil": "Filipino", "fr": "French",
    "ga": "Irish", "gl": "Galician", "gu": "Gujarati", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "id": "Indonesian",
    "is": "Icelandic", "it": "Italian", "iu": "Inuktitut", "ja": "Japanese",
    "jv": "Javanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer",
    "kn": "Kannada", "ko": "Korean", "lo": "Lao", "lt": "Lithuanian",
    "lv": "Latvian", "mk": "Macedonian", "ml": "Malayalam", "mn": "Mongolian",
    "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese",
    "nb": "Norwegian Bokmal", "ne": "Nepali", "nl": "Dutch", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "sq": "Albanian", "sr": "Serbian", "su": "Sundanese", "sv": "Swedish",
    "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "vi": "Vietnamese", "zh": "Chinese", "zu": "Zulu",
}

# The few locales whose second subtag is a writing system, not a country.
_SCRIPT_LOCALE_NAMES = {
    "iu-Cans": "Inuktitut (Syllabics)",
    "iu-Latn": "Inuktitut (Latin)",
}


def _locale_display_name(locale: str) -> str:
    """Best-effort offline display name, e.g. ``fr-FR`` -> ``French (FR)``."""
    if locale in _SCRIPT_LOCALE_NAMES:
        return _SCRIPT_LOCALE_NAMES[locale]
    lang, _, region = locale.partition("-")
    name = _LANG_NAMES.get(lang, lang)
    return f"{name} ({region})" if region else name


def builtin_edge_voices() -> list[EdgeVoice]:
    """Return the bundled offline Edge TTS catalogue (locale-sorted)."""
    voices: list[EdgeVoice] = []
    for line in _EDGE_VOICES_DATA.strip().splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2 or not parts[0]:
            continue
        short, gender = parts[0], parts[1]
        cats = tuple(c.strip() for c in parts[2].split(",") if c.strip()) \
            if len(parts) > 2 else ()
        pers = tuple(p.strip() for p in parts[3].split(",") if p.strip()) \
            if len(parts) > 3 else ()
        locale = "-".join(short.split("-")[:2])
        voices.append(
            EdgeVoice(
                short_name=short,
                locale=locale,
                locale_name=_locale_display_name(locale),
                gender=gender,
                friendly_name=short,
                categories=cats,
                personalities=pers,
            )
        )
    voices.sort(key=lambda v: (v.locale, v.gender, v.short_name))
    return voices


def load_edge_voices(timeout: float = 20.0) -> list[EdgeVoice]:
    """
    Fetch the full Edge TTS voice catalogue (network call, ~300 entries).

    Falls back to a bundled offline catalogue when the network request fails,
    so the application still lists voices without a connection. Raises
    :class:`EngineError` only when ``edge-tts`` itself is missing.
    """
    if edge_tts is None:
        raise EngineError(
            "Le paquet 'edge-tts' n'est pas installe. Lancez :\n"
            "    pip install edge-tts"
        )
    try:
        raw = asyncio.run(asyncio.wait_for(edge_tts.list_voices(), timeout=timeout))
    except Exception as exc:
        log.warning("Voix Edge TTS indisponibles en ligne (%s) : catalogue hors-ligne.", exc)
        return builtin_edge_voices()
    voices = []
    for v in raw:
        short = v.get("ShortName", "")
        if not short:
            continue
        cats, pers = _flatten_voice_tag(v)
        voices.append(
            EdgeVoice(
                short_name=short,
                locale=v.get("Locale", ""),
                locale_name=v.get("LocaleName", "") or v.get("Locale", ""),
                gender=v.get("Gender", ""),
                friendly_name=v.get("FriendlyName", ""),
                categories=cats,
                personalities=pers,
            )
        )
    voices.sort(key=lambda v: (v.locale, v.gender, v.short_name))
    log.info("Edge TTS : %d voix chargees.", len(voices))
    return voices or builtin_edge_voices()


class EdgeTTSEngine:
    """
    Self-contained Edge TTS backend exposing the same surface as
    :class:`TTSEngine`, ce qui permet à l'interface de changer de moteur.

    Each request opens a short-lived ``edge_tts.Communicate`` stream, accumulates
    the MP3, optionally decodes it to PCM (for capture/export and playback),
    applies speed/gain and reports the recognised transcript. Playback reuses a
    single shared pyaudio stream owned by this engine.
    """

    def __init__(self, api_key: str | None = None):
        # ``api_key`` is accepted (and ignored) for interface parity with TTSEngine.
        self.ffmpeg_path = ""
        self._playback_enabled = threading.Event()
        self._playback_enabled.set()
        self._live_speed = 1.0
        self._live_gain = 1.0
        self._shutdown = False
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._error_callback: Optional[Callable[[Exception], None]] = None
        self._status_callback: Optional[Callable[[str], None]] = None

    # -- lifecycle (no-op, but present for parity) ------------------------
    def start(self):
        self._shutdown = False

    def stop(self):
        self._shutdown = True

    def cancel_pending(self):
        self._cancel.set()

    def clear_cancel(self):
        self._cancel.clear()

    def set_error_callback(self, callback: Optional[Callable[[Exception], None]]):
        self._error_callback = callback

    def set_status_callback(self, callback: Optional[Callable[[str], None]]):
        self._status_callback = callback

    def _emit_status(self, message: str):
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                pass

    def set_playback_enabled(self, enabled: bool):
        if enabled:
            self._playback_enabled.set()
        else:
            self._playback_enabled.clear()

    # -- public TTS API ---------------------------------------------------
    def speak(self, text: str, voice: str = DEFAULT_EDGE_VOICE, *,
              collect_audio: bool = False, save_path: str = "",
              system_prompt: Optional[str] = None, tag: str = "",
              speed: float = 1.0, gain: float = 1.0) -> Job:
        """
        Synthesise ``text`` with Edge TTS and block until done.

        ``voice`` is an Edge short-name (e.g. ``fr-FR-DeniseNeural``). Speed and
        gain are applied to the decoded PCM exactly like the Gemini engine, so
        export and playback behave identically across providers.
        """
        job = Job(
            text=text,
            voice=voice,
            tag=tag,
            speed=float(speed),
            gain=float(gain),
            capture=bool(collect_audio or save_path),
            save_path=save_path if (save_path or collect_audio) else "",
            system_prompt=system_prompt or DEFAULT_PROMPT,
        )
        if not text or not text.strip():
            return job
        if edge_tts is None:
            raise EngineError(
                "Le paquet 'edge-tts' n'est pas installe. Lancez : pip install edge-tts"
            )

        self._cancel.clear()
        # Edge accepts rate only within [-100%, +100%] (0x..2x). Clamp the network
        # request there and time-stretch the leftover locally after decoding, so
        # the full 0.25x..4.0x range still works.
        speed = max(0.25, min(4.0, job.speed))
        edge_rate = max(0.0, min(2.0, speed))
        residual_speed = speed / edge_rate if edge_rate else 1.0
        rate_pct = int(round((edge_rate - 1.0) * 100))
        rate = f"{rate_pct:+d}%"
        # Edge volume is likewise limited to [-100%, +100%].
        gain = max(0.0, min(4.0, job.gain))
        edge_volume = max(0.0, min(2.0, gain))
        residual_gain = gain / edge_volume if edge_volume else 1.0
        volume_pct = int(round((edge_volume - 1.0) * 100))
        volume = f"{volume_pct:+d}%"

        self._emit_status(f"Synthese ({voice})...")
        try:
            mp3, transcript = asyncio.run(
                self._synthesize(text, voice, rate=rate, volume=volume)
            )
        except EngineError:
            raise
        except Exception as exc:
            if self._error_callback:
                self._error_callback(exc)
            raise EngineError(f"Echec de la synthese : {exc}") from exc

        if self._cancel.is_set():
            self._cancel.clear()
            job.error = "annule"
            return job

        job.transcript = transcript

        # Decode to PCM once; reuse it for both playback and capture/export.
        try:
            pcm = decode_mp3_to_pcm(mp3, ffmpeg_path=self.ffmpeg_path)
        except AudioError:
            # No MP3 decoder available. Raw MP3 bytes can only be saved as-is
            # when the destination really is an MP3; writing them to a .wav
            # path would produce a corrupt file.
            if job.capture and job.save_path:
                if Path(job.save_path).suffix.lower() == ".mp3":
                    Path(job.save_path).write_bytes(mp3)
                    return job
                raise EngineError(
                    "Export vers "
                    f"'{Path(job.save_path).suffix}' impossible : un decodeur "
                    "MP3 est necessaire pour convertir. Installez 'miniaudio' "
                    "(pip install miniaudio) ou 'ffmpeg', ou exportez en MP3."
                )
            if job.capture:
                raise EngineError(
                    "La lecture/export necessite un decodeur MP3. "
                    "Installez 'miniaudio' (pip install miniaudio) ou 'ffmpeg'."
                )
            pcm = b""

        # Edge already applied the representable part of speed/gain at the
        # network level; apply the residual locally (e.g. >2x or <0.5x).
        if pcm:
            if residual_speed not in (1.0, 0.0):
                pcm = apply_speed(pcm, residual_speed)
            if residual_gain not in (1.0, 0.0):
                pcm = apply_gain(pcm, residual_gain)
        if job.capture and pcm:
            job.audio = bytearray(pcm)

        # Edge already speaks MP3 natively. When the caller asked for an MP3
        # file and no residual speed/gain transform is needed, write those bytes
        # directly instead of decoding to PCM and re-encoding via ffmpeg (which
        # would needlessly fail when ffmpeg is absent, even though the audio is
        # already exactly what was requested).
        if job.save_path and Path(job.save_path).suffix.lower() == ".mp3" \
                and residual_speed in (1.0, 0.0) and residual_gain in (1.0, 0.0):
            Path(job.save_path).write_bytes(mp3)
        elif pcm and job.save_path:
            export_audio(pcm, job.save_path, ffmpeg_path=self.ffmpeg_path)

        if pcm and self._playback_enabled.is_set():
            self._play_audio(pcm)

        return job

    async def _synthesize(self, text: str, voice: str, *, rate: str, volume: str):
        """Stream one utterance; return (mp3_bytes, transcript)."""
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        mp3 = bytearray()
        async for chunk in communicate.stream():
            if self._shutdown or self._cancel.is_set():
                break
            ctype = chunk.get("type")
            if ctype == "audio":
                mp3.extend(chunk.get("data", b""))
            elif ctype in ("WordBoundary", "SentenceBoundary"):
                # Edge reports boundary offsets, not text; keep the source text
                # as the transcript so history stays meaningful.
                pass
        return bytes(mp3), text

    def _play_audio(self, pcm: bytes):
        """Play raw 16-bit mono PCM on a short-lived pyaudio stream."""
        if not pcm:
            return
        try:
            pya = pyaudio.PyAudio()
        except Exception as exc:
            log.warning("Lecture Edge impossible : %s", exc)
            return
        stream = None
        try:
            stream = pya.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RECEIVE_SAMPLE_RATE,
                output=True,
                frames_per_buffer=1024,
            )
            chunk_size = 4800  # 100 ms of 24 kHz/16-bit mono
            for i in range(0, len(pcm), chunk_size):
                if self._shutdown or not self._playback_enabled.is_set():
                    break
                stream.write(pcm[i:i + chunk_size])
        except Exception as exc:
            log.warning("Erreur de lecture Edge TTS : %s", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            pya.terminate()


# ===========================================================================
# MiMo TTS engine — optional voice cloning through an HTTP endpoint
# ===========================================================================
class MiMoTTSEngine:
    """
    Xiaomi MiMo backend driven through its Chat Completions endpoint.

    Works against the MiMo ``POST /v1/chat/completions`` API. The
    ``mimo-v2.5-tts-voiceclone`` model performs **voice cloning**: a reference
    audio clip (path, plus its optional transcript) is always required and is
    passed as a data URL in ``audio.voice``.

    L'API reprend celle de :class:`EdgeTTSEngine`, ce qui permet à l'interface de
    changer de moteur. Le PCM renvoyé conserve le même comportement de lecture,
    d'export et de changement de vitesse.
    """

    def __init__(self, api_key: str | None = None):
        self.ffmpeg_path = ""
        self.api_key = api_key or ""
        self.ref_audio = ""
        self.ref_text = ""
        # MiMo (chat-completions) endpoint settings.
        self.mimo_url = MIMO_DEFAULT_URL
        self.mimo_key = ""
        self.mimo_model = MIMO_DEFAULT_MODEL
        self.timeout = 60.0
        self._playback_enabled = threading.Event()
        self._playback_enabled.set()
        self._shutdown = False
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._error_callback: Optional[Callable[[Exception], None]] = None
        self._status_callback: Optional[Callable[[str], None]] = None

    # -- lifecycle (present for parity with the other engines) ------------
    def start(self):
        self._shutdown = False

    def stop(self):
        self._shutdown = True

    def cancel_pending(self):
        self._cancel.set()

    def clear_cancel(self):
        self._cancel.clear()

    def set_error_callback(self, callback: Optional[Callable[[Exception], None]]):
        self._error_callback = callback

    def set_status_callback(self, callback: Optional[Callable[[str], None]]):
        self._status_callback = callback

    def _emit_status(self, message: str):
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                pass

    def set_playback_enabled(self, enabled: bool):
        if enabled:
            self._playback_enabled.set()
        else:
            self._playback_enabled.clear()

    # -- public TTS API ---------------------------------------------------
    def speak(self, text: str, voice: str = "", *,
              collect_audio: bool = False, save_path: str = "",
              system_prompt: Optional[str] = None, tag: str = "",
              speed: float = 1.0, gain: float = 1.0) -> Job:
        """
        Synthesise ``text`` through the configured MiMo endpoint.

        The voice is defined by the reference clip set on the engine
        (``ref_audio``); ``voice`` is accepted for signature parity with the
        other engines but is unused. Speed and gain are applied to the decoded
        PCM like every other engine.
        """
        job = Job(
            text=text,
            voice=voice,
            tag=tag,
            speed=float(speed),
            gain=float(gain),
            capture=bool(collect_audio or save_path),
            save_path=save_path if (save_path or collect_audio) else "",
            system_prompt=system_prompt or DEFAULT_PROMPT,
        )
        if not text or not text.strip():
            return job

        self._cancel.clear()
        self._emit_status("Clonage de voix...")
        try:
            audio_bytes, content_type = self._request_audio(text, voice)
        except EngineError:
            raise
        except Exception as exc:
            if self._error_callback:
                self._error_callback(exc)
            raise EngineError(f"Echec de la synthese : {exc}") from exc

        if self._cancel.is_set():
            self._cancel.clear()
            job.error = "annule"
            return job

        job.transcript = text

        # The endpoint may return WAV/PCM (decoded directly) or MP3 (decoded).
        try:
            pcm = self._to_pcm(audio_bytes, content_type)
        except AudioError:
            if job.capture and job.save_path:
                Path(job.save_path).write_bytes(audio_bytes)
                return job
            if job.capture:
                raise EngineError(
                    "Lecture/export impossible : format audio non "
                    "decodable. Installez 'miniaudio' (pip install miniaudio) "
                    "ou 'ffmpeg'."
                )
            pcm = b""

        if pcm:
            if job.speed not in (1.0, 0.0):
                pcm = apply_speed(pcm, job.speed)
            if job.gain not in (1.0, 0.0):
                pcm = apply_gain(pcm, job.gain)
        if job.capture and pcm:
            job.audio = bytearray(pcm)

        if pcm and job.save_path:
            export_audio(pcm, job.save_path, ffmpeg_path=self.ffmpeg_path)

        if pcm and self._playback_enabled.is_set():
            self._play_pcm(pcm)

        return job

    # -- HTTP plumbing ----------------------------------------------------
    def _request_audio(self, text: str, voice: str = "") -> tuple[bytes, str]:
        """Send the request; return (audio_bytes, content_type)."""
        return self._request_audio_chat(text)

    def _request_audio_chat(self, text: str) -> tuple[bytes, str]:
        """
        Xiaomi MiMo Chat Completions request.

        The reference clip (for voice cloning) is passed as a data URL in
        ``audio.voice``; the text to speak goes in the assistant turn. The
        response carries base64 audio at ``choices[0].message.audio.data``.
        """
        import requests

        if not self.ref_audio or not Path(self.ref_audio).exists():
            raise EngineError(
                "Clonage de voix : selectionnez un fichier audio de reference."
            )
        ref_path = Path(self.ref_audio)
        suffix = ref_path.suffix.lower().lstrip(".") or "wav"
        mime = {"mp3": "mpeg", "m4a": "mp4"}.get(suffix, suffix)
        voice_b64 = base64.b64encode(ref_path.read_bytes()).decode("ascii")
        voice_data_url = f"data:audio/{mime};base64,{voice_b64}"

        url = self.mimo_url or MIMO_DEFAULT_URL
        payload = {
            "model": self.mimo_model or MIMO_DEFAULT_MODEL,
            "messages": [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": "wav", "voice": voice_data_url},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = self.mimo_key or self.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"

        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            detail = resp.text[:300] if resp.text else ""
            # MiMo's voice-clone model rejects a missing reference clip with
            # HTTP 400 "audio.voice must not be empty": surface a clear hint.
            lower = detail.lower()
            if "audio.voice" in lower or "voice clone" in lower or "must not be empty" in lower:
                raise EngineError(
                    "Le modele de clonage exige un clip de reference "
                    "(audio.voice). Selectionnez un fichier audio de quelques "
                    "secondes de parole, puis relancez l'operation."
                )
            raise EngineError(f"Reponse inattendue du service : HTTP {resp.status_code} — {detail}")

        try:
            doc = resp.json()
        except ValueError as exc:
            raise EngineError("Reponse du service non-JSON.") from exc

        encoded = None
        try:
            message = doc["choices"][0]["message"]
            audio_obj = message.get("audio") or {}
            encoded = audio_obj.get("data") if isinstance(audio_obj, dict) else None
        except (KeyError, IndexError, TypeError):
            encoded = None
        if not encoded:
            raise EngineError(
                "Reponse du service sans audio (choices[0].message.audio.data absent)."
            )
        return base64.b64decode(encoded), "audio/wav"

    def _to_pcm(self, audio: bytes, content_type: str) -> bytes:
        """Decode endpoint output to 16-bit mono PCM at RECEIVE_SAMPLE_RATE."""
        if not audio:
            return b""
        if audio[:4] == b"RIFF":
            return self._wav_to_pcm(audio)
        # MP3 / other containers: reuse the shared decoder (miniaudio/ffmpeg).
        return decode_mp3_to_pcm(audio, ffmpeg_path=self.ffmpeg_path)

    @staticmethod
    def _wav_to_pcm(data: bytes) -> bytes:
        """Read a WAV byte buffer and return 16-bit mono PCM at 24 kHz."""
        import io
        with wave.open(io.BytesIO(data), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if width != SAMPLE_WIDTH:
            raise AudioError(f"WAV non 16-bit ({width * 8} bits).")
        # Normalise channel count to mono.
        if channels == 2:
            frames = _audioop.tomono(frames, SAMPLE_WIDTH, 0.5, 0.5)
        elif channels != 1:
            raise AudioError(f"WAV a {channels} canaux (mono/stereo attendu).")
        # Normalise sample rate to the shared receive rate.
        if rate != RECEIVE_SAMPLE_RATE:
            frames, _ = _audioop.ratecv(
                frames, SAMPLE_WIDTH, 1, rate, RECEIVE_SAMPLE_RATE, None
            )
        return frames

    def _play_pcm(self, pcm: bytes):
        """Play raw 16-bit mono PCM on a short-lived pyaudio stream."""
        if not pcm:
            return
        try:
            pya = pyaudio.PyAudio()
        except Exception as exc:
            log.warning("Lecture MiMo impossible : %s", exc)
            return
        stream = None
        try:
            stream = pya.open(
                format=FORMAT, channels=CHANNELS, rate=RECEIVE_SAMPLE_RATE,
                output=True, frames_per_buffer=1024,
            )
            chunk_size = 4800
            for i in range(0, len(pcm), chunk_size):
                if self._shutdown or not self._playback_enabled.is_set():
                    break
                stream.write(pcm[i:i + chunk_size])
        except Exception as exc:
            log.warning("Erreur de lecture MiMo : %s", exc)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            pya.terminate()

    @staticmethod
    def _probe_hint(status: int) -> str:
        """Turn an HTTP status into an actionable French hint (probe only)."""
        if status in (401, 403):
            return "authentification refusee : verifiez la cle API."
        if status == 404:
            return "endpoint introuvable : verifiez l'URL."
        if status == 400:
            return ("requete refusee : verifiez le nom du modele et le clip de "
                    "reference (format audio reconnu, quelques secondes de parole).")
        if status == 429:
            return "quota atteint : reessayez plus tard."
        if status >= 500:
            return "erreur cote serveur."
        return "la requete de test a ete refusee."

    def _check_mimo_endpoint(self) -> tuple[bool, str]:
        """
        Probe the MiMo chat endpoint.

        The ``mimo-v2.5-tts-voiceclone`` model always needs a reference clip in
        ``audio.voice``, so a probe without one cannot succeed: it is reported as
        an actionable message instead of a misleading HTTP 400. With a clip the
        probe is a real (tiny) synthesis, which proves the whole chain
        (key + model + cloning) works.
        """
        url = self.mimo_url or MIMO_DEFAULT_URL
        if not url:
            return False, "Aucune adresse de service definie."

        ref = self.ref_audio
        if not (ref and Path(ref).exists()):
            return False, (
                "Clip de reference requis : le modele de clonage "
                "exige un fichier audio "
                "(audio.voice). Selectionnez un clip de quelques secondes "
                "de parole, puis relancez le test."
            )

        try:
            audio, _ctype = self._request_audio_chat("Test.")
        except EngineError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, f"Injoignable : {exc}"
        if audio:
            return True, f"Service OK : {len(audio)} octets d'audio recus."
        return False, "Le service a repondu sans audio."

    def check_endpoint(self) -> tuple[bool, str]:
        """Probe the MiMo endpoint; return (reachable, human-readable message)."""
        return self._check_mimo_endpoint()


# Module-level singleton so the legacy ``TTS`` function still works.
_engine: Optional[TTSEngine] = None
_edge_engine: Optional[EdgeTTSEngine] = None
_mimo_engine: Optional[MiMoTTSEngine] = None


def get_engine() -> TTSEngine:
    global _engine
    if _engine is None:
        _engine = TTSEngine()
    return _engine


def get_edge_engine() -> EdgeTTSEngine:
    global _edge_engine
    if _edge_engine is None:
        _edge_engine = EdgeTTSEngine()
    return _edge_engine


def get_mimo_engine() -> MiMoTTSEngine:
    global _mimo_engine
    if _mimo_engine is None:
        _mimo_engine = MiMoTTSEngine()
    return _mimo_engine


def TTS(query, voice=DEFAULT_VOICE):
    """Backwards-compatible helper: synthesise ``query`` and return transcript."""
    job = get_engine().speak(query, voice)
    return job.transcript

