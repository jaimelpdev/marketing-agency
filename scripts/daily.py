"""
Agencia de marketing IA · tarea diaria (multiusuario)
------------------------------------------------------
Para cada persona de la agencia (con marca configurada e Instagram conectado):
1. Abre la web de la agencia (docs/index.html) en un Chrome sin ventana.
2. Pide a la IA el plan (qué días publicar, formatos, horas, sonido) respetando sus límites.
3. Genera imágenes, locución y música y renderiza: reels/historias -> MP4, posts/carruseles -> JPG.
4. Guarda los archivos en docs/media/<persona>/ (GitHub Pages les da un enlace público)
   y añade cada pieza a content/users/<persona>/queue.json.

Variables de entorno: GROQ_API_KEY, POLLINATIONS_TOKEN (opcional), AGENCIA_API, AGENCIA_ADMIN_TOKEN,
FORCE=1 (rehacer el plan), ONLY_USER=<id> (solo una persona).
"""
import base64
import json
import time
import re
import requests
import os
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from common import api, summarize, zernio_reconcile
import brain

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CONTENT = ROOT / "content"
MUSIC_DIR = ROOT / "assets" / "music"
FPS = 30
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
}
DEFAULT_SLOTS = ["09:00", "13:30", "19:30", "21:00", "15:00"]


def log(*a):
    print("[agencia]", *a, flush=True)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- red: proxy con CORS para Groq y Pollinations ----------
def cors_proxy(route, request):
    if request.method == "OPTIONS":
        route.fulfill(status=204, headers=CORS)
        return
    try:
        resp = route.fetch(timeout=180_000)
    except Exception as e:  # sin conexión
        log("Error de red:", request.url[:80], e)
        route.abort()
        return
    headers = dict(resp.headers)
    headers.update(CORS)
    route.fulfill(response=resp, headers=headers)


def install_routes(page):
    page.route("https://api.groq.com/**", cors_proxy)
    page.route("https://image.pollinations.ai/**", cors_proxy)
    page.route("https://api.pexels.com/**", cors_proxy)
    page.route("https://images.pexels.com/**", cors_proxy)


# ---------- utilidades ----------
def publish_time(day_str, dia, hora, tz, idx):
    base = datetime.fromisoformat(day_str).replace(tzinfo=tz) + timedelta(days=int(dia or 0))
    h = (hora or "").strip()
    if not h or ":" not in h:
        h = DEFAULT_SLOTS[idx % len(DEFAULT_SLOTS)]
    hh, mm = h.split(":")[:2]
    try:
        return base.replace(hour=int(hh) % 24, minute=int(mm[:2]) % 60, second=0, microsecond=0)
    except ValueError:
        return base.replace(hour=19, minute=30)


def write_dataurls(urls, folder, start):
    for k, u in enumerate(urls):
        data = base64.b64decode(u.split(",", 1)[1])
        (folder / f"f_{start + k:05d}.jpg").write_bytes(data)


VOICES = {"mujer": "es-ES-ElviraNeural", "hombre": "es-ES-AlvaroNeural"}
# Catálogo de voces que se puede elegir en la app (si una falla, se usa la de respaldo de su género)
VOICE_CATALOG = {
    "alvaro": ("es-ES-AlvaroNeural", "hombre"), "elvira": ("es-ES-ElviraNeural", "mujer"), "ximena": ("es-ES-XimenaNeural", "mujer"),
    "jorge": ("es-MX-JorgeNeural", "hombre"), "dalia": ("es-MX-DaliaNeural", "mujer"),
    "andrew": ("en-US-AndrewMultilingualNeural", "hombre"), "ava": ("en-US-AvaMultilingualNeural", "mujer"),
    "brian": ("en-US-BrianMultilingualNeural", "hombre"), "emma": ("en-US-EmmaMultilingualNeural", "mujer"),
}
RATES = {"tranquilo": "-4%", "normal": "+8%", "energico": "+16%"}
PITCH = {"tranquilo": "-2Hz", "normal": "+0Hz", "energico": "+3Hz"}
# Cadena de "micro de creador": limpia graves, comprime, da presencia y brillo
VOICE_FX = ("highpass=f=85,equalizer=f=220:t=q:w=1:g=-2,equalizer=f=3200:t=q:w=1.3:g=3.5,equalizer=f=9000:t=q:w=1.5:g=2,"
            "acompressor=threshold=-20dB:ratio=3.5:attack=4:release=90:makeup=3,alimiter=limit=0.92")
MUSIC_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg")


def tts(text, voice, rate, out_path, pitch="+0Hz", fallback=None):
    """Locución gratuita con las voces neuronales de Microsoft (edge-tts).
    Devuelve los tiempos de cada palabra [(inicio_s, duración_s, palabra)] para los subtítulos dinámicos."""
    # Playwright (modo síncrono) ya tiene un bucle asyncio en este hilo: la voz se genera en otro hilo.
    import asyncio
    import threading
    import edge_tts
    err, words = [], []
    use_voice = [voice]

    async def go():
        try:
            com = edge_tts.Communicate(text, use_voice[0], rate=rate, pitch=pitch, boundary="WordBoundary")
        except TypeError:  # versiones antiguas: ya dan WordBoundary por defecto
            com = edge_tts.Communicate(text, use_voice[0], rate=rate)
        with open(out_path, "wb") as fh:
            async for ch in com.stream():
                if ch.get("type") == "audio":
                    fh.write(ch["data"])
                elif ch.get("type") == "WordBoundary":
                    words.append((ch["offset"] / 1e7, ch["duration"] / 1e7, str(ch.get("text", ""))))

    def run():
        try:
            asyncio.run(go())
        except Exception as e:  # noqa: BLE001
            err.append(e)
    for intento in range(3):
        if intento == 2 and fallback and fallback != use_voice[0]:
            use_voice[0] = fallback  # la voz elegida no responde: voz de respaldo
        err.clear()
        words.clear()
        t = threading.Thread(target=run)
        t.start()
        t.join(90)
        if not err and Path(out_path).exists() and Path(out_path).stat().st_size > 0:
            return list(words)
    raise RuntimeError(f"edge-tts falló: {err[0] if err else 'sin respuesta'}")


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def pick_music(mood, workdir=None, free_only=False):
    """Música para la pieza: primero tus pistas de assets/music/<ambiente>; si no, música libre real (Openverse)."""
    pools = []
    if mood:
        pools.append([p for p in (MUSIC_DIR / mood).glob("*") if p.suffix.lower() in MUSIC_EXT])
    pools.append([p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in MUSIC_EXT])
    pools.append([p for p in MUSIC_DIR.rglob("*") if p.suffix.lower() in MUSIC_EXT])
    for pool in pools:
        if pool:
            return random.choice(pool), {"license": "own", "title": pool[0].stem}
    import music_lib
    return music_lib.get_track(mood, CONTENT / "music", lambda *a: log(*a), free_only)


