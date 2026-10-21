import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_server

server = web_server.create_http_server("127.0.0.1", 8765)


def run():
    server.run()


t = threading.Thread(target=run, daemon=True)
t.start()
for _ in range(200):
    if server.started:
        break
    time.sleep(0.05)
print("READY", flush=True)
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.should_exit = True
