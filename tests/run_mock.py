"""
Prueba completa sin internet: simula Groq, Pollinations e Instagram.
Uso:  python tests/run_mock.py   (trabaja sobre una copia temporal; simula también el servidor de la agencia)
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
        PROMPTS.append(request.post_data or "")
        route.fulfill(status=200, headers={**daily.CORS, "content-type": "application/json"},
                      body=json.dumps({"choices": [{"message": {"content": json.dumps(PLAN)}}]}))

    def poll(route, request):
        route.fulfill(status=200, headers={**daily.CORS, "content-type": "image/jpeg"}, body=FAKE_IMG)
    page.route("https://api.groq.com/**", groq)
    page.route("https://image.pollinations.ai/**", poll)
    page.route("https://fonts.googleapis.com/**", lambda r, q: r.fulfill(status=200, body="", headers={"content-type": "text/css"}))


import subprocess  # noqa: E402
calls = []
STRAT = []
OV = []
PROMPTS = []
DONE = []
zcalls = []
uploads = []
STATUS = {}
USERS = [
    {"id": "u_ana", "name": "Ana", "ig": {"connected": False}, "zernioProfileId": "prof_ana",
     "social": [{"id": "acc_ig", "platform": "instagram", "username": "ana_ig"}, {"id": "acc_tt", "platform": "tiktok", "username": "ana_tt"}, {"id": "acc_yt", "platform": "youtube", "username": "ana_yt"}],
     "config": {"marca": {"name": "Academia Ana", "about": "Clases de simracing", "ig": "@ana_academia", "acc": "#ff3b30", "bg": "#0b0d12", "style": "racing"},
                "visual": {"bgMode": "ai", "imgPer": "scene", "agentVisuals": True},
                "plan": {"modo": "smart", "auto": True, "max_por_dia": 2, "max_semana": 5, "formatos": ["reel", "historia", "post", "carrusel"], "zona_horaria": "Europe/Madrid"}}},
    {"id": "u_luis", "name": "Luis", "ig": {"connected": False}, "social": [], "config": {"marca": {"name": "Luis"}}},
]


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(work / "docs"), **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def _auth(self):
        return self.headers.get("authorization") == "Bearer admin-test"

    def do_GET(self):
        if self.path.startswith("/ov/audio/"):
            OV.append(self.path)
            return self._json({"results": [{"id": f"trk{len(OV)}", "title": "Sunny Ride", "creator": "Band X", "license": "cc0" if "license=cc0&" in self.path + "&" else "by", "license_version": "4.0",
                                            "url": "http://127.0.0.1:8765/ov/track.mp3", "duration": 95000, "filetype": "mp3", "source": "jamendo",
                                            "foreign_landing_url": "https://jamendo.com/x"}]})
        if self.path == "/ov/track.mp3":
            data = TRACK.read_bytes()
            self.send_response(200); self.send_header("content-type", "audio/mpeg"); self.send_header("content-length", str(len(data))); self.end_headers()
            return self.wfile.write(data)
        if self.path.startswith("/admin/"):
            if not self._auth():
                return self._json({"error": {"message": "no"}}, 401)
            if self.path == "/admin/users":
                return self._json({"users": USERS})
        return super().do_GET()

    def do_PUT(self):
        n = int(self.headers.get("content-length", 0))
        if self.path.startswith("/upload/"):
            uploads.append((self.path, n, self.headers.get("content-type")))
            self.rfile.read(n)
            return self._json({})
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/admin/status/") and self._auth():
            STATUS[self.path.rsplit("/", 1)[1]] = body
            return self._json({"ok": True})
        self._json({"error": {"message": "?"}}, 404)

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/groq/chat/completions":
            STRAT.append(body["messages"][0]["content"])
            mem = {"fase": "descubrimiento", "objetivo_mes": "Crecer hasta 1.000 seguidores", "arco_semana": {"tema": "La frenada perfecta", "porque": "es lo que más dudas genera"},
                   "mezcla": {"alcance": 55, "confianza": 30, "comunidad": 15, "conversion": 0},
                   "series": [{"nombre": "El error de la semana", "formato": "reel", "cuando": "miércoles", "descripcion": "un error típico y cómo evitarlo", "episodios": 0}],
                   "aprendizajes": ["Los reels de errores tienen más comentarios"], "que_evitar": ["Posts solo de app"], "promesas_pendientes": ["Enviar la guía de frenada"],
                   "ideas": ["Reto de la semana"], "tono_comunidad": "cercano y gamer", "hitos": ["1.000 seguidores"], "resumen_para_dueño": "Esta semana vamos a por alcance."}
            return self._json({"choices": [{"message": {"content": json.dumps(mem, ensure_ascii=False)}}]})
        if self.path.startswith("/admin/requests/") and self._auth():
            DONE.append((self.path, body))
            for u in USERS:
                if f"/{u['id']}/" in self.path:
                    u["requests"] = None
            return self._json({"ok": True})
        if self.path == "/admin/zernio" and self._auth():
            zcalls.append(body)
            if body.get("method") == "GET" and body["path"].endswith("/posts"):
                return self._json({"status": "ok", "posts": [{"id": "x1", "platform": "instagram", "message": "Cómo elegir tu primer volante de simracing #simracing", "mediaType": "VIDEO", "createdTime": "2026-11-01T18:00:00Z", "likeCount": 87, "commentCount": 9}]})
            if body["path"] == "/media/presign":
                n = len(zcalls)
                return self._json({"uploadUrl": f"http://127.0.0.1:8765/upload/{n}", "publicUrl": f"https://cdn.zernio.test/{n}/{body['body']['filename']}"})
            if body["path"] == "/posts":
                return self._json({"post": {"_id": f"post_{len(zcalls)}"}})
        if self.path == "/admin/ig" and self._auth():
            calls.append((body["userId"], body["action"], json.dumps(body.get("params", body.get("id")))[:110]))
            if body["action"] == "status":
                return self._json({"status_code": "FINISHED"})
            return self._json({"id": f"c{len(calls)}"})
        self._json({"error": {"message": "?"}}, 404)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update({"AGENCIA_API": "http://127.0.0.1:8765", "AGENCIA_ADMIN_TOKEN": "admin-test", "PAGES_URL": "http://127.0.0.1:8765"})
import daily  # noqa: E402
daily.install_routes = mock_routes
# voz simulada: un tono cuya duración depende del número de palabras
def fake_tts(text, voice, rate, out, *a, **k):
    d = max(1.0, len(text.split()) * 0.33)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={d}", "-ar", "24000", str(out)], check=True)
daily.tts = fake_tts
# sin pistas propias: la música se busca en Openverse (simulado)
TRACK = work / "track.mp3"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=90", "-ac", "2", str(TRACK)], check=True)
os.environ["OPENVERSE_API"] = "http://127.0.0.1:8765/ov"
os.environ["GROQ_API_BASE"] = "http://127.0.0.1:8765/groq"
# acelerar Pollinations en la prueba
os.environ["POLLINATIONS_TOKEN"] = "x"
daily.main()

qpath = work / "content/users/u_ana/queue.json"
queue = json.loads(qpath.read_text())
assert not (work / "content/users/u_luis").exists(), "Luis no tiene Instagram: no se planifica"
print("\nCOLA DE ANA:")
for q in queue:
    print(" ", q["publishAt"], q["tipo"], q["media"])
    for m in q["media"]:
        assert (work / "docs" / m).exists(), m
    if q["media"][0].endswith(".mp4"):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type:format=duration", "-of", "compact", str(work / "docs" / q["media"][0])], capture_output=True, text=True)
        print("    ", r.stdout.strip().replace("\n", " | "))
assert "u_ana" in STATUS and STATUS["u_ana"]["queue"], "la app recibe el estado"

posts = [c["body"] for c in zcalls if c["path"] == "/posts"]
print("\nPUBLICACIONES PROGRAMADAS EN ZERNIO:")
for pb in posts:
    print("  ", pb["scheduledFor"], [p["platform"] + ("(story)" if p.get("platformSpecificData") else "") for p in pb["platforms"]], [m["type"] for m in pb["mediaItems"]], "· yt:", pb.get("title", "-"))
print("  archivos subidos:", len(uploads), [u[2] for u in uploads][:5])
assert posts and all(q["status"] == "scheduled" for q in queue), [q["status"] for q in queue]
reel = next(pb for pb in posts if len(pb["platforms"]) == 3)
assert {p["platform"] for p in reel["platforms"]} == {"instagram", "tiktok", "youtube"} and reel["tiktokSettings"]["media_type"] == "video"
story = next(pb for pb in posts if any(p.get("platformSpecificData") for p in pb["platforms"]))
assert story["content"] == "" and len(story["platforms"]) == 1
import publish  # noqa: E402
publish.main()  # no debe tocar las piezas ya programadas en Zernio
final = json.loads(qpath.read_text())
assert all(q["status"] == "scheduled" for q in final)
def loudness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
    import re
    return float(re.search(r"mean_volume: (-?[0-9.]+)", r.stderr).group(1))
assert any("primer volante de simracing" in pr and "87" in pr for pr in PROMPTS), "la IA ve lo ya publicado en las redes"
assert STRAT and "primer volante de simracing" in STRAT[0] and "87" in STRAT[0], "el estratega ve los resultados reales"
memf = work / "content/users/u_ana/memoria.json"
mem = json.loads(memf.read_text())
assert mem["fase"] == "descubrimiento" and mem["semana"] == 1 and mem["series"][0]["nombre"] == "El error de la semana"
assert any("MEMORIA Y ESTRATEGIA DE MARCA" in pr and "El error de la semana" in pr and "La frenada perfecta" in pr for pr in PROMPTS), "el planificador usa la memoria"
assert STATUS["u_ana"].get("memoria", {}).get("fase") == "descubrimiento", "la memoria llega a la app"
print("🧠 MEMORIA:", mem["fase"], "·", mem["arco_semana"]["tema"], "·", [x["nombre"] for x in mem["series"]])
assert OV, "busca música libre"
assert (work / "content/music").exists() and list((work / "content/music").rglob("index.json")), "guarda las pistas"
reel_item = next(q for q in final if q["tipo"] == "reel")
assert "Sunny Ride" in reel_item["caption"] and "CC BY 4.0" in reel_item["caption"], "acredita al artista"
hist_item = next(q for q in final if q["tipo"] == "historia")
lv = loudness(work / "docs" / hist_item["media"][0])
print("\nHistoria solo música, volumen medio:", lv, "dB")
assert lv > -40, "la historia tiene música generada (no es silencio)"

# — Rehacer una pieza desde la app —
old_reel = next(q for q in final if q["tipo"] == "reel")
USERS[0]["requests"] = {"redo": [old_reel["id"]]}
USERS[0]["config"]["plan"]["audio"] = "voz"
import shutil as _sh
_sh.copy(work / "docs" / hist_item["media"][0], "/tmp/historia_mock.mp4")
zcalls.clear()
daily.main()
q2 = json.loads(qpath.read_text())
new_reel = [q for q in q2 if q["tipo"] == "reel"]
print("\nREHACER:", old_reel["id"], "→", [q["id"] for q in new_reel])
assert old_reel["id"] not in [q["id"] for q in q2] and len(new_reel) == 1 and "-r" in new_reel[0]["id"]
assert new_reel[0]["publishAt"] == old_reel["publishAt"] or new_reel[0]["publishAt"] > old_reel["publishAt"]
assert any(c["method"] == "DELETE" and c["path"] == "/posts/" + old_reel["zernioPostId"] for c in zcalls), "anula el reel antiguo en Zernio"
assert "Sustituye a otra pieza" in PROMPTS[-1] and "3 errores" in PROMPTS[-1]
assert len(q2) == len(final), "las demás piezas siguen igual"
assert DONE and DONE[-1][1].get("redo") == [old_reel["id"]]
r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(work / "docs" / new_reel[0]["media"][0])], capture_output=True, text=True)
assert "audio" in r.stdout

# — Publicación pedida desde el chat —
USERS[0]["requests"] = {"create": [{"id": "cX1", "tipo": "reel", "tema": "Trail braking", "notas": "Tono cercano", "cuando": None}]}
daily.main()
q3 = json.loads(qpath.read_text())
chat = [q for q in q3 if q.get("source") == "chat"]
assert len(chat) == 1 and chat[0]["tipo"] == "reel" and len(q3) == len(q2) + 1, [(q["id"], q.get("source")) for q in q3]
assert "La ha pedido el dueño" in PROMPTS[-1] and "Trail braking" in PROMPTS[-1]
assert DONE[-1][1].get("create") == ["cX1"]
print("CHAT: publicación creada →", chat[0]["id"], chat[0]["publishAt"])
q2 = q3

# — Reiniciar cuenta —
USERS[0]["requests"] = {"reset": "2026-11-09T19:00:00Z"}
USERS[0]["config"] = None
zcalls.clear()
daily.main()
assert not (work / "content/users/u_ana").exists() and not (work / "docs/media/u_ana").exists(), "reinicio borra todo"
assert sum(1 for c in zcalls if c["method"] == "DELETE") == len(q2), "anula todo lo programado"
assert DONE[-1][1].get("reset")
print("REINICIO: cola y archivos borrados, publicaciones anuladas ✓")
srv.shutdown()
print("\nESTADO ENVIADO A LA APP:", [(x["tipo"], x["status"]) for x in STATUS["u_ana"]["queue"]])
print("\n✓ Todo el recorrido funciona. Carpeta de prueba:", work)
