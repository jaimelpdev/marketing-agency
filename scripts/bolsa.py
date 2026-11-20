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
        seq = 1 + max([int(x["id"].split("-")[4]) for x in bolsa if x["id"].startswith(f"b-{today}-")] or [0])
        bid = f"b-{today}-{seq:02d}-{p['tipo']}"
        caption = (str(p.get("caption", "")) + "\n\n" + str(p.get("hashtags", ""))).strip() if p["tipo"] != "historia" else ""
        bolsa.append({
            "id": bid, "tipo": p["tipo"], "tema": p.get("tema", ""), "gancho": p.get("gancho", ""),
            "caption": caption[:2200], "nota": p.get("nota") or 6, "created": today, "status": "pendiente",
            "veo": {"clips": clips,
                    "voz": daily.VOICES.get(p.get("voz") if p.get("voz") in ("mujer", "hombre") else "mujer"),
                    "ritmo": daily.RATES.get(p.get("ritmo_voz") or "normal", "+8%"),
                    "musica": p.get("musica") or "", "hashtags": p.get("hashtags", "")},
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
        folder = daily.DOCS / "media" / uid / "bolsa" / bid
        if folder.exists():
            shutil.rmtree(folder)
        total, meta = veo.compose(segs, v.get("voz") or daily.VOICES["hombre"], v.get("ritmo") or "+8%", v.get("musica") or "energetica",
                                  folder / "video.mp4", log, music_free_only=b["tipo"] == "historia")
    import music_lib
    credit = music_lib.credit((meta or {}).get("track") if isinstance(meta, dict) else None) if b["tipo"] != "historia" else ""
    b["media"] = [str((folder / "video.mp4").relative_to(daily.DOCS))]
    if (folder / "cover.jpg").exists():
        b["cover"] = str((folder / "cover.jpg").relative_to(daily.DOCS))
    if credit and b.get("caption"):
        b["caption"] = (b["caption"] + "\n\n" + credit)[:2200]
    b["status"] = "lista"
    b["listo"] = datetime.now().isoformat(timespec="minutes")
    save(uid, bolsa)
    log(f"    ✓ Listo para publicar: {total:.1f}s · música: {(meta or {}).get('title', 'ninguna')}")
    return True


def fill(user, queue, tz, log=print):
    """Rellena los huecos de vídeo del calendario con los mejores vídeos ya montados de la bolsa.
    Los huecos cuya hora ya ha pasado sin vídeo se marcan como no publicados. Devuelve True si cambió algo."""
    import daily
    uid = user["id"]
    bolsa = load(uid)
    now = datetime.now(tz)
    changed = False
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