def ff_text(t):
    return str(t).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def encode_video(frames_dir, total, out_path, voice_clips=(), music=None, credit="", sfx_track=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.jpg")]
    for clip, _ in voice_clips:
        cmd += ["-i", str(clip)]
    if music:
        mdur = probe_duration(music)
        # empezar ya dentro de la canción (se salta la intro lenta) si es larga
        start = round(min(mdur * 0.18, max(0.0, mdur - total - 2)), 2) if mdur > total + 20 else 0
        cmd += ["-stream_loop", "-1", "-ss", str(start), "-i", str(music)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    m_idx = len(voice_clips) + 1
    has_voice = bool(voice_clips)
    music_vol = 0.3 if has_voice else 1.3  # con voz, la música baja sola cuando se habla (sidechain)
    fade_start = max(0.0, total - 1.2)
    parts, labels = [], []
    for k, (_, start_s) in enumerate(voice_clips):
        ms = int(start_s * 1000)
        parts.append(f"[{k + 1}:a]aresample=48000,aformat=channel_layouts=stereo,{VOICE_FX},adelay={ms}:all=1,volume=1.1[v{k}]")
    if sfx_track:
        cmd += ["-i", str(sfx_track)]
        parts.append(f"[{m_idx + 1}:a]aresample=48000,aformat=channel_layouts=stereo,volume={0.3 if has_voice else 0.4}[fx]")
    norm = "loudnorm=I=-18:TP=-2:LRA=9," if music else ""
    parts.append(f"[{m_idx}:a]aresample=48000,aformat=channel_layouts=stereo,{norm}aresample=48000,volume={music_vol},"
                 f"afade=t=in:d=0.6,afade=t=out:st={fade_start:.2f}:d=1.2[mu]")
    if has_voice:
        n = len(voice_clips)
        vin = "".join(f"[v{k}]" for k in range(n))
        parts.append(f"{vin}amix=inputs={n}:normalize=0:duration=longest[vx]" if n > 1 else "[v0]anull[vx]")
        parts.append("[vx]asplit=2[vox][vsc]")
        # la música se aparta mientras habla la voz y vuelve en las pausas
        parts.append("[mu][vsc]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=350:makeup=1[mud]")
        labels += ["[vox]", "[mud]"]
    else:
        labels.append("[mu]")
    if sfx_track:
        labels.append("[fx]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,"
                 "alimiter=limit=0.84:attack=3:release=60:level=false,aformat=channel_layouts=stereo[aout]")
    vmap = "0:v"
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if credit and Path(font).exists():
        parts.append(f"[0:v]drawtext=fontfile={font}:text='{ff_text(credit.replace("🎵", "♪"))}':fontsize=22:fontcolor=white@0.85:"
                     f"box=1:boxcolor=black@0.35:boxborderw=8:x=(w-text_w)/2:y=h-70[vout]")
        vmap = "[vout]"
    cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)


def clean_speech(t):
    import re
    t = re.sub(r"[*#_]", "", str(t or ""))
    t = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def make_voice(page, i, p, workdir, voices):
    """Genera la locución de cada pantalla y ajusta la duración de las escenas a la voz."""
    texts = page.evaluate("""i=>{const p=S.today.videos[i];const SC=buildScenes(p);
      const esc=p.tipo==='historia'?p.escenas.slice(0,2):p.escenas;let k=0;
      return SC.scenes.map(s=>s.kind==='hook'?p.gancho:s.kind==='tip'?(()=>{const e=esc[k++]||{};return (e.titulo||'')+'. '+(e.texto||'');})():(p.sticker?p.sticker.texto:p.cta));}""", i)
    guion = [g for g in (p.get("guion") or []) if str(g).strip()]
    lines = [clean_speech(guion[k] if k < len(guion) else texts[k]) for k in range(len(texts))]
    gender = p.get("voz") if p.get("voz") in ("mujer", "hombre") else "mujer"
    chosen = VOICE_CATALOG.get(str(voices.get("_elegida") or ""))
    voice = chosen[0] if chosen else voices.get(gender, VOICES["mujer"])
    fallback = VOICES[chosen[1] if chosen else gender]
    rate = RATES.get(p.get("ritmo_voz") or "normal", "+8%")
    pitch = PITCH.get(p.get("ritmo_voz") or "normal", "+0Hz")
    clips, durs, caps = [], [], []
    gem = None
    if os.environ.get("VOICE_ENGINE", "gemini") != "edge":
        try:
            import voz
            gem = voz.lines(lines, gender, p.get("ritmo_voz") or "normal", workdir, lambda *a: log(*a))
            voice = "Gemini " + voz.VOCES.get(gender, "")
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ Voz de Gemini no disponible ({str(e)[:120]}); uso edge-tts")
            gem = None
    for k, line in enumerate(lines):
        if gem is not None:
            if not gem[k]:
                clips.append(None)
                durs.append(0)
                caps.append([])
                continue
            out, wb = gem[k]
            caps.append([{"t": round(a + 0.18, 3), "d": round(b, 3), "w": w} for a, b, w in wb if str(w).strip()])
            clips.append(out)
            durs.append(round(probe_duration(out) + 0.55, 3))
            continue
        if not line:
            clips.append(None)
            durs.append(0)
            caps.append([])
            continue
        out = workdir / f"voz_{k:02d}.mp3"
        wb = tts(line, voice, rate, out, pitch, fallback) or []
        caps.append([{"t": round(a + 0.18, 3), "d": round(b, 3), "w": w} for a, b, w in wb if str(w).strip()])
        d = probe_duration(out)
        clips.append(out)
        durs.append(round(d + 0.55, 3))
    page.evaluate("a=>{S.today.videos[a[0]]._durs=a[1];S.today.videos[a[0]]._caps=a[2];}", [i, durs, caps])
    starts, t = [], 0.0
    sc_durs = page.evaluate("i=>buildScenes(S.today.videos[i]).scenes.map(s=>s.dur)", i)
    for d in sc_durs:
        starts.append(t)
        t += d
    return [(c, starts[k] + 0.18) for k, c in enumerate(clips) if c], voice


RENDER_JS = """
window.__agRender = {
  canvas: document.createElement('canvas'),
  info(i){ const p=S.today.videos[i]; const SC=buildScenes(p); return {total:SC.total, slides:SC.scenes.length, video:TYPES[p.tipo].video}; },
  frames(i, from, n, fps){
    const p=S.today.videos[i]; const SC=buildScenes(p); const prev=ctx; ctx=this.canvas.getContext('2d'); const out=[];
    try{ for(let f=from; f<from+n; f++){ const t=f/fps; if(t>=SC.total) break; drawFrame(SC, Math.min(t, SC.total-0.001)); out.push(this.canvas.toDataURL('image/jpeg',0.9)); } }
    finally{ ctx=prev; }
    return out;
  },
  cover(i){
    // portada: el gancho con todo ya visible (texto y pegatinas), para la miniatura
    const p=S.today.videos[i]; const SC=buildScenes(p); const prev=ctx; ctx=this.canvas.getContext('2d'); STATIC=true;
    try{ drawFrame(SC,0,0); } finally{ STATIC=false; ctx=prev; }
    return this.canvas.toDataURL('image/jpeg',0.92);
  },
  slide(i, k){
    const p=S.today.videos[i]; const SC=buildScenes(p); const prev=ctx; ctx=this.canvas.getContext('2d'); STATIC=true;
    try{ drawFrame(SC,0,k); } finally{ STATIC=false; ctx=prev; }
    return this.canvas.toDataURL('image/jpeg',0.93);
  }
};
"""


COVER_FRAMES = 6

MIME = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def zernio_upload(path):
    """Sube un archivo al almacenamiento de Zernio y devuelve su URL pública."""
    import requests
    ct = MIME.get(Path(path).suffix.lower(), "application/octet-stream")
    pre = api("POST", "/admin/zernio", {"method": "POST", "path": "/media/presign", "body": {"filename": Path(path).name, "contentType": ct}})
    up, pub = pre.get("uploadUrl"), pre.get("publicUrl")
    if not up or not pub:
        raise RuntimeError("Zernio no devolvió la URL de subida")
    data = Path(path).read_bytes()
    for intento in range(4):  # el almacenamiento de Zernio a veces responde 5xx: reintentar
        try:
            r = requests.put(up, data=data, headers={"Content-Type": ct}, timeout=600)
            if r.status_code < 300:
                return pub
            err = f"Subida a Zernio falló ({r.status_code})"
            if r.status_code < 500:
                break
        except requests.RequestException as e:
            err = f"Subida a Zernio falló ({e})"
        time.sleep(15 * (intento + 1))
    raise RuntimeError(err)


def zernio_schedule(user, item, piece, tz):
    """Programa la pieza en Zernio para las redes conectadas. Devuelve el id del post o None."""
    social = user.get("social") or []
    by = {}
    for a in social:
        by.setdefault(a.get("platform"), a)
    tipo = item["tipo"]
    platforms = []
    if "instagram" in by:
        pl = {"platform": "instagram", "accountId": by["instagram"]["id"]}
        if tipo == "historia":
            pl["platformSpecificData"] = {"contentType": "story"}
        platforms.append(pl)
    if tipo == "reel":
        if "tiktok" in by:
            platforms.append({"platform": "tiktok", "accountId": by["tiktok"]["id"]})
        if "youtube" in by:
            platforms.append({"platform": "youtube", "accountId": by["youtube"]["id"]})
    if not platforms:
        return None
    urls = [zernio_upload(DOCS / m) for m in item["media"]]
    when = datetime.fromisoformat(item["publishAt"])
    soon = datetime.now(tz) + timedelta(minutes=10)
    if when < soon:
        when = soon
    floor = publish_floor(user, tz)
    if floor and when < floor:  # última barrera: nunca se programa antes de lo que pidió el dueño
        when = floor
    item["publishAt"] = when.isoformat()
    body = {
        "profileId": user.get("zernioProfileId"),
        "content": "" if tipo == "historia" else item.get("caption", ""),
        "platforms": platforms,
        "mediaItems": [{"type": "video" if u.lower().split("?")[0].endswith(".mp4") else "image", "url": u} for u in urls],
        "scheduledFor": when.isoformat(),
        "timezone": str(tz),
    }
    if any(p["platform"] == "youtube" for p in platforms):
        body["title"] = (piece.get("tema") or piece.get("gancho") or "Nuevo vídeo")[:95]
        body["visibility"] = "public"
        body["tags"] = [h.strip("#") for h in str(piece.get("hashtags", "")).split() if h.startswith("#")][:10]
    if any(p["platform"] == "tiktok" for p in platforms):
        body["tiktokSettings"] = {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True, "allow_duet": True, "allow_stitch": True,
                                  "content_preview_confirmed": True, "express_consent_given": True, "media_type": "video",
                                  "videoCoverTimestampMs": 0}  # el primer fotograma es la portada
    r = api("POST", "/admin/zernio", {"method": "POST", "path": "/posts", "body": body})
    post = r.get("post") or r
    return post.get("_id") or post.get("id") or "ok"


NET_SHORT = {"instagram": "IG", "tiktok": "TikTok", "youtube": "YouTube"}


def _norm_text(t):
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"#\w+|@\w+|https?://\S+", " ", t)
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", t)).strip()


def fetch_published(user):
    """Lo que ya hay publicado en las redes conectadas (también lo subido a mano), para no repetir temas."""
    import re
    out = []
    for acc in user.get("social") or []:
        try:
            r = api("POST", "/admin/zernio", {"method": "GET", "path": f"/accounts/{acc['id']}/posts"})
        except Exception as e:
            log(f"  ⚠ No se pudo leer lo publicado en {acc.get('platform')}: {e}")
            continue
        for p in (r.get("posts") or [])[:25]:
            text = str(p.get("title") or p.get("message") or "").strip()
            text = re.sub(r"\s+", " ", re.sub(r"#\w+", "", text)).strip()
            if not text:
                continue
            mt = str(p.get("mediaType") or "").lower()
            tipo = "reel" if any(k in mt for k in ("video", "reel", "short")) else "carrusel" if "carousel" in mt or "album" in mt else "post"
            likes, comments = p.get("likeCount"), p.get("commentCount")
            eng = f" · ❤️{likes}" if likes is not None else ""
            eng += f" 💬{comments}" if comments else ""
            out.append({"date": str(p.get("createdTime") or "")[:10], "tipo": tipo, "src": "red",
                        "tema": f"[{NET_SHORT.get(acc.get('platform'), acc.get('platform'))}{eng}] {text[:140]}"})
    return out


