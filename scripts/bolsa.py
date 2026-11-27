"""Bolsa de vídeos (modo «Veo obligatorio»).

La estrategia ya no ata cada vídeo a un día: escribe guiones de reels e historias y los mete en la bolsa.
Cada día se generan en Google Flow los clips que dé el cupo gratis (acumulando para los días siguientes).
Cuando un guion tiene todos sus clips, la agencia lo monta. En cada hueco de vídeo del calendario se publica
el mejor vídeo ya montado de ese tipo; si no hay ninguno listo, ese hueco no se publica."""
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

MAX_EDAD_DIAS = 21  # los clips se guardan 30 días en el servidor
MAX_PEND = 15


def _paths(uid):
    import daily
    udir = daily.CONTENT / "users" / uid
    return udir, udir / "bolsa.json"


def load(uid):
    import daily
    return daily.load_json(_paths(uid)[1], [])


def save(uid, bolsa):
    import daily
    daily.save_json(_paths(uid)[1], bolsa)


def add(user, pieces, today, log=print):
    """Mete en la bolsa los guiones de vídeo del plan (con su lista de clips de Veo)."""
    import daily
    import veo
    uid = user["id"]
    bolsa = load(uid)
    n = 0
    for k, p in enumerate(pieces):
        clips = veo.plan_clips(p)
        if not clips:
            continue
        _, captura = veo.split_producto(p)
        seq = 1 + max([int(x["id"].split("-")[4]) for x in bolsa if x["id"].startswith(f"b-{today}-")] or [0])
        bid = f"b-{today}-{seq:02d}-{p['tipo']}"
        caption = (str(p.get("caption", "")) + "\n\n" + str(p.get("hashtags", ""))).strip() if p["tipo"] != "historia" else ""
        bolsa.append({
            "id": bid, "tipo": p["tipo"], "tema": p.get("tema", ""), "gancho": p.get("gancho", ""),
            "caption": caption[:2200], "nota": p.get("nota") or 6, "created": today, "status": "pendiente", "dir": DIRECCION,
            "veo": {"clips": clips,
                    "voz": daily.VOICES.get(p.get("voz") if p.get("voz") in ("mujer", "hombre") else "mujer"),
                    "ritmo": daily.RATES.get(p.get("ritmo_voz") or "normal", "+8%"),
                    "musica": p.get("musica") or "", "hashtags": p.get("hashtags", ""),
                    **({"captura": captura} if captura else {})},
        })
        n += 1
        log(f"    🎥 A la bolsa: {p['tipo']} «{p.get('tema', '')}» ({len(clips)} clips, nota {p.get('nota') or '-'})")
    # como mucho MAX_PEND guiones esperando clips: se quedan los de mejor nota (y, a igualdad, los más nuevos)
    pend = sorted([b for b in bolsa if b.get("status") == "pendiente"], key=lambda b: b["id"], reverse=True)
    pend.sort(key=lambda b: -float(b.get("nota") or 0))
    drop = {b["id"] for b in pend[MAX_PEND:]}
    bolsa = [b for b in bolsa if b["id"] not in drop]
    save(uid, bolsa)
    return n


DIRECCION = 2  # versión de la dirección de arte de los clips (planos según lo que dice cada frase)


def redirect(user, key, log=print):
    """Rehace los prompts de los guiones de la bolsa que aún no tienen ningún clip, con la dirección de arte actual."""
    import re
    import brain
    import veo
    if "veo" not in user:  # el servidor aún no dice qué clips hay subidos: mejor no tocar nada
        return 0
    uid = user["id"]
    bolsa = load(uid)
    have = user.get("veo") or {}
    n = 0
    for b in bolsa:
        if b.get("status") != "pendiente" or b.get("dir", 1) >= DIRECCION or have.get(b["id"]):
            continue
        frases = [f.strip() for c in b["veo"]["clips"] for f in re.split(r"(?<=[.!?])\s+", c["t"]) if f.strip()]
        piece = {"tipo": b["tipo"], "tema": b.get("tema", ""), "beats": [{"t": f} for f in frases]}
        try:
            if brain.art_direct(piece, user, key, log):
                clips = veo.plan_clips(piece)
                if clips:
                    b["veo"]["clips"] = clips
                    b["dir"] = DIRECCION
                    n += 1
                    log(f"    🎯 Planos rehechos para «{b.get('tema', '')}» ({len(clips)} clips)")
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ No se pudieron rehacer los planos de «{b.get('tema', '')}»: {e}")
    if n:
        save(uid, bolsa)
    return n


