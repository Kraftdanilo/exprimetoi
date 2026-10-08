"""Point d'entrée de l'application de bureau Fautexprimer.

Flask reste le backend local ; PyWebView affiche le même frontend dans une
fenêtre Windows native. Le port est choisi automatiquement pour éviter tout
conflit avec une autre application locale.
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.request
from pathlib import Path

import webview
from webview import settings as webview_settings


def _configure_bundled_ffmpeg() -> None:
    """Expose le binaire FFmpeg embarqué dans le PATH de l'EXE."""
    if not getattr(sys, "frozen", False):
        return
    resource_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    tools_dir = resource_dir / "tools"
    ffmpeg = tools_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if ffmpeg.is_file():
        os.environ["PATH"] = f"{tools_dir}{os.pathsep}{os.environ.get('PATH', '')}"


_configure_bundled_ffmpeg()

import server  # noqa: E402


def main() -> int:
    http_server = server.create_http_server("127.0.0.1", 0)
    port = http_server.server_port
    server_thread = threading.Thread(
        target=http_server.serve_forever,
        name="fautexprimer-flask",
        daemon=True,
    )
    server_thread.start()

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
            http_server.shutdown()
            http_server.server_close()
            server_thread.join(timeout=5)

    webview.create_window(
        "Fautexprimer — Eben · Dane · Michel",
        url,
        width=1280,
        height=900,
        min_size=(900, 650),
        resizable=True,
        confirm_close=False,
        text_select=True,
    )
    try:
        # Les liens audio doivent pouvoir declencher la boite de dialogue
        # d'enregistrement de Windows. Le cache reste persistant hors du bundle.
        webview_settings["ALLOW_DOWNLOADS"] = True
        webview.start(
            debug=False,
            private_mode=False,
            storage_path=str(server.runtime_dir() / "webview"),
        )
    finally:
        http_server.shutdown()
        http_server.server_close()
        server_thread.join(timeout=5)
        server.shutdown_engines()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