def merge_history(history, published):
    """Añade lo publicado en redes al historial sin duplicar (la misma pieza en varias redes cuenta una vez)."""
    items = [h for h in history.get("items", []) if h.get("src") != "red"]
    seen = {_norm_text(h.get("tema"))[:60] for h in items}
    for p in sorted(published, key=lambda x: x.get("date") or ""):
        key = _norm_text(p["tema"].split("] ", 1)[-1])[:60]
        if not key or any(key[:40] in k or k[:40] in key for k in seen if k):
            continue
        seen.add(key)
        items.append(p)
    items.sort(key=lambda h: h.get("date") or "")
    return {"items": items[-120:]}


AUDIO_MODES = {"voz": "solo_voz", "musica": "solo_musica", "ambos": "voz_y_musica"}


def zernio_cancel(item):
    """Anula en Zernio una pieza que estaba programada (si lo estaba)."""
    if item.get("zernioPostId") and item.get("status") == "scheduled":
        try:
            api("POST", "/admin/zernio", {"method": "DELETE", "path": "/posts/" + item["zernioPostId"]})
        except Exception as e:
            if "404" not in str(e) and "not found" not in str(e).lower():  # ya anulada desde la app
                log("  ⚠ No se pudo anular en Zernio:", e)


def publish_floor(user, tz):
    """Momento más temprano en que se puede publicar: ajuste de la app, archivo del repo o variable NO_ANTES."""
    cands = []
    plan = (user.get("config") or {}).get("plan") or {}
    extra = load_json(CONTENT / "users" / user["id"] / "ajustes.json", {})
    for v in (plan.get("no_publicar_antes"), extra.get("no_publicar_antes"), os.environ.get("NO_ANTES")):
        if not v:
            continue
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            cands.append(d if d.tzinfo else d.replace(tzinfo=tz))
        except ValueError:
            log(f"  ⚠ Fecha «no publicar antes de» no válida: {v}")
    return max(cands).astimezone(tz) if cands else None


def retry_failed(user):
    """Vuelve a programar en las redes las piezas que fallaron al subir (si aún no ha pasado su hora)."""
    uid = user["id"]
    qpath = CONTENT / "users" / uid / "queue.json"
    queue = load_json(qpath, [])
    plan_conf = ((user.get("config") or {}).get("plan") or {})
    tz = ZoneInfo(plan_conf.get("zona_horaria", "Europe/Madrid"))
    now = datetime.now(tz)
    todo = [q for q in queue if q.get("status") == "failed" and datetime.fromisoformat(q["publishAt"]) > now + timedelta(minutes=12)]
    if not todo or not user.get("social"):
        return
    pieces = {}
    for pf in sorted((CONTENT / "users" / uid / "plans").glob("*.json"))[-8:]:
        for p in load_json(pf, {}).get("piezas", []):
            pieces[(pf.stem, p.get("tema"))] = p
    for it in todo:
        piece = pieces.get((it.get("date"), it.get("tema"))) or {"tema": it.get("tema"), "gancho": it.get("tema")}
        try:
            pid_z = zernio_schedule(user, it, piece, tz)
            if pid_z:
                it["status"], it["zernioPostId"] = "scheduled", pid_z
                it.pop("error", None)
                log(f"  🔁 Reprogramada en redes: {it['tipo']} «{it.get('tema', '')}»")
        except Exception as e:
            log(f"  ⚠ Sigue sin poder programarse «{it.get('tema', '')}»: {e}")
    save_json(qpath, queue)
    try:
        api("PUT", f"/admin/status/{uid}", {"estrategia": "", "queue": summarize(queue)})
    except Exception:
        pass


def think_only(user, key, queue):
    """Sin plan nuevo: el estratega igualmente revisa resultados y actualiza la memoria (una vez por semana)."""
    uid = user["id"]
    mem_path = CONTENT / "users" / uid / "memoria.json"
    mem = brain.load(mem_path)
    if not brain.needs_reflection(mem):
        return
    history = load_json(CONTENT / "users" / uid / "history.json", {"items": []})
    published = fetch_published(user)
    if published:
        history = merge_history(history, published)
    try:
        mem = brain.reflect(mem, user, history, published, key, lambda *a: log(*a))
        brain.save(mem_path, mem)
        log(f"  🧠 Estrategia semana {mem['semana']}: fase {mem['fase']} · arco «{mem['arco_semana'].get('tema', '')}» · series: {', '.join(x['nombre'] for x in mem['series']) or '-'}")
        api("PUT", f"/admin/status/{uid}", {"estrategia": "", "queue": summarize(queue), "memoria": brain.public(mem)})
    except Exception as e:
        log("  ⚠ El estratega no pudo actualizar la memoria:", e)