def prune(bolsa, today):
    lim = (datetime.fromisoformat(today) - timedelta(days=MAX_EDAD_DIAS)).date().isoformat()
    return [b for b in bolsa if b.get("status") == "lista" or b.get("created", today) >= lim]


def summary(uid, have=None):
    """Lo que ve la app y la tarea de Veo: guiones pendientes de clips (ordenados por prioridad) y vídeos listos."""
    bolsa = load(uid)
    pend = [b for b in bolsa if b.get("status") == "pendiente"]
    lista = [b for b in bolsa if b.get("status") == "lista"]
    # prioridad: primero el tipo del que hay menos vídeos listos, luego la mejor nota y luego el más antiguo
    stock = {t: sum(1 for b in lista if b["tipo"] == t) for t in ("reel", "historia")}
    pend.sort(key=lambda b: (stock.get(b["tipo"], 0), -float(b.get("nota") or 0), b.get("created", "")))
    out = [{"id": b["id"], "tipo": b["tipo"], "tema": b.get("tema", ""), "nota": b.get("nota"), "status": "pendiente",
            **({"rehacer": b["rehacer"]} if b.get("rehacer") else {}),
            "veo": {"clips": [{"t": c.get("t", "")[:300], "prompt": c.get("prompt", "")[:1400], "dur": c.get("dur", 8)} for c in b["veo"]["clips"]]}}
           for b in pend[:20]]
    out += [{"id": b["id"], "tipo": b["tipo"], "tema": b.get("tema", ""), "nota": b.get("nota"), "status": "lista",
             **({"cover": b["cover"]} if b.get("cover") else {})} for b in lista[:20]]
    return out


def assemble(user, bid, log=print):
    """Monta un guion de la bolsa con sus clips (ya subidos al servidor) y lo deja listo para un hueco."""
    import daily
    import veo
    uid = user["id"]
    bolsa = load(uid)
    b = next((x for x in bolsa if x["id"] == bid), None)
    if not b or b.get("status") not in ("pendiente", "lista"):
        log(f"  — {bid}: ya no está en la bolsa.")
        return False
    v = b["veo"]
    log(f"🎬 Montando de la bolsa «{b.get('tema', '')}» con {len(v['clips'])} clips de Veo")
    with tempfile.TemporaryDirectory() as td:
        segs = []
        for k, c in enumerate(v["clips"]):
            path = Path(td) / f"c{k}.mp4"
            daily.fetch_clip(uid, bid, k, path)
            segs.append({"clip": path, "t": c["t"]})
        malos = review_clips(user, b, segs, log)
        if malos:
            reject_clips(user, bolsa, b, malos, log)
            return False
        if v.get("captura"):  # el final del vídeo de producto: captura real de la app
            try:
                segs.append({"clip": veo.image_clip(v["captura"]["url"], Path(td) / "captura.mp4"), "t": v["captura"]["t"]})
                log("    📸 Final con captura real de la app")
            except Exception as e:  # noqa: BLE001
                log(f"    ⚠ No se pudo usar la captura ({e}); el vídeo acaba sin ella")
        folder = daily.DOCS / "media" / uid / "bolsa" / bid
        if folder.exists():
            shutil.rmtree(folder)
        total, meta = veo.compose(segs, v.get("voz") or daily.VOICES["hombre"], v.get("ritmo") or "+8%", v.get("musica") or "energetica",
                                  folder / "video.mp4", log, music_free_only=b["tipo"] == "historia", hook=b.get("gancho", ""))
    import music_lib
    credit = music_lib.credit((meta or {}).get("track") if isinstance(meta, dict) else None) if b["tipo"] != "historia" else ""
    b["media"] = [str((folder / "video.mp4").relative_to(daily.DOCS))]
    if (folder / "cover.jpg").exists():
        b["cover"] = str((folder / "cover.jpg").relative_to(daily.DOCS))
    if credit and b.get("caption"):
        b["caption"] = (b["caption"] + "\n\n" + credit)[:2200]
    b["status"] = "lista"
    b.pop("rehacer", None)
    b["listo"] = datetime.now().isoformat(timespec="minutes")
    save(uid, bolsa)
    log(f"    ✓ Listo para publicar: {total:.1f}s · música: {(meta or {}).get('title', 'ninguna')}")
    return True


