"""
Prueba completa sin internet: simula Groq, Pollinations e Instagram.
Uso:  python tests/run_mock.py   (trabaja sobre una copia temporal del repositorio)
"""
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
work = Path(tempfile.mkdtemp()) / "repo"
shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
sys.path.insert(0, str(work / "scripts"))
os.environ["GROQ_API_KEY"] = "gsk_prueba"
FAKE_IMG = Path(os.environ.get("FAKE_IMG", "/tmp/fake.jpg")).read_bytes()

PLAN = {"estrategia": "Semana con 4 piezas de feed y 2 historias; descansamos el martes y el sábado para no saturar.",
        "piezas": [
            {"dia": 0, "hora": "19:30", "tipo": "reel", "objetivo": "alcance", "razon": "Arrancar fuerte", "tema": "3 errores al frenar", "formato": "error común",
             "gancho": "Frenas *mal* y no lo sabes", "visual": "race car braking at night", "escenas": [{"titulo": "Frenas tarde", "texto": "Frena *antes* y suelta progresivo", "visual": "glowing brake disc"}],
             "cta": "Aprende más en la bio", "sticker": None, "fondo": "ia", "estilo": "racing", "caption": "¿Te pasa?", "hashtags": ["#simracing"],
             "audio": "voz_y_musica", "voz": "hombre", "ritmo_voz": "energico", "musica": "energetica",
             "guion": ["Seguro que frenas mal y no te has dado cuenta.", "El truco es frenar antes y soltar el pedal poco a poco.", "Tienes la guía completa en el enlace de la bio."]},
            {"dia": 0, "hora": "20:00", "tipo": "post", "tema": "Duplicado mismo día (debe filtrarse por las 18 h)", "gancho": "Extra", "escenas": [{"titulo": "", "texto": "x"}], "cta": "x", "fondo": "color", "estilo": "pop", "caption": "x", "hashtags": []},
            {"dia": 2, "hora": "13:30", "tipo": "carrusel", "objetivo": "autoridad", "razon": "Guardable", "tema": "Checklist de setup", "formato": "checklist",
             "gancho": "Tu *setup* en 3 pasos", "visual": "sim racing rig", "escenas": [{"titulo": "Asiento", "texto": "Ajusta la *distancia*", "visual": "seat"}, {"titulo": "FOV", "texto": "Calcula tu *campo de visión*", "visual": "monitor"}],
             "cta": "Guía en la bio", "fondo": "color", "estilo": "minimal", "caption": "Guárdalo", "hashtags": ["#setup"]},
            {"dia": 3, "hora": "09:00", "tipo": "historia", "objetivo": "interacción", "razon": "Encuesta", "tema": "¿Volante o mando?", "formato": "pregunta",
             "gancho": "¿Volante o *mando*?", "visual": "steering wheel", "escenas": [{"titulo": "Sé sincero", "texto": "Te leo"}], "cta": "", "sticker": {"tipo": "encuesta", "texto": "¿Con qué juegas?"},
             "fondo": "animado", "estilo": "pop", "caption": "", "hashtags": [], "audio": "solo_musica", "musica": "divertida"},
        ]}


def mock_routes(page):
    def groq(route, request):
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=daily.CORS)
        route.fulfill(status=200, headers={**daily.CORS, "content-type": "application/json"},
                      body=json.dumps({"choices": [{"message": {"content": json.dumps(PLAN)}}]}))

    def poll(route, request):
        route.fulfill(status=200, headers={**daily.CORS, "content-type": "image/jpeg"}, body=FAKE_IMG)
    page.route("https://api.groq.com/**", groq)
    page.route("https://image.pollinations.ai/**", poll)
    page.route("https://fonts.googleapis.com/**", lambda r, q: r.fulfill(status=200, body="", headers={"content-type": "text/css"}))


import subprocess  # noqa: E402
import daily  # noqa: E402
daily.install_routes = mock_routes
# voz simulada: un tono cuya duración depende del número de palabras
def fake_tts(text, voice, rate, out):
    d = max(1.0, len(text.split()) * 0.33)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={d}", "-ar", "24000", str(out)], check=True)
daily.tts = fake_tts
mdir = work / "assets/music/energetica"; mdir.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=8", str(mdir / "pista.mp3")], check=True)
# acelerar Pollinations en la prueba
os.environ["POLLINATIONS_TOKEN"] = "x"
daily.main()

queue = json.loads((work / "content/queue.json").read_text())
print("\nCOLA:")
for q in queue:
    if q["media"][0].endswith(".mp4"):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type:format=duration", "-of", "compact", str(work / "docs" / q["media"][0])], capture_output=True, text=True)
        print("  ", q["tipo"], r.stdout.strip().replace("\n", " | "))
for q in queue:
    print(" ", q["publishAt"], q["tipo"], q["media"])
    for m in q["media"]:
        assert (work / "docs" / m).exists(), m

# ---- publicar contra una API de Instagram simulada ----
calls = []


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(work / "docs"), **k)

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = self.rfile.read(n).decode()
        calls.append(("POST", self.path, body[:160]))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"id": f"c{len(calls)}"}).encode())

    def do_GET(self):
        if self.path.startswith("/v23.0/"):
            calls.append(("GET", self.path[:60], ""))
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status_code":"FINISHED"}')
        else:
            super().do_GET()


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
tz = ZoneInfo("Europe/Madrid")
for q in queue:  # todo vence ya
    q["publishAt"] = (datetime.now(tz) - timedelta(minutes=5)).isoformat()
(work / "content/queue.json").write_text(json.dumps(queue))
os.environ.update({"IG_USER_ID": "17840000", "IG_ACCESS_TOKEN": "tok", "IG_API_HOST": "http://127.0.0.1:8765", "PAGES_URL": "http://127.0.0.1:8765"})
import publish  # noqa: E402
publish.main()
srv.shutdown()
final = json.loads((work / "content/queue.json").read_text())
print("\nESTADOS:", [(q["tipo"], q["status"], q.get("error", "")) for q in final])
print("LLAMADAS A INSTAGRAM:")
for c in calls:
    print("  ", c)
print("\nCarpeta de prueba:", work)
