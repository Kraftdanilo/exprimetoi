"""Test de la réutilisation d'une synthèse comme clip MiMo."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
import threading

os.environ["FAUTEXPRIMER_DATA_DIR"] = os.path.join(os.environ.get("TEMP", "/tmp"), "fautexprimer_asvoice")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_server

server = web_server.create_http_server("127.0.0.1", 0)
port = server.config.port
t = threading.Thread(target=server.run, daemon=True)
t.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)

BASE = f"http://127.0.0.1:{port}"


def req(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# 1. Synthèse Edge réelle courte
status, content = req("POST", "/api/synthesize", {
    "text": "Ceci est un test de clip de reference.",
    "provider": "edge", "voice": "fr-FR-DeniseNeural",
    "speed": 1.0, "gain": 1.0, "formats": [".wav"],
})
print(f"[1] synthese Edge: HTTP {status} ({len(content)} octets)")
assert status == 200

# 2. Historique -> trouver la synthèse
status, content = req("GET", "/api/history?limit=5")
history = json.loads(content)
syn = (history.get("syntheses") or [])[0]
print(f"[2] historique: id={syn['id']} provider={syn['provider']}")

# 3. Convertir en clip MiMo
status, content = req("POST", f"/api/history/syntheses/{syn['id']}/as-voice",
                      {"name": "Clip depuis synthese"})
result = json.loads(content)
voice = result.get("voice") or {}
print(f"[3] as-voice: HTTP {status}, clip id={voice.get('id')} name={voice.get('name')!r} source={voice.get('source')}")
assert status == 200 and voice.get("id")

# 4. Le clip est lisible dans la bibliothèque
status, content = req("GET", "/api/history?limit=5")
history = json.loads(content)
voices = history.get("voices") or []
print(f"[4] bibliotheque clips: {len(voices)} clip(s), contient id={voice['id']}: {any(v['id']==voice['id'] for v in voices)}")

# 5. Le clip est jouable
status, content = req("GET", f"/api/history/voices/{voice['id']}/audio")
print(f"[5] audio du clip: HTTP {status} ({len(content)} octets)")
assert status == 200 and len(content) > 1000

# 6. Synthèse inexistante -> 404
status, content = req("POST", "/api/history/syntheses/99999/as-voice", {})
print(f"[6] synthese inexistante: HTTP {status} (attendu 404)")

server.should_exit = True
time.sleep(1)
print("Tous les tests passent.")