HORA = {"reel": "19:30", "historia": "09:00"}


def add_slots(queue, bolsa, now, tz, log=print, days=3):
    """Si hay vídeos montados esperando y en los próximos días no hay hueco de ese tipo, crea uno (un reel y una
    historia al día como mucho, a la hora habitual de la cuenta): así lo que genera Veo cada día no se queda parado."""
    from collections import Counter
    listos = Counter(b["tipo"] for b in bolsa if b.get("status") == "lista")
    huecos = Counter(q["tipo"] for q in queue if q.get("status") == "hueco")
    added = False
    for tipo in ("reel", "historia"):
        libres = listos.get(tipo, 0) - huecos.get(tipo, 0)
        if libres <= 0:
            continue
        horas = Counter(datetime.fromisoformat(q["publishAt"]).astimezone(tz).strftime("%H:%M") for q in queue if q.get("tipo") == tipo and q.get("publishAt"))
        hora = horas.most_common(1)[0][0] if horas else HORA[tipo]
        for d in range(1, days + 1):
            if libres <= 0:
                break
            day = (now + timedelta(days=d)).date()
            mismos = ("reel", "post", "carrusel") if tipo == "reel" else ("historia",)  # un solo contenido de feed al día
            if any(q.get("tipo") in mismos and q.get("status") not in ("removed", "cancelled", "failed", "sin_veo")
                   and datetime.fromisoformat(q["publishAt"]).astimezone(tz).date() == day for q in queue):
                continue
            hh, mm = map(int, hora.split(":"))
            when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            queue.append({"id": f"{day.isoformat()}-auto-{tipo}", "date": now.date().isoformat(), "source": "auto", "tipo": tipo, "tema": "",
                          "publishAt": when.isoformat(), "caption": "", "media": [], "status": "hueco", "attempts": 0})
            libres -= 1
            added = True
            log(f"  ➕ Hueco nuevo de {tipo} el {when.strftime('%d/%m %H:%M')} para un vídeo de Veo ya montado")
    return added


MAX_REPETIR = 2  # cada clip se puede rechazar como mucho 2 veces; a la tercera se acepta (para no atascar la bolsa)


def _frames(path, n=3):
    import base64
    import subprocess
    try:
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                 capture_output=True, text=True).stdout.strip() or 8)
    except ValueError:
        d = 8.0
    out = []
    for f in [0.15, 0.5, 0.85][:n]:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{d * f:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=360:-2", "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True)
        if r.stdout:
            out.append("data:image/jpeg;base64," + base64.b64encode(r.stdout).decode())
    return out


