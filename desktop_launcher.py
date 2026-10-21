"""Point d'entr�e de l'application de bureau Fautexprimer.

FastAPI (via uvicorn) sert de backend local ; PyWebView affiche le m�me
frontend dans une fen�tre Windows native. Le port est choisi automatiquement
pour �viter tout conflit avec une autre application locale.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import webview
from webview import settings as webview_settings


def _configure_bundled_ffmpeg() -> None:
    """Expose le binaire FFmpeg embarqu� dans le PATH de l'EXE."""
    if not getattr(sys, "frozen", False):
        return
    resource_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    tools_dir = resource_dir / "tools"
    ffmpeg = tools_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if ffmpeg.is_file():
        os.environ["PATH"] = f"{tools_dir}{os.pathsep}{os.environ.get('PATH', '')}"


_configure_bundled_ffmpeg()

import web_server  # noqa: E402


def main() -> int:
    http_server = web_server.create_http_server("127.0.0.1", 0)
    port = http_server.config.port
    server_thread = threading.Thread(
        target=http_server.run,
        name="fautexprimer-uvicorn",
        daemon=True,
    )
    server_thread.start()

    # Attendre que le serveur soit pr�t � accepter les connexions.
    deadline = time.monotonic() + 15
    while not http_server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Le serveur uvicorn n'a pas d�marr� dans le d�lai imparti.")
        time.sleep(0.05)

    url = f"http://127.0.0.1:{port}/"
    if os.environ.get("FAUTEXPRIMER_SMOKE_TEST") == "1":
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError(f"Page principale : HTTP {response.status}")
            with urllib.request.urlopen(f"{url}api/health", timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError(f"API health : HTTP {response.status}")
            return 0
        finally:
            http_server.should_exit = True
            server_thread.join(timeout=5)

    webview.create_window(
        "Fautexprimer � Gemini � Edge � MiMo",
        url,
        width=1280,
        height=900,
        min_size=(900, 650),
        resizable=True,
        confirm_close=False,
        text_select=True,
    )
    try:
        # Les liens audio doivent pouvoir d�clencher la bo�te de dialogue
        # d'enregistrement de Windows. Le cache reste persistant hors du bundle.
        webview_settings["ALLOW_DOWNLOADS"] = True
        webview.start(
            debug=False,
            private_mode=False,
            storage_path=str(web_server.runtime_dir() / "webview"),
        )
    finally:
        http_server.should_exit = True
        server_thread.join(timeout=5)
        web_server.shutdown_engines()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
