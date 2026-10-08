# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller Windows reproductible pour Fautexprimer."""

from pathlib import Path
import shutil

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)
ICON = ROOT / "assets" / "fautexprimer.ico"
if not ICON.is_file():
    raise SystemExit("assets/fautexprimer.ico est introuvable; lancez tools/generate_icon.py.")
FFMPEG = shutil.which("ffmpeg")
if not FFMPEG:
    raise SystemExit("ffmpeg est introuvable dans le PATH; installez-le avant le build.")

datas = [(str(ROOT / "webapp"), "webapp")]
binaries = [(FFMPEG, "tools")]
hiddenimports = [
    "Google_Gemini_Live_TTS",
    "history_store",
    "sqlite3",
    "clr",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
]

# Les paquets utilisent des imports/certificats chargés dynamiquement.
for package in ("edge_tts", "google.genai"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Evite d'embarquer des runtimes graphiques et du materiel inutilises par le
# backend Flask et le backend PyWebView Edge.
excludes = [
    "PyQt5", "PyQt6", "PySide2", "PySide6", "gtk", "cefpython",
    "tkinter", "pyaudio", "PIL", "PIL.*", "pytest", "IPython",
]

a = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fautexprimer",
    debug=False,
    icon=str(ICON),
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Fautexprimer",
)