def plan_user(browser, user, key, force=False, redo=None):
    """Planifica y crea el contenido de una persona. Devuelve True si hizo un plan nuevo.
    force: rehace la semana entera. redo: pieza de la cola que hay que rehacer (solo esa)."""
    uid, conf = user["id"], user.get("config") or {}
    udir = CONTENT / "users" / uid
    queue_path, hist_path = udir / "queue.json", udir / "history.json"
    plan_conf = conf.get("plan", {})
    tz = ZoneInfo(plan_conf.get("zona_horaria", "Europe/Madrid"))
    now = datetime.now(tz)
    today = now.date().isoformat()
    queue = load_json(queue_path, [])
    modo = str(plan_conf.get("modo", "smart"))
    if expire_veo(user, queue, udir, tz):
        try:
            api("PUT", f"/admin/status/{user['id']}", {"queue": summarize(queue)})
        except Exception as e:
            log("  ⚠ No se pudo avisar a la app:", e)
    if (udir / "bolsa.json").exists():
        import bolsa
        _aj = load_json(udir / "ajustes.json", {})
        user["persona_veo"], user["escenario_veo"] = _aj.get("persona_veo", ""), _aj.get("escenario_veo", "")
        if bolsa.redirect(user, key, lambda *a: log(*a)):
            log("  🎯 Bolsa: planos de Veo actualizados")
        fill_bolsa(user, queue)
    if not (force or redo or os.environ.get("FORCE")):
        backfill_veo(user, queue, udir, tz)
        if any(q.get("date") == today and q.get("source") == "auto" for q in queue) or (udir / "plans" / f"{today}.json").exists():
            log("  Ya se planificó hoy.")
            think_only(user, key, queue)
            return False
        future = [q for q in queue if q.get("status") in ("pending", "scheduled", "esperando_veo", "hueco") and datetime.fromisoformat(q["publishAt"]) > now + timedelta(hours=24)]
        if modo == "smart" and future:
            log(f"  Plan en marcha ({len(future)} piezas más allá de mañana). Hoy no hace falta planificar.")
            think_only(user, key, queue)
            return False
    history = load_json(hist_path, {"items": []})
    published = fetch_published(user)
    if published:
        history = merge_history(history, published)
        log(f"  Revisado lo ya publicado en sus redes: {len(published)} publicaciones.")
    # 🧠 memoria a largo plazo + estratega semanal (antes de planificar)
    mem_path = udir / "memoria.json"
    mem = brain.load(mem_path)
    notas = (plan_conf.get("notas") or [])
    if not redo and brain.needs_reflection(mem, force):
        try:
            mem = brain.reflect(mem, user, history, published, key, lambda *a: log(*a))
            brain.save(mem_path, mem)
            log(f"  🧠 Estrategia semana {mem['semana']}: fase {mem['fase']} · arco «{mem['arco_semana'].get('tema', '')}» · series: {', '.join(s['nombre'] for s in mem['series']) or '-'}")
        except Exception as e:
            log("  ⚠ El estratega no pudo actualizar la memoria (se usa la anterior):", e)
    allowed = plan_conf.get("formatos") or ["reel", "historia", "post", "carrusel"]
    nets = {a.get("platform") for a in (user.get("social") or [])}
    if not (user.get("ig") or {}).get("connected") and "instagram" not in nets and (nets or not (os.environ.get("PRUEBA") or user.get("_prueba"))):
        allowed = [f for f in allowed if f == "reel"] or ["reel"]  # solo TikTok/YouTube: solo vídeo
    voices = {**VOICES, **conf.get("voces", {}), "_elegida": plan_conf.get("voz_elegida") or ""}
    cfg = dict(conf.get("marca", {}))
    cfg["v"] = dict(conf.get("visual", {}))
    if cfg["v"].get("bgMode") in ("stock", "media"):
        cfg["v"]["bgMode"] = "ai"
    init = {
        "ma_cfg": json.dumps(cfg),
        "ma_groq_key": json.dumps(key),
        "ma_groq_model": json.dumps(conf.get("modelo", "openai/gpt-oss-120b")),
        "ma_hist": json.dumps(history),
        "ma_poll_token": json.dumps(os.environ.get("POLLINATIONS_TOKEN", "")),
        "ma_pexels_key": json.dumps(os.environ.get("PEXELS_API_KEY", "")),
    }
    brand_photos = []
    try:  # fotos propias de la marca (productos, equipo, local): tienen prioridad sobre las de stock
        brand_photos = api("GET", f"/admin/photos/{uid}").get("photos", [])
    except Exception as e:
        log("  ⚠ No se pudieron cargar las fotos de la marca:", e)
    if brand_photos:
        log(f"  📸 {len(brand_photos)} fotos propias de la marca disponibles")
    init_js = "window.__AGENCIA_SERVER=true;try{localStorage.clear();" + "".join(
        f"localStorage.setItem({json.dumps(k)},{json.dumps(v)});" for k, v in init.items()) + "}catch(e){}"
    init_js += ";window.__brandPhotos=" + json.dumps(brand_photos) + ";"
    faceless = str(((user.get("config") or {}).get("plan") or {}).get("estilo_video", "faceless")) != "clasico"
    init_js += f";window.__faceless={'true' if faceless else 'false'};"

    page = browser.new_page(viewport={"width": 480, "height": 900})
    page.expose_function("__genImage", lambda prompt, seed=0: gen_image(prompt, seed, log))
    try:
        page.on("pageerror", lambda e: log("  Error en la página:", e))
        install_routes(page)
        page.add_init_script(init_js)
        page.goto((DOCS / "index.html").as_uri())
        page.wait_for_function("typeof S!=='undefined' && typeof buildScenes==='function'")
        page.evaluate("ensureFonts()")
        page.wait_for_timeout(1500)
        page.evaluate("(allowed)=>{ delete FONDOS.stock; delete FONDOS.mis; window.__allowed = allowed; }", allowed)
        page.evaluate("a=>{S.count=a[0]; PLAN_LIMITS={maxDia:a[1],maxSemana:a[2]}; AUDIO_CAPS=true;}",
                      ["1" if redo else modo, max(1, int(plan_conf.get("max_por_dia", 2))), max(1, int(plan_conf.get("max_semana", 5)))])
        if redo and redo.get("brief") is not None:
            tipo_b = redo["tipo"] if redo["tipo"] in allowed else None
            hora = datetime.fromisoformat(redo["publishAt"]).astimezone(tz).strftime("%H:%M")
            page.evaluate("""r=>{ window.__allowed=r.allowed; const orig=window.__origBP||buildPrompt; window.__origBP=orig;
              window.buildPrompt=()=>orig("1").replace(/exactamente 1 pieza\\(s\\) para HOY \\(dia 0, [^)]*\\)\\. Elige tú el tipo\\./,
                'UNA sola pieza '+(r.tipo?'de tipo "'+r.tipo+'" ':'(elige tú el mejor tipo) ')+'para el dia 0 a las '+r.hora+'. La ha pedido el dueño de la cuenta: tema "'+r.tema+'"'+(r.brief?'. Indicaciones: "'+r.brief+'"':'')+'. Respeta lo que pide y hazla tan llamativa como el mejor contenido de la cuenta.'); }""",
                          {"tipo": tipo_b, "allowed": [tipo_b] if tipo_b else allowed, "hora": hora, "tema": redo.get("tema", ""), "brief": redo.get("brief", "")})
        elif redo:
            hora = datetime.fromisoformat(redo["publishAt"]).astimezone(tz).strftime("%H:%M")
            page.evaluate("""r=>{ window.__allowed=[r.tipo]; const orig=window.__origBP||buildPrompt; window.__origBP=orig;
              window.buildPrompt=()=>orig("1").replace(/exactamente 1 pieza\\(s\\) para HOY \\(dia 0, [^)]*\\)\\. Elige tú el tipo\\./,
                'UNA sola pieza de tipo "'+r.tipo+'" para el dia 0 a las '+r.hora+'. Sustituye a otra pieza que no gustó (tema: "'+r.tema+'"): haz algo claramente distinto y mejor, con otro gancho y otro enfoque.'); }""",
                          {"tipo": redo["tipo"], "hora": hora, "tema": redo.get("tema", "")})
        temas_saber = brain.knowledge_topics(user)
        usados = brain.used_topics(udir)
        page.evaluate("t=>{window.__memText=t;}", brain.memory_text(mem, notas) + brain.best_examples(mem) + (
            "\nTEMAS CON BASE VERIFICADA (los consejos de la semana deben salir de estos temas o de algo muy cercano; "
            "así no se inventa nada): " + "; ".join(temas_saber) if temas_saber else "") + (
            "\nTEMAS YA TRATADOS EN LAS ÚLTIMAS 3 SEMANAS (no repitas ninguno ni lo reformules; elige otros de la lista verificada): "
            + "; ".join(usados) if usados else "") +
            "\nVARIEDAD: como mucho 2 piezas de la semana sobre el mismo subtema; mezcla técnica de pilotaje, configuración del volante y los pedales, ajustes del coche y equipo.")
        log("  Pidiendo el plan a la IA…")
        if os.environ.get("GEMINI_API_KEY"):
            # si Groq se queda sin cupo gratis, el plan se pide a Gemini (también gratis)
            try:
                page.expose_function("__geminiText", lambda pr, sy="": brain.gemini_text(pr, lambda *a: log(*a), sy))
            except Exception:
                pass
            page.evaluate("""()=>{ if(window.__gsWrapped) return; window.__gsWrapped=true; const orig=window.groqSample;
              window.groqSample=async function(prompt,opts,retried){
                try{ return await orig(prompt,opts,retried); }
                catch(e){ const msg=String((e&&(e.message||e.code))||""); if(e&&(e.code==="rate_limited"||/too large|tokens per minute|TPM|rate limit|429|413/i.test(msg))&&window.__geminiText){
                    const t=await window.__geminiText(prompt,"Eres un experto en marketing de Instagram en español. Respondes siempre SOLO con JSON válido.");
                    if(t){ console.log("plan con Gemini"); return {text:t}; } }
                  throw e; } }; }""")
        for intento in range(4):
            page.click("#genBtn")
            page.wait_for_function("!document.querySelector('#genBtn').disabled", timeout=300_000)
            status = page.inner_text("#genStatus")
            if not re.search(r"L[íi]mite|Demasiadas|rate|429|saturad", status, re.I) or page.evaluate("(S.today&&S.today.videos||[]).length"):
                break
            # Groq gratis tiene límite por minuto: esperar y, si sigue, pasar a un modelo con cupo propio
            if intento >= 1:
                alt = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"][(intento - 1) % 2]
                page.evaluate("m=>{try{localStorage.setItem('ma_groq_model',JSON.stringify(m));}catch(e){}}", alt)
                log(f"  ⏳ Groq saturado: espero y pruebo con {alt}…")
            else:
                log("  ⏳ Groq saturado: espero un minuto y lo intento otra vez…")
            time.sleep(65)
        if "recomienda no publicar" in status:
            log("  " + status)
            return False
        n = page.evaluate("(S.today&&S.today.videos||[]).length")
        if not n or "No he podido" in status:
            raise RuntimeError("No se pudo crear el plan: " + status)
        page.evaluate("""()=>{
          S.today.videos = S.today.videos.filter(p=>window.__allowed.includes(p.tipo));
          if (window.__origBP) S.today.videos = S.today.videos.slice(0, 1);
          S.today.videos.forEach(p=>{ if(p.tipo==='historia' && p.sticker){ p.sticker.auto = true; } });
        }""")
        n = page.evaluate("S.today.videos.length")
        if redo and not n:
            raise RuntimeError("La IA no devolvió una pieza del tipo pedido.")
        audio_pref = AUDIO_MODES.get(str(plan_conf.get("audio", "auto")))
        if audio_pref:
            page.evaluate("a=>S.today.videos.forEach(p=>{p.audio=a;})", audio_pref)
        # faceless: la voz siempre está presente (con música baja de fondo)
        page.evaluate("()=>S.today.videos.forEach(p=>{if(p.layout==='faceless'&&p.audio!=='solo_voz')p.audio='voz_y_musica';})")
        log("  Estrategia:", page.evaluate("S.today.estrategia"))
        # 🎨 director de arte: cada frase de los vídeos faceless con una imagen concreta del mundo del negocio
        vids = page.evaluate("JSON.parse(JSON.stringify(S.today.videos))")
        # 🛒 piezas de producto: 1 de cada 3 reels/carruseles termina con lo que hace la app y dónde descargarla
        _prod = brain.producto(user)
        if _prod:
            cand = [vp for vp in vids if vp.get("tipo") in ("reel", "carrusel")]
            for k, vp in enumerate(cand):
                if k % 3 == 1 or (len(cand) <= 2 and k == len(cand) - 1):
                    vp["producto"] = True
                    vp["producto_nombre"] = _prod["nombre"]
                    vp["captura_url"] = brain.captura_para(_prod, str(vp.get("tema", "")) + " " + str(vp.get("gancho", "")))
                    log(f"    🛒 Pieza de producto: {vp['tipo']} «{vp.get('tema', '')}»")
        for vi, vp in enumerate(vids):
            if vp.get("layout") == "faceless" and TYPES_VIDEO(vp):
                if brain.write_script(vp, user, key, lambda *a: log(*a)):
                    page.evaluate("a=>{const p=S.today.videos[a[0]];p.beats=a[1];p.guion=a[1].map(b=>b.t);p.cierre_bio=a[2]||p.cierre_bio;if(a[3])p.caption=a[3];if(a[4])p.tema=a[4];if(a[5])p.gancho=a[5];}", [vi, vp["beats"], vp.get("cierre_bio", ""), vp.get("caption", ""), vp.get("tema", ""), vp.get("gancho", "")])
                    log(f"    ✍️ Guion de «{vp.get('tema', '')}»: {len(vp['beats'])} frases, {sum(len(b['t'].split()) for b in vp['beats'])} palabras")
            if vp.get("layout") == "faceless" and len(vp.get("beats") or []) >= 2:
                _aj = load_json(CONTENT / "users" / user["id"] / "ajustes.json", {})
                user["persona_veo"], user["escenario_veo"] = _aj.get("persona_veo", ""), _aj.get("escenario_veo", "")
                if brain.art_direct(vp, user, key, lambda *a: log(*a)):
                    page.evaluate("a=>{const p=S.today.videos[a[0]];p.beats=a[1];p.estilo_visual=a[2];}", [vi, vp["beats"], vp.get("estilo_visual", "")])
                    log(f"    🎨 Imágenes dirigidas para «{vp.get('tema', '')}»: {vp['beats'][-1]['img'][:90]}…")
            elif vp.get("tipo") in ("post", "carrusel"):  # también las imágenes de carruseles y posts
                # siempre con foto generada, una por diapositiva (con fondo de color liso quedaban vacíos y todos iguales)
                page.evaluate("i=>{const p=S.today.videos[i];p.fondo='ia';p.layout='cine';}", vi)
                if brain.write_slides(vp, user, key, lambda *a: log(*a)):
                    page.evaluate("""a=>{const p=S.today.videos[a[0]];const es=a[1];
                      p.escenas=es.map((e,i)=>({...(p.escenas[i]||{emoji:"",visual:"",visuales:[],foto:"",grafico:null}),titulo:e.titulo,texto:e.texto}));
                      if(a[2])p.tema=a[2];if(a[3])p.gancho=a[3];if(a[4])p.caption=a[4];}""",
                                  [vi, [{"titulo": e["titulo"], "texto": e["texto"]} for e in vp["escenas"]], vp.get("tema", ""), vp.get("gancho", ""), vp.get("caption", "")])
                    log(f"    ✍️ Texto de «{vp.get('tema', '')}»: {len(vp['escenas'])} diapositiva(s)")
                bs = brain.beats_of(vp)
                if len(bs) >= 2 and brain.art_direct(vp, user, key, lambda *a: log(*a), beats=bs):
                    page.evaluate("""a=>{const p=S.today.videos[a[0]];const b=a[1];p.visual=b[0].img;(p.escenas||[]).forEach((e,i)=>{if(b[i+1])e.visual=b[i+1].img;});}""", [vi, bs])
                    log(f"    🎨 Imágenes dirigidas para «{vp.get('tema', '')}»: {bs[0]['img'][:90]}…")
                page.evaluate("i=>{const p=S.today.videos[i];if(!String(p.cta||'').trim())p.cta='Guárdalo para tu próxima sesión';}", vi)  # cierre nunca vacío
                caps = [(k, e["captura"]) for k, e in enumerate(vp.get("escenas") or []) if e.get("captura")]
                if caps:  # captura real de la app en la diapositiva del producto
                    page.evaluate("""a=>{const p=S.today.videos[a[0]];a[1].forEach(([k,u])=>{if(p.escenas[k])p.escenas[k].visual='CAPTURA '+u;});p.cta='Guárdalo para tu próxima sesión';}""", [vi, caps])
                    log(f"    📸 Captura de la app en la diapositiva {caps[0][0] + 1}")
        # 🏷 pies de foto: por pieza, a partir de lo que de verdad dice (guion o diapositivas ya escritos)
        for vi, vp in enumerate(vids):
            if vp.get("tipo") != "historia" and brain.write_caption(vp, user, key, lambda *a: log(*a)):
                page.evaluate("a=>{const p=S.today.videos[a[0]];p.caption=a[1];p.hashtags=a[2];}", [vi, vp["caption"], vp.get("hashtags", "")])
        # 🎥 modo «Veo obligatorio»: los vídeos no se renderizan con imágenes; van a la bolsa y el calendario guarda su hueco
        huecos, bolsa_pieces = [], []
        if str(plan_conf.get("veo_modo", "si_hay")) == "obligatorio" and not redo:
            import bolsa
            import veo
            keep = []
            for vi, vp in enumerate(vids):
                if vp.get("layout") == "faceless" and TYPES_VIDEO(vp) and len(vp.get("beats") or []) >= 2 and veo.plan_clips(vp):
                    huecos.append({"tipo": vp["tipo"], "dia": vp.get("dia", 0), "hora": vp.get("hora"), "idx": vi})
                    bolsa_pieces.append(vp)
                else:
                    keep.append(vi)
            if bolsa_pieces:
                bolsa.add(user, bolsa_pieces, today, lambda *a: log(*a))
                page.evaluate("k=>{S.today.videos=k.map(i=>S.today.videos[i]);}", keep)
                n = len(keep)
        _pt = os.environ.get("POLLINATIONS_TOKEN", "").strip()
        log("  Motor de imágenes de reserva: " + ("Pollinations con clave nueva (FLUX)" if _pt[:3] == "sk_" else "Pollinations con token antiguo" if _pt else "Pollinations ANÓNIMO (mala calidad: falta POLLINATIONS_TOKEN)"))
        raw_n = page.evaluate("()=>window.__planRaw||0")
        for why in page.evaluate("()=>window.__planDrop||[]") or []:
            log(f"    ✗ Descartada: {why}")
        log(f"  {n} piezas" + (f" (la IA propuso {raw_n}; {raw_n - n} descartadas por venir incompletas)" if raw_n and raw_n > n else "") + ". Generando imágenes…")
        page.evaluate("prefetchAll()")
        page.wait_for_function("typeof pendingA==='undefined' || pendingA.size===0", timeout=2_700_000, polling=2000)
        page.wait_for_timeout(1000)
        miss = "S.today.videos.reduce((a,p)=>a+needed(p).filter(it=>!S.assets.has(it.k)).length,0)"
        total_img = page.evaluate("S.today.videos.reduce((a,p)=>a+needed(p).length,0)")
        if total_img and page.evaluate(miss):  # segunda vuelta para las imágenes que fallaron
            log(f"  ⏳ Faltan {page.evaluate(miss)}/{total_img} imágenes; espero y lo vuelvo a intentar…")
            time.sleep(60)
            page.evaluate("prefetchAll()")
            page.wait_for_function("typeof pendingA==='undefined' || pendingA.size===0", timeout=2_700_000, polling=2000)
        if total_img:
            got = total_img - page.evaluate(miss)
            log(f"  🖼 Imágenes: {got}/{total_img} generadas ({IMG_ENGINE['n'] + POLL['n']} con FLUX de calidad)" + (f" · último error de Pollinations: {POLL['err']}" if POLL['err'] else "") + ("" if got == total_img else " (el generador gratuito limita la velocidad)"))
        page.evaluate(RENDER_JS)
        pieces = page.evaluate("JSON.parse(JSON.stringify(S.today.videos))")
        out_root = DOCS / "media" / uid / today
        out_root.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%H%M%S")
        new_items = []
        for i, p in enumerate(pieces):
            pid = (f"{today}-{'c' if redo.get('brief') is not None else 'r'}{stamp}-{p['tipo']}" if redo else f"{today}-{i + 1:02d}-{p['tipo']}")
            folder = out_root / pid
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)
            info = page.evaluate("i=>__agRender.info(i)", i)
            log(f"  [{i + 1}/{n}] {p['tipo']} · {p.get('hora', '')} · {p.get('tema', '')}")
            media = []
            credit = ""
            if info["video"]:
                with tempfile.TemporaryDirectory() as td:
                    tdp = Path(td)
                    voice_clips = []
                    vname = None
                    if p.get("audio") in ("voz_y_musica", "solo_voz"):
                        try:
                            voice_clips, vname = make_voice(page, i, p, tdp, voices)
                            log(f"    voz: {vname} ({len(voice_clips)} frases)")
                        except Exception as e:
                            log("    ⚠ No se pudo generar la voz, va solo con música:", e)
                            page.evaluate("i=>{delete S.today.videos[i]._durs;delete S.today.videos[i]._caps;}", i)
                            voice_clips = []
                    music, track = (None, None) if p.get("audio") == "solo_voz" else pick_music(p.get("musica"), tdp, free_only=p["tipo"] == "historia")  # las historias no llevan texto donde acreditar
                    import music_lib
                    credit = music_lib.credit(track)
                    log(f"    música: {(track or {}).get('title', 'ninguna')} · ambiente {p.get('musica') or '-'} · audio {p.get('audio') or '-'}")
                    total = float(page.evaluate("i=>buildScenes(S.today.videos[i]).total", i))
                    cover_url = page.evaluate("i=>__agRender.cover(i)", i)
                    cover_bytes = base64.b64decode(cover_url.split(",", 1)[1])
                    (folder / "cover.jpg").write_bytes(cover_bytes)
                    count = int(total * FPS + 0.999)
                    f = 0
                    while f < count:
                        batch = page.evaluate("a=>__agRender.frames(a[0],a[1],a[2],a[3])", [i, f, 45, FPS])
                        if not batch:
                            break
                        write_dataurls(batch, tdp, f)
                        f += len(batch)
                    for k in range(min(COVER_FRAMES, count)):  # los primeros fotogramas son la portada (miniatura)
                        (tdp / f"f_{k:05d}.jpg").write_bytes(cover_bytes)
                    sfx_path = None
                    try:
                        import sfx
                        events = page.evaluate("i=>sceneEvents(buildScenes(S.today.videos[i]))", i)
                        sfx_path = sfx.track(events, total, tdp / "sfx.wav")
                    except Exception as e:
                        log("    ⚠ Sin efectos de sonido:", e)
                    encode_video(tdp, total, folder / "video.mp4", voice_clips, music, "", sfx_path)  # sin marca de agua de la canción
                media.append(str((folder / "video.mp4").relative_to(DOCS)))
            else:
                for k in range(int(info["slides"])):
                    url = page.evaluate("a=>__agRender.slide(a[0],a[1])", [i, k])
                    path = folder / f"{k + 1:02d}.jpg"
                    path.write_bytes(base64.b64decode(url.split(",", 1)[1]))
                    media.append(str(path.relative_to(DOCS)))
            caption = brain.de_voseo((p.get("caption", "") + "\n\n" + p.get("hashtags", "")).strip()) if p["tipo"] != "historia" else ""
            if caption and info["video"] and credit:
                caption += "\n\n" + credit
            when = publish_time(today, p.get("dia", 0), p.get("hora"), tz, i)
            if when < now + timedelta(minutes=15):  # la hora de hoy ya pasó: se publica en un rato
                when = now + timedelta(minutes=15 + 20 * i)
            if redo:
                when = datetime.fromisoformat(redo["publishAt"]).astimezone(tz)
                if when < now + timedelta(minutes=20):
                    when = now + timedelta(minutes=30)
            floor = publish_floor(user, tz)
            if floor and when < floor:  # el dueño ha pedido no publicar antes de esa fecha: mismo hora, primer día permitido
                when = when.replace(year=floor.year, month=floor.month, day=floor.day)
                while when < floor:
                    when += timedelta(days=1)
                log(f"    ⏸ Se retrasa hasta {when.strftime('%d/%m %H:%M')} (no publicar antes de {floor.strftime('%d/%m %H:%M')})")
            veo_plan = None
            if info["video"] and p.get("layout") == "faceless":
                try:
                    import veo
                    cl = veo.plan_clips(p)
                    if cl:
                        veo_plan = {"clips": cl, "voz": vname if info["video"] and p.get("audio") in ("voz_y_musica", "solo_voz") and vname else VOICES.get(p.get("voz") if p.get("voz") in ("mujer", "hombre") else "mujer"),
                                    "ritmo": RATES.get(p.get("ritmo_voz") or "normal", "+8%"), "musica": p.get("musica") or "",
                                    "hashtags": p.get("hashtags", "")}
                        log(f"    🎬 {len(cl)} clips de Veo preparados (duraciones {', '.join(str(c['dur']) for c in cl)} s)")
                except Exception as e:
                    log("    ⚠ No se pudieron preparar los clips de Veo:", e)
            new_items.append({
                "id": pid, "date": today, "source": ("chat" if redo.get("brief") is not None else "redo") if redo else "auto", "tipo": p["tipo"], "tema": p.get("tema", ""),
                "publishAt": when.isoformat(),
                "caption": caption[:2200], "media": media, "status": "pending" if (user.get("social") or (user.get("ig") or {}).get("connected")) else "prueba", "attempts": 0,
                **({"cover": str((folder / "cover.jpg").relative_to(DOCS))} if (folder / "cover.jpg").exists() else {}),
                **({"veo": veo_plan} if veo_plan else {}),
            })
        for h in huecos:  # el hueco: tipo y hora; el vídeo lo pone luego la bolsa (el mejor que esté listo)
            when = publish_time(today, h["dia"], h["hora"], tz, h["idx"])
            floor = publish_floor(user, tz)
            if floor and when < floor:
                log(f"    ⏸ Hueco de {h['tipo']} del {when.strftime('%d/%m %H:%M')} descartado (no publicar antes de {floor.strftime('%d/%m %H:%M')})")
                continue
            if when < now + timedelta(hours=1):
                continue
            new_items.append({"id": f"{today}-{h['idx'] + 1:02d}-{h['tipo']}", "date": today, "source": "auto", "tipo": h["tipo"], "tema": "",
                              "publishAt": when.isoformat(), "caption": "", "media": [], "status": "hueco", "attempts": 0})
        if huecos:
            log(f"  🎥 {len(bolsa_pieces)} guion(es) de vídeo a la bolsa de Veo · {sum(1 for it in new_items if it['status'] == 'hueco')} hueco(s) en el calendario")
        pieces = pieces + bolsa_pieces
        hist = page.evaluate("S.hist")
        estrategia = page.evaluate("S.today.estrategia")
    finally:
        page.close()

    # Lo que se sustituye: la pieza rehecha o, si se rehace la semana, todo lo que aún no se ha publicado
    if redo:
        replaced = [q for q in queue if q.get("id") == redo["id"]]
    elif force:  # rehacer la semana: menos lo marcado como fijo y los vídeos de Veo ya revisados de la bolsa
        replaced = [q for q in queue if q.get("status") in ("pending", "scheduled", "esperando_veo", "hueco", "prueba") and datetime.fromisoformat(q["publishAt"]) > now
                    and not (q.get("fijo") or q.get("bolsa"))]
    else:
        replaced = [q for q in queue if q.get("date") == today and q.get("source") == "auto" and q.get("status") in ("pending", "scheduled", "esperando_veo", "hueco")]
    for q in replaced:
        zernio_cancel(q)
    gone = {q["id"] for q in replaced}
    kept = [q for q in queue if (q.get("fijo") or q.get("bolsa")) and q.get("status") in ("pending", "scheduled", "prueba") and datetime.fromisoformat(q["publishAt"]) > now]
    if kept and not redo:  # lo nuevo no puede caer el mismo día que algo que se queda (un contenido de feed y una historia al día)
        def _clash(it):
            g = "historia" if it["tipo"] == "historia" else "feed"
            d = datetime.fromisoformat(it["publishAt"]).astimezone(tz).date()
            return any(("historia" if k["tipo"] == "historia" else "feed") == g and datetime.fromisoformat(k["publishAt"]).astimezone(tz).date() == d for k in kept)
        pares = [(it, pc) for it, pc in zip(new_items, pieces + [None] * (len(new_items) - len(pieces))) if not _clash(it)]
        if len(pares) < len(new_items):
            log(f"  ↷ {len(new_items) - len(pares)} pieza(s) nuevas movidas fuera: ese día ya hay algo que se queda")
        new_items = [a for a, _ in pares]
        pieces = [b for _, b in pares if b is not None]
    veo_modo = str(plan_conf.get("veo_modo", "si_hay"))
    for it in new_items:
        if veo_modo == "obligatorio" and it.get("veo") and it.get("status") == "pending":
            it["status"] = "esperando_veo"  # no se programa hasta tener los clips de Veo; si no llegan, no sale
    if veo_modo == "obligatorio" and any(it.get("status") == "esperando_veo" for it in new_items):
        log(f"  🎥 {sum(1 for it in new_items if it.get('status') == 'esperando_veo')} pieza(s) esperando sus clips de Veo (sin ellos no se publican)")
    if user.get("social"):
        for it, piece in zip(new_items, pieces):
            if it.get("status") in ("esperando_veo", "hueco"):
                continue
            try:
                pid_z = zernio_schedule(user, it, piece, tz)
                if pid_z:
                    it["status"] = "scheduled"
                    it["zernioPostId"] = pid_z
                    log(f"  📅 Programado en redes: {it['tipo']} · {it['publishAt']}")
                elif not (user.get("ig") or {}).get("connected"):
                    it["status"] = "expired"
                    it["error"] = "Ninguna red conectada admite este formato."
            except Exception as e:
                log("  ✗ No se pudo programar en Zernio:", e)
                if not (user.get("ig") or {}).get("connected"):
                    it["status"] = "failed"
                    it["error"] = str(e)[:300]
    if not redo:
        gone |= {q["id"] for q in queue if q.get("date") == today and q.get("source") == "auto" and not (q.get("fijo") or q.get("bolsa"))}
    queue = [q for q in queue if q["id"] not in gone] + new_items
    queue.sort(key=lambda q: q["publishAt"])
    save_json(queue_path, queue)
    save_json(hist_path, hist)
    if not redo:
        save_json(udir / "plans" / f"{today}.json", {"date": today, "estrategia": estrategia, "piezas": pieces})
    mem = brain.after_plan(mem, pieces)
    brain.save(mem_path, mem)
    try:
        extra = {}
        if (udir / "bolsa.json").exists():
            import bolsa
            extra["bolsa"] = bolsa.summary(uid)
        api("PUT", f"/admin/status/{uid}", {"estrategia": "" if redo else estrategia, "queue": summarize(queue), "memoria": brain.public(mem), **extra})
    except Exception as e:
        log("  ⚠ No se pudo avisar a la app:", e)

    # Limpieza de archivos antiguos ya publicados
    keep = int(plan_conf.get("dias_guardar_media", 10))
    limit = (now - timedelta(days=keep)).date().isoformat()
    busy = {m.split("/")[2] for q in queue if q.get("status") == "pending" for m in q.get("media", []) if m.count("/") >= 3}
    for d in (DOCS / "media" / uid).glob("*"):
        if d.is_dir() and d.name < limit and d.name not in busy:
            shutil.rmtree(d)
    log(f"  Listo: {len(new_items)} pieza(s) {'rehecha' if redo else 'en la cola'}.")
    return True


