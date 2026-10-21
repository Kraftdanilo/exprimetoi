"""Test des plafonds Gemini et Edge avec de longues phrases/textes rÃ©els."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
import threading

os.environ["FAUTEXPRIMER_DATA_DIR"] = os.path.join(os.environ.get("TEMP", "/tmp"), "fautexprimer_limits")
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


def post(path, body, timeout=300):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, len(body), time.time() - start, None
    except urllib.error.HTTPError as e:
        return e.code, 0, time.time() - start, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return 0, 0, time.time() - start, str(e)[:200]


paragraphe = (
    "La synthÃ¨se vocale de longue haleine met au dÃ©fi les plafonds appliquÃ©s par l'application. "
    "Chaque paragraphe ajoute des caractÃ¨res, des mots et des phrases au compteur du studio. "
    "Le moteur doit rester stable du premier mot au dernier point, sans coupure ni timeout. "
)

gemini_text = paragraphe * 28    # 7 448 caractères / 1 316 mots — sous les deux plafonds Gemini
edge_text = paragraphe * 74     # juste sous le plafond Edge (20000)

print("=== PLAFONDS : limite acceptÃ©e, dÃ©passement rejetÃ© ===")

# 1. DÃ©passement volontaire -> doit Ãªtre bloquÃ© par le serveur (400)
over_gemini = paragraphe * 31
status, size, dur, err = post("/api/synthesize", {
    "text": over_gemini, "provider": "gemini", "formats": [".wav"]})
print(f"[Gemini {len(over_gemini)} car.] => HTTP {status} (attendu 400) {err or ''}")

over_edge = paragraphe * 75
status, size, dur, err = post("/api/synthesize", {
    "text": over_edge, "provider": "edge", "voice": "fr-FR-DeniseNeural", "formats": [".wav"]})
print(f"[Edge   {len(over_edge)} car.] => HTTP {status} (attendu 400) {err or ''}")

# 2. Textes longs mais dans les plafonds -> synthÃ¨se rÃ©elle
status, size, dur, err = post("/api/synthesize", {
    "text": gemini_text, "provider": "gemini", "formats": [".wav"]}, timeout=600)
print(f"[Gemini {len(gemini_text)} car.] => HTTP {status}, audio {size} octets, {dur:.1f}s {err or ''}")

status, size, dur, err = post("/api/synthesize", {
    "text": edge_text, "provider": "edge", "voice": "fr-FR-DeniseNeural", "formats": [".wav"]}, timeout=600)
print(f"[Edge   {len(edge_text)} car.] => HTTP {status}, audio {size} octets, {dur:.1f}s {err or ''}")

server.should_exit = True
time.sleep(1)
print("Termine.")

