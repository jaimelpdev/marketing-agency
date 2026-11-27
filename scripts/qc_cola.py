"""Control de calidad de los vídeos de Veo ya montados (en la cola o listos en la bolsa).

Revisa cada vídeo con la visión de Gemini (un fotograma cada 3 s, comparado con lo que dice la voz). Si tiene un
fallo grave: lo quita de la programación en redes, devuelve el hueco a la bolsa (para que lo ocupe otro vídeo bueno,
si lo hay) y marca ese vídeo como descartado. Uso: python scripts/qc_cola.py [id_usuario]"""
import base64
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import bolsa  # noqa: E402
import brain  # noqa: E402
import daily  # noqa: E402
from common import api  # noqa: E402


def frames(path, every=3.0, limit=12):
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True).stdout.strip() or 0)
    out, t = [], 1.0
    while t < d and len(out) < limit:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=300:-2", "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True)
        if r.stdout:
            out.append("data:image/jpeg;base64," + base64.b64encode(r.stdout).decode())
        t += every
    return out


def review_video(path, texto, persona, log=print):
    fs = frames(path)
    fallos = []
    for i in range(0, len(fs), 6):
        ok, f = brain.vision_check(fs[i:i + 6], (
            f"Son fotogramas (uno cada 3 segundos, del {i + 1} al {i + len(fs[i:i + 6])}) de un reel vertical de Instagram "
            "hecho con IA. Los subtítulos quemados en la imagen son correctos: no los cuentes como texto inventado.\n"
            f"El guion completo que dice la voz: «{texto[:900]}»\n"
            + (f"El presentador debe ser siempre: {persona}.\n" if persona else "") + "\n" + brain.FALLOS_GRAVES), log)
        if not ok:
            fallos += f
    return fallos


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    users = api("GET", "/admin/users").get("users", [])
    for user in users:
        if only and user["id"] != only:
            continue
        uid = user["id"]
        udir = daily.CONTENT / "users" / uid
        if not (udir / "bolsa.json").exists():
            continue
        aj = daily.load_json(udir / "ajustes.json", {})
        persona = aj.get("persona_veo", "")
        tz = ZoneInfo(((user.get("config") or {}).get("plan") or {}).get("zona_horaria", "Europe/Madrid"))
        now = datetime.now(tz)
        queue = daily.load_json(udir / "queue.json", [])
        bol = bolsa.load(uid)
        byid = {b["id"]: b for b in bol}
        print(f"▶ {user.get('name')}: revisando vídeos de Veo")
        # 1) los ya puestos en el calendario
        for it in queue:
            if not it.get("bolsa") or it.get("revisada") or it.get("status") not in ("pending", "scheduled", "prueba") or datetime.fromisoformat(it["publishAt"]) <= now:
                continue
            b = byid.get(it["bolsa"]) or {}
            texto = " ".join(c.get("t", "") for c in (b.get("veo") or {}).get("clips", []))
            path = daily.DOCS / (it.get("media") or [""])[0]
            if not path.exists():
                continue
            fallos = review_video(path, texto, persona)
            it["revisada"] = True
            if not fallos:
                print(f"  ✓ {it['publishAt'][:16]} «{it.get('tema', '')}»: sin fallos graves")
                continue
            print(f"  ✗ {it['publishAt'][:16]} «{it.get('tema', '')}»: {'; '.join(fallos)[:300]}")
            daily.zernio_cancel(it)
            it.update({"status": "hueco", "tema": "", "caption": "", "media": [], "attempts": 0})
            for k in ("zernioPostId", "bolsa", "cover", "error", "revisada"):
                it.pop(k, None)
            if b:
                b["status"], b["motivo"] = "descartada", "; ".join(fallos)[:300]
        # 2) los listos en la bolsa, antes de que ocupen un hueco
        for b in bol:
            if b.get("status") != "lista" or b.get("revisada"):
                continue
            path = daily.DOCS / (b.get("media") or [""])[0]
            if not path.exists():
                continue
            texto = " ".join(c.get("t", "") for c in b["veo"]["clips"])
            fallos = review_video(path, texto, persona)
            b["revisada"] = True
            if fallos:
                b["status"], b["motivo"] = "descartada", "; ".join(fallos)[:300]
                print(f"  ✗ listo «{b.get('tema', '')}» descartado: {b['motivo'][:200]}")
            else:
                print(f"  ✓ listo «{b.get('tema', '')}»: sin fallos graves")
        bolsa.save(uid, bol)
        daily.save_json(udir / "queue.json", queue)
        daily.fill_bolsa(user, queue)
        print(f"  Revisión visual: {brain.VISION['n']} consultas, {brain.VISION['rechazos']} con fallos")


if __name__ == "__main__":
    main()