def remove_items(user, ids):
    """Elimina piezas de la cola (pedido desde la app): anula en Zernio por si acaso y borra sus archivos."""
    uid = user["id"]
    qpath = CONTENT / "users" / uid / "queue.json"
    queue = load_json(qpath, [])
    gone = [q for q in queue if q.get("id") in set(ids)]
    for q in gone:
        zernio_cancel(q)
        folder = DOCS / "media" / uid / str(q.get("date", "")) / q["id"]
        if folder.exists():
            shutil.rmtree(folder)
    queue = [q for q in queue if q.get("id") not in set(ids)]
    save_json(qpath, queue)
    log(f"🗑 {user.get('name')}: {len(gone)} pieza(s) eliminada(s)")
    try:
        api("PUT", f"/admin/status/{uid}", {"estrategia": "", "queue": summarize(queue)})
    except Exception:
        pass


def reset_user(user):
    """Reinicio de cuenta: anula lo programado y borra cola, historial y archivos de esa persona."""
    uid = user["id"]
    udir = CONTENT / "users" / uid
    for q in load_json(udir / "queue.json", []):
        zernio_cancel(q)
    for d in (udir, DOCS / "media" / uid):
        if d.exists():
            shutil.rmtree(d)
    log("  Cuenta reiniciada: cola, historial y archivos borrados.")