def review_clips(user, b, segs, log=print):
    """Revisa con visión cada clip de Veo antes de montarlo. Devuelve los índices con un fallo grave."""
    import brain
    persona = str(user.get("persona_veo") or "")
    intentos = b.setdefault("intentos", {})
    malos = []
    for k, s in enumerate(segs):
        if int(intentos.get(str(k), 0)) >= MAX_REPETIR:
            continue
        frames = _frames(s["clip"])
        pedido = b["veo"]["clips"][k].get("prompt", "")[:900]
        ok, fallos = brain.vision_check(frames, (
            "Son 3 fotogramas (principio, mitad y final) de un clip de vídeo vertical para un reel de Instagram.\n"
            f"Mientras se ve, la voz dice: «{s['t'][:400]}»\nSe le pidió al generador de vídeo: «{pedido}»\n"
            + (f"La persona debe ser siempre: {persona}.\n" if persona else "") + "\n" + brain.FALLOS_GRAVES), log)
        if not ok:
            malos.append(k)
            log(f"    🔍 Clip {k + 1} rechazado: {'; '.join(fallos)[:160]}")
    return malos


def reject_clips(user, bolsa, b, malos, log=print):
    """Borra del servidor los clips con fallos para que Flow los vuelva a generar, y deja el guion pendiente."""
    import daily
    intentos = b.setdefault("intentos", {})
    for k in malos:
        intentos[str(k)] = int(intentos.get(str(k), 0)) + 1
        try:
            daily.api("DELETE", f"/admin/clips/{user['id']}/{b['id']}/{k}")
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ No se pudo borrar el clip {k + 1} del servidor ({str(e)[:80]}); lo hará la tarea de Flow")
    b["rehacer"] = sorted(set(b.get("rehacer", [])) | set(malos))
    b["status"] = "pendiente"
    save(user["id"], bolsa)
    log(f"    ↺ «{b.get('tema', '')}»: {len(malos)} clip(s) a repetir en Flow; se montará cuando estén")


def fill(user, queue, tz, log=print):
    """Rellena los huecos de vídeo del calendario con los mejores vídeos ya montados de la bolsa.
    Los huecos cuya hora ya ha pasado sin vídeo se marcan como no publicados. Devuelve True si cambió algo."""
    import daily
    uid = user["id"]
    bolsa = load(uid)
    now = datetime.now(tz)
    changed = add_slots(queue, bolsa, now, tz, log)
    for h in sorted([q for q in queue if q.get("status") == "hueco"], key=lambda q: q["publishAt"]):
        when = datetime.fromisoformat(h["publishAt"])
        if when < now + timedelta(minutes=15):
            h["status"] = "sin_veo"
            h["error"] = "No había ningún vídeo de Veo listo para este hueco."
            log(f"  ⏭ Hueco de {h['tipo']} del {when.strftime('%d/%m %H:%M')}: no había vídeo listo, no se publica nada")
            changed = True
            continue
        cands = sorted([b for b in bolsa if b.get("status") == "lista" and b["tipo"] == h["tipo"]],
                       key=lambda b: (-float(b.get("nota") or 0), b.get("listo", "")))
        if not cands:
            continue
        b = cands[0]
        h.update({"tema": b.get("tema", ""), "caption": b.get("caption", ""), "media": b.get("media", []),
                  "status": "pending" if (user.get("social") or (user.get("ig") or {}).get("connected")) else "prueba",
                  "bolsa": b["id"], "attempts": 0})
        if b.get("cover"):
            h["cover"] = b["cover"]
        b["status"] = "usada"
        b["usada"] = h["id"]
        changed = True
        log(f"  📌 Hueco de {h['tipo']} del {when.strftime('%d/%m %H:%M')} ← «{b.get('tema', '')}» (nota {b.get('nota')})")
        if h["status"] == "pending" and user.get("social"):
            try:
                pid_z = daily.zernio_schedule(user, h, {"tema": h.get("tema"), "hashtags": b["veo"].get("hashtags", "")}, tz)
                if pid_z:
                    h["status"], h["zernioPostId"] = "scheduled", pid_z
                    log(f"    📅 Programado en redes: {h['tipo']} · {h['publishAt']}")
            except Exception as e:
                log("    ✗ No se pudo programar en Zernio:", e)
                h["status"], h["error"] = "failed", str(e)[:300]
    if changed:
        save(uid, bolsa)
    return changed
