"""Test rapide de l'application fautexprimer : lance le serveur et teste chaque endpoint."""
import os
import sys
import time
import urllib.request
import urllib.error
import json

os.environ["FAUTEXPRIMER_DATA_DIR"] = os.path.join(os.environ.get("TEMP", "/tmp"), "fautexprimer_test")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_server

print("=== DEMARRAGE SERVEUR ===")
server = web_server.create_http_server("127.0.0.1", 0)
port = server.config.port
print(f"Port: {port}")

import threading
t = threading.Thread(target=server.run, daemon=True)
t.start()

# Attendre le demarrage
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
print(f"Serveur pret: {server.started}")

BASE = f"http://127.0.0.1:{port}"

def check(condition, label):
    print(f"  {'OK' if condition else 'FAIL'} {label}")
    return condition

def test(name, method, path, body=None, headers=None, timeout=10):
    try:
        data = None
        if body is not None:
            data = json.dumps(body).encode()
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content = resp.read()
            size = len(content)
            print(f"  [{status}] {name} ({size} bytes)")
            return status, content
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", "replace")[:120]
        print(f"  [{e.code}] {name} - {body_err}")
        return e.code, b""
    except Exception as e:
        print(f"  [ERR] {name} - {e}")
        return 0, b""

print("\n=== TESTS GET ===")
test("GET / (page HTML)", "GET", "/")
test("GET /api/health", "GET", "/api/health")
test("GET /api/config", "GET", "/api/config")
test("GET /api/settings", "GET", "/api/settings")
status, content = test("GET /api/models/gemini", "GET", "/api/models/gemini", timeout=30)
if status == 200:
    payload = json.loads(content)
    models = payload.get("models") or []
    check(bool(models), "liste de modeles Gemini non vide")
    check(all(model.get("id") for model in models), "chaque modele possede un id")
    check(payload.get("source") in ("api", "fallback"), "source de decouverte valide")
test("GET /api/api-catalog", "GET", "/api/api-catalog")
test("GET /api/voices/edge", "GET", "/api/voices/edge")
test("GET /api/voices/edge?q=fr", "GET", "/api/voices/edge?q=fr")
test("GET /api/history", "GET", "/api/history")
test("GET /static/css/app.css", "GET", "/static/css/app.css")
test("GET /static/js/main.js", "GET", "/static/js/main.js")
test("GET /static/fautexprimer.png", "GET", "/static/fautexprimer.png")

print("\n=== TESTS POST ===")
status, content = test(
    "POST /api/settings (cles + modeles)",
    "POST", "/api/settings",
    {
        "geminiKey": "test",
        "geminiModel": "gemini-3.8-live",
        "mimoKey": "test",
        "mimoUrl": "https://api.xiaomimimo.com/v1/chat/completions",
        "mimoModel": "mimo-v2.5-tts-voiceclone",
    },
)
if status == 200:
    saved = json.loads(content).get("settings") or {}
    check(saved.get("geminiKeyConfigured") is True, "cle Gemini configuree")
    check(saved.get("geminiModel") == "gemini-3.8-live", "modele Gemini applique")
    check(saved.get("mimoKeyConfigured") is True, "cle MiMo configuree")
    check(saved.get("mimoModel") == "mimo-v2.5-tts-voiceclone", "modele MiMo applique")
    check("geminiKey" not in saved and "mimoKey" not in saved, "cles jamais exposees")
    settings_file = os.path.join(os.environ["FAUTEXPRIMER_DATA_DIR"], "settings.json")
    check(os.path.exists(settings_file), "fichier settings.json persiste")
    if os.path.exists(settings_file):
        with open(settings_file, encoding="utf-8") as handle:
            stored = json.load(handle)
        check(stored.get("geminiModel") == "gemini-3.8-live", "modele Gemini relu du disque")
test("POST /api/synthesize (vide)", "POST", "/api/synthesize", {"text": ""})
test("POST /api/synthesize (moteur inconnu)", "POST", "/api/synthesize", {"text": "test", "provider": "xxx"})

print("\n=== TESTS DELETE ===")
test("DELETE /api/history/syntheses", "DELETE", "/api/history/syntheses")
test("DELETE /api/history/voices", "DELETE", "/api/history/voices")

print("\n=== TEST SYNTHESE RÉELLE (Edge TTS, sans clé) ===")
status, content = test(
    "POST /api/synthesize (Edge, texte court)",
    "POST", "/api/synthesize",
    {"text": "Bonjour, ceci est un test de synthese vocale.", "provider": "edge", "voice": "fr-FR-DeniseNeural", "speed": 1.0, "gain": 1.0, "formats": [".wav"]},
)
if status == 200:
    print(f"  Audio recu: {len(content)} bytes")

print("\n=== TEST SYNTHESE MP3 ===")
status, content = test(
    "POST /api/synthesize (Edge, MP3)",
    "POST", "/api/synthesize",
    {"text": "Test MP3.", "provider": "edge", "voice": "fr-FR-DeniseNeural", "speed": 1.0, "gain": 1.0, "formats": [".mp3"]},
    timeout=60,
)
if status == 200:
    print(f"  Audio recu: {len(content)} bytes")

print("\n=== TESTS ERREURS ===")
test("GET /api/history/syntheses/9999/audio (404)", "GET", "/api/history/syntheses/9999/audio")
test("POST /api/synthesize (texte vide)", "POST", "/api/synthesize", {"text": ""})

print("\n=== ARRET ===")
server.should_exit = True
time.sleep(1)
print("Termine.")