def ready(u):
    conf = u.get("config") or {}
    marca = conf.get("marca") or {}
    return bool((marca.get("name") or marca.get("about")) and ((u.get("ig") or {}).get("connected") or u.get("social") or os.environ.get("PRUEBA") or u.get("_prueba")))


def expire_veo(user, queue, udir, tz):
    """Piezas que esperaban sus clips de Veo y ya pasó su hora: no se publican (modo «obligatorio»)."""
    now = datetime.now(tz)
    gone = [q for q in queue if q.get("status") == "esperando_veo" and datetime.fromisoformat(q["publishAt"]) < now + timedelta(minutes=10)]
    for q in gone:
        q["status"] = "sin_veo"
        q["error"] = "No llegaron los clips de Veo a tiempo; no se publica."
        log(f"  ⏭ «{q.get('tema', '')}» no sale: no se generaron sus clips de Veo a tiempo")
    if gone:
        save_json(udir / "queue.json", queue)
    return bool(gone)


def backfill_veo(user, queue, udir, tz):
    """Piezas ya programadas antes del modo Veo: se les preparan los clips a partir de su plan."""
    import glob
    import veo
    now = datetime.now(tz)
    pieces = []
    for f in sorted(glob.glob(str(udir / "plans" / "*.json")))[-4:]:
        pieces += (load_json(Path(f), {}) or {}).get("piezas") or []
    n = 0
    for q in queue:
        if q.get("veo") or q.get("tipo") not in ("reel", "historia") or q.get("status") not in ("pending", "scheduled", "prueba"):
            continue
        if datetime.fromisoformat(q["publishAt"]) < now + timedelta(hours=1):
            continue
        p = next((x for x in reversed(pieces) if x.get("tipo") == q["tipo"] and x.get("tema") == q.get("tema") and x.get("beats")), None)
        cl = veo.plan_clips(p) if p else None
        if not cl:
            continue
        q["veo"] = {"clips": cl, "voz": VOICES.get(p.get("voz") if p.get("voz") in ("mujer", "hombre") else "mujer"),
                    "ritmo": RATES.get(p.get("ritmo_voz") or "normal", "+8%"), "musica": p.get("musica") or "", "hashtags": p.get("hashtags", "")}
        n += 1
    if n:
        save_json(udir / "queue.json", queue)
        log(f"  🎬 Clips de Veo preparados para {n} pieza(s) ya programadas")
        try:
            api("PUT", f"/admin/status/{user['id']}", {"queue": summarize(queue)})
        except Exception as e:
            log("  ⚠ No se pudo avisar a la app:", e)


def fill_bolsa(user, queue=None):
    """Pone en los huecos de vídeo del calendario los mejores vídeos ya montados y avisa a la app."""
    import bolsa
    uid = user["id"]
    qpath = CONTENT / "users" / uid / "queue.json"
    if queue is None:
        queue = load_json(qpath, [])
    tz = ZoneInfo(((user.get("config") or {}).get("plan") or {}).get("zona_horaria", "Europe/Madrid"))
    b = bolsa.load(uid)
    pruned = bolsa.prune(b, datetime.now(tz).date().isoformat())
    if len(pruned) != len(b):
        bolsa.save(uid, pruned)
    changed = bolsa.fill(user, queue, tz, lambda *a: log(*a))
    if changed:
        save_json(qpath, queue)
    if changed or (CONTENT / "users" / uid / "bolsa.json").exists():
        try:
            api("PUT", f"/admin/status/{uid}", {"queue": summarize(queue), "bolsa": bolsa.summary(uid)})
        except Exception as e:
            log("  ⚠ No se pudo avisar a la app:", e)
    return changed


def fetch_clip(uid, iid, k, path):
    base = os.environ.get("AGENCIA_API", "").strip().rstrip("/")
    token = os.environ.get("AGENCIA_ADMIN_TOKEN", "").strip()
    r = requests.get(f"{base}/admin/clips/{uid}/{iid}/{k}", timeout=120, headers={"authorization": "Bearer " + token})
    if r.status_code != 200 or len(r.content) < 1000:
        raise RuntimeError(f"clip {k + 1}: HTTP {r.status_code}")
    Path(path).write_bytes(r.content)


def assemble_veo(user, iid):
    """Monta una pieza con los clips de Veo que la persona ha subido y sustituye la versión programada."""
    import veo
    uid = user["id"]
    udir = CONTENT / "users" / uid
    queue_path = udir / "queue.json"
    queue = load_json(queue_path, [])
    tz = ZoneInfo(((user.get("config") or {}).get("plan") or {}).get("zona_horaria", "Europe/Madrid"))
    item = next((q for q in queue if q.get("id") == iid), None)
    if not item or not item.get("veo") or item.get("status") not in ("pending", "scheduled", "failed", "esperando_veo", "prueba"):
        log(f"  — {iid}: ya no se puede montar con Veo.")
        return False
    if datetime.fromisoformat(item["publishAt"]) < datetime.now(tz) + timedelta(minutes=12):
        log(f"  — {iid}: ya se ha publicado o está a punto; se deja como estaba.")
        return False
    v = item["veo"]
    log(f"🎬 Montando «{item.get('tema', '')}» con {len(v['clips'])} clips de Veo")
    with tempfile.TemporaryDirectory() as td:
        segs = []
        for k, c in enumerate(v["clips"]):
            path = Path(td) / f"c{k}.mp4"
            fetch_clip(uid, iid, k, path)
            segs.append({"clip": path, "t": c["t"]})
        folder = DOCS / "media" / uid / item.get("date", datetime.now(tz).strftime("%Y-%m-%d")) / f"{iid}-veo"
        if folder.exists():
            shutil.rmtree(folder)
        total, meta = veo.compose(segs, v.get("voz") or VOICES["hombre"], v.get("ritmo") or "+8%", v.get("musica") or "energetica",
                                  folder / "video.mp4", log, music_free_only=item["tipo"] == "historia", hook=item.get("gancho", "") if isinstance(item, dict) else "")
    log(f"    ✓ {total:.1f}s · música: {(meta or {}).get('title', 'ninguna')}")
    zernio_cancel(item)
    item["media"] = [str((folder / "video.mp4").relative_to(DOCS))]
    if (folder / "cover.jpg").exists():
        item["cover"] = str((folder / "cover.jpg").relative_to(DOCS))
    item["status"] = "pending" if item.get("status") != "prueba" else "prueba"
    item.pop("zernioPostId", None)
    item.pop("error", None)
    v["done"] = datetime.now(tz).isoformat()
    if user.get("social") and item["status"] != "prueba":
        try:
            pid_z = zernio_schedule(user, item, {"tema": item.get("tema"), "hashtags": v.get("hashtags", "")}, tz)
            if pid_z:
                item["status"], item["zernioPostId"] = "scheduled", pid_z
                log(f"    📅 Reprogramado con la versión Veo: {item['publishAt']}")
        except Exception as e:
            log("    ✗ No se pudo programar en Zernio:", e)
            item["status"], item["error"] = "failed", str(e)[:300]
    save_json(queue_path, queue)
    try:
        api("PUT", f"/admin/status/{uid}", {"queue": summarize(queue)})
    except Exception as e:
        log("  ⚠ No se pudo avisar a la app:", e)
    return True


def process_requests(browser, users, key):
    """Peticiones hechas desde la app: reiniciar cuenta, rehacer la semana o rehacer piezas sueltas."""
    errors = 0
    for u in users:
        r = u.get("requests") or {}
        if not (r.get("reset") or r.get("replan") or r.get("redo") or r.get("create") or r.get("remove") or r.get("veo")):
            continue
        label = f"{u.get('name')} ({u['id']})"
        done = {}
        if r.get("veo") and not r.get("reset"):
            for iid in r["veo"]:
                try:
                    if str(iid).startswith("b-"):
                        import bolsa
                        bolsa.assemble(u, iid, lambda *a: log(*a))
                    else:
                        assemble_veo(u, iid)
                except Exception as e:
                    errors += 1
                    log(f"  ✗ No se pudo montar {iid} con Veo:", e)
            if any(str(x).startswith("b-") for x in r["veo"]):
                try:
                    fill_bolsa(u)
                except Exception as e:
                    log("  ⚠ No se pudieron rellenar los huecos con la bolsa:", e)
            try:
                api("POST", f"/admin/requests/{u['id']}/done", {"veo": r["veo"]})
            except Exception as e:
                log("  ⚠ No se pudo marcar el montaje como hecho:", e)
            if not (r.get("replan") or r.get("redo") or r.get("create") or r.get("remove")):
                continue
        if r.get("remove"):
            remove_items(u, r["remove"])
            try:
                api("POST", f"/admin/requests/{u['id']}/done", {"remove": r["remove"]})
            except Exception as e:
                log("  ⚠ No se pudo marcar la eliminación como hecha:", e)
            if not (r.get("reset") or r.get("replan") or r.get("redo") or r.get("create")):
                continue
        try:
            if r.get("reset"):
                log(f"♻ {label}: reiniciar cuenta")
                reset_user(u)
                done = {"reset": r["reset"], "replan": r.get("replan"), "redo": r.get("redo") or [], "create": [c["id"] for c in r.get("create") or []]}
            elif not ready(u):
                log(f"— {label}: hay peticiones pero falta marca o redes; se descartan.")
                done = {"replan": r.get("replan"), "redo": r.get("redo") or [], "create": [c["id"] for c in r.get("create") or []]}
            elif r.get("create") and not r.get("replan"):
                done = {"create": []}
                for c in r.get("create") or []:
                    done["create"].append(c["id"])
                    tz = ZoneInfo(((u.get("config") or {}).get("plan") or {}).get("zona_horaria", "Europe/Madrid"))
                    when = c.get("cuando") or (datetime.now(tz) + timedelta(minutes=30)).isoformat()
                    item = {"id": c["id"], "tipo": c.get("tipo") or "auto", "tema": c.get("tema", ""), "brief": c.get("notas", ""),
                            "publishAt": datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(tz).isoformat()}
                    log(f"💬 {label}: crear {item['tipo']} «{item['tema']}» desde el chat")
                    try:
                        plan_user(browser, u, key, redo=item)
                    except Exception as e:
                        errors += 1
                        log(f"  ✗ No se pudo crear la publicación pedida:", e)
                if r.get("redo"):
                    done["redo"] = []  # se atienden en la siguiente vuelta
            elif r.get("replan"):
                log(f"🔄 {label}: rehacer la semana")
                plan_user(browser, u, key, force=True)
                REPLANNED.add(u["id"])
                done = {"replan": r["replan"], "redo": r.get("redo") or []}
            else:
                queue = load_json(CONTENT / "users" / u["id"] / "queue.json", [])
                done = {"redo": []}
                for rid in r.get("redo") or []:
                    item = next((q for q in queue if q.get("id") == rid), None)
                    done["redo"].append(rid)
                    if not item or item.get("status") not in ("pending", "scheduled"):
                        log(f"— {label}: la pieza {rid} ya no se puede rehacer.")
                        continue
                    if done["redo"][:-1]:
                        time.sleep(25)  # repartir las peticiones a la IA (límite por minuto de Groq)
                    log(f"🔄 {label}: rehacer {item['tipo']} «{item.get('tema', '')}»")
                    try:
                        plan_user(browser, u, key, redo=item)
                    except Exception as e:
                        errors += 1
                        log(f"  ✗ No se pudo rehacer {rid}:", e)
                    queue = load_json(CONTENT / "users" / u["id"] / "queue.json", [])
        except Exception as e:
            errors += 1
            log(f"  ✗ Error con {label}:", e)
        try:
            api("POST", f"/admin/requests/{u['id']}/done", {k: v for k, v in done.items() if v})
        except Exception as e:
            log("  ⚠ No se pudo marcar la petición como hecha:", e)
    return errors


REPLANNED = set()
IMG_ENGINE = {"ok": None, "n": 0}


POLL = {"ok": None, "n": 0, "err": "", "fails": 0}


def gen_pollinations(prompt, seed, log=print):
    """FLUX vertical con la clave secreta (sk_) de enter.pollinations.ai, desde Python (sin límites del navegador)."""
    key = os.environ.get("POLLINATIONS_TOKEN", "").strip()
    if not key.startswith("sk_") or POLL["ok"] is False:
        return ""
    from urllib.parse import quote
    url = (f"https://gen.pollinations.ai/image/{quote(str(prompt)[:1500], safe='')}"
           f"?model=flux&width=1080&height=1920&seed={int(seed or 0) % 2147483647}&nologo=true&private=true&enhance=false")
    for t in range(3):
        try:
            r = requests.get(url, timeout=75, headers={"Authorization": "Bearer " + key})
        except Exception as e:  # noqa: BLE001
            POLL["err"] = str(e)[:120]
            time.sleep(5)
            continue
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image/"):
            POLL["ok"] = True
            POLL["n"] += 1
            POLL["fails"] = 0
            return "data:" + ct.split(";")[0] + ";base64," + base64.b64encode(r.content).decode()
        POLL["err"] = f"HTTP {r.status_code}: {r.text[:160]}"
        if r.status_code in (401, 402, 403):  # clave mala o sin saldo: no insistir
            if POLL["ok"] is not False:
                log(f"  ⚠ Pollinations rechaza la clave ({POLL['err']}). Uso el generador de reserva.")
            POLL["ok"] = False
            return ""
        time.sleep(4 + 6 * t)
    POLL["fails"] += 1
    if POLL["fails"] >= 3 and POLL["ok"] is not True:  # no funciona: no perder horas reintentando
        POLL["ok"] = False
        log(f"  ⚠ Pollinations con clave no responde bien ({POLL['err']}). Sigo con el generador de reserva.")
    return ""


def gen_captura(url):
    """Una captura real del producto (de su web), en vez de una imagen generada."""
    r = requests.get(url, timeout=60, headers={"user-agent": "Mozilla/5.0 agencia"})
    r.raise_for_status()
    return "data:image/jpeg;base64," + base64.b64encode(vertical_captura(r.content)).decode()


def vertical_captura(data, W=1080, H=1920):
    """Una captura apaisada, en vertical: entera y nítida arriba, sobre un fondo desenfocado de ella misma
    (abajo queda sitio para el texto de la diapositiva)."""
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "c.img", Path(td) / "o.jpg"
        src.write_bytes(data)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex",
                        f"[0]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:3,eq=brightness=-0.25[bg];"
                        f"[0]scale={W - 60}:-2[fg];[bg][fg]overlay=(W-w)/2:H*0.14,format=yuvj420p",
                        "-frames:v", "1", "-q:v", "3", str(out)], check=True)
        return out.read_bytes()


def gen_image(prompt, seed, log=print):
    m = re.search(r"CAPTURA (https?://\S+?\.(?:jpe?g|png|webp))", str(prompt))
    if m:
        try:
            return gen_captura(m.group(1))
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ No se pudo bajar la captura ({e}); uso una imagen generada")
            prompt = str(prompt).replace(m.group(0), "over-the-shoulder view of the big monitor showing a sim racing telemetry app")
    d = gen_image_worker(prompt, seed, log) or gen_pollinations(prompt, seed, log)
    if not d:
        return d
    # control de calidad: si la imagen tiene un fallo grave, se repite una vez con otra semilla
    ok, fallos = brain.vision_check([d], "Esta imagen es para una publicación de Instagram. Se pidió: «" + str(prompt)[:700] + "».\n\n" + brain.FALLOS_GRAVES, log)
    if ok:
        return d
    log(f"    🔍 Imagen rechazada ({'; '.join(fallos)[:140]}); la repito")
    d2 = gen_image_worker(prompt, int(seed or 0) + 7919, log) or gen_pollinations(prompt, int(seed or 0) + 7919, log)
    return d2 or d


def gen_image_worker(prompt, seed, log=print):
    """Imagen de calidad (FLUX en Cloudflare Workers AI, gratis) a través del servidor de la agencia.
    Devuelve una data URL, o "" si el servidor no la ofrece (entonces se usa el generador de reserva)."""
    if IMG_ENGINE["ok"] is False:
        return ""
    base = os.environ.get("AGENCIA_API", "").strip().rstrip("/")
    token = os.environ.get("AGENCIA_ADMIN_TOKEN", "").strip()
    if not base or not token:
        IMG_ENGINE["ok"] = False
        return ""
    for t in range(3):
        try:
            r = requests.post(base + "/admin/image", json={"prompt": str(prompt)[:1800], "seed": int(seed or 0) % 2147483647},
                              timeout=120, headers={"authorization": "Bearer " + token})
        except Exception:
            time.sleep(5)
            continue
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            IMG_ENGINE["ok"] = True
            IMG_ENGINE["n"] += 1
            return "data:" + r.headers["content-type"].split(";")[0] + ";base64," + base64.b64encode(r.content).decode()
        if r.status_code in (404, 501) or (r.status_code == 429 and IMG_ENGINE["ok"] is None):
            IMG_ENGINE["ok"] = False
            log("  ⚠ El servidor aún no genera imágenes de calidad (hay que desplegarlo: cd worker; npx wrangler deploy). Uso el generador gratuito de reserva, que se ve peor.")
            return ""
        if r.status_code == 429:  # cupo diario gratuito agotado
            IMG_ENGINE["ok"] = False
            log("  ⚠ Cupo diario de imágenes de calidad agotado; el resto con el generador de reserva.")
            return ""
        time.sleep(6 + 6 * t)
    return ""


def TYPES_VIDEO(p):
    return p.get("tipo") in ("reel", "historia")


def main():
    users = api("GET", "/admin/users").get("users", [])
    has_requests = any((u.get("requests") or {}) for u in users)
    for u in users:
        if u.get("requests"):
            log(f"📥 Peticiones de {u.get('name')}: {json.dumps(u['requests'], ensure_ascii=False)[:300]}")
    if not has_requests and not any(ready(u) for u in users):
        log(f"{len(users)} personas en la agencia; ninguna tiene aún marca y redes conectadas. Nada que planificar.")
        return
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        sys.exit("Falta el secreto GROQ_API_KEY.")
    only = os.environ.get("ONLY_USER", "").strip()
    log(f"{len(users)} personas en la agencia.")
    errors = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
                                     executable_path=os.environ.get("CHROME_PATH") or None)
        if has_requests:
            for u in users:  # sin redes y pidiendo algo desde la app: se hace en modo prueba (no se publica nada)
                if u.get("requests") and not u.get("social") and not (u.get("ig") or {}).get("connected"):
                    u["_prueba"] = True
                    log(f"🧪 {u.get('name')}: sin redes conectadas — lo que ha pedido se crea en modo prueba, sin publicar.")
            errors += process_requests(browser, users, key)
            users = api("GET", "/admin/users").get("users", [])
        for u in users:  # red de seguridad: nada borrado o sustituido sigue programado en las redes
            if u.get("social"):
                retry_failed(u)
                zernio_reconcile(u, load_json(CONTENT / "users" / u["id"] / "queue.json", []), lambda *a: log(*a))
        for u in users:
            if only and u["id"] != only:
                continue
            conf = u.get("config") or {}
            marca = conf.get("marca") or {}
            label = f"{u.get('name')} ({u['id']})"
            if not (marca.get("name") or marca.get("about")):
                log(f"— {label}: sin marca configurada, se salta.")
                continue
            if not (u.get("ig") or {}).get("connected") and not u.get("social"):
                if not os.environ.get("PRUEBA"):
                    log(f"— {label}: sin redes conectadas, se salta.")
                    continue
                log(f"🧪 {label}: sin redes conectadas — modo prueba: se crea el contenido pero NO se publica nada.")
            if (conf.get("plan") or {}).get("auto") is False:
                log(f"— {label}: piloto automático en pausa.")
                continue
            log(f"▶ {label}")
            try:
                # FORCE (lanzado a mano con "rehacer"): sustituye TODO lo que aún no se ha publicado
                # si en esta misma ejecución ya se rehízo su semana, no se rehace otra vez
                plan_user(browser, u, key, force=bool(os.environ.get("FORCE")) and u["id"] not in REPLANNED)
            except Exception as e:
                errors += 1
                log(f"  ✗ Error con {label}:", e)
        browser.close()
    if errors:
        sys.exit(f"{errors} persona(s) con errores; revisa el registro.")


if __name__ == "__main__":
    main()
