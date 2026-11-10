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

from common import api, summarize

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
RATES = {"tranquilo": "-6%", "normal": "+4%", "energico": "+14%"}
MUSIC_EXT = (".mp3", ".m4a", ".wav", ".aac", ".ogg")


def tts(text, voice, rate, out_path):
    """Locución gratuita con las voces neuronales de Microsoft (edge-tts).
    Devuelve los tiempos de cada palabra [(inicio_s, duración_s, palabra)] para los subtítulos dinámicos."""
    # Playwright (modo síncrono) ya tiene un bucle asyncio en este hilo: la voz se genera en otro hilo.
    import asyncio
    import threading
    import edge_tts
    err, words = [], []

    async def go():
        try:
            com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        except TypeError:  # versiones antiguas: ya dan WordBoundary por defecto
            com = edge_tts.Communicate(text, voice, rate=rate)
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


def pick_music(mood, workdir=None):
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
    return music_lib.get_track(mood, CONTENT / "music", lambda *a: log(*a))


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
    music_vol = 0.16 if has_voice else 0.9
    fade_start = max(0.0, total - 1.2)
    parts, labels = [], []
    for k, (_, start_s) in enumerate(voice_clips):
        ms = int(start_s * 1000)
        parts.append(f"[{k + 1}:a]aresample=48000,adelay={ms}:all=1,volume=1.15[v{k}]")
        labels.append(f"[v{k}]")
    if sfx_track:
        cmd += ["-i", str(sfx_track)]
        parts.append(f"[{m_idx + 1}:a]aresample=48000,volume={0.32 if has_voice else 0.42}[fx]")
        labels.append("[fx]")
    norm = "loudnorm=I=-16:TP=-1.5:LRA=11," if music else ""
    parts.append(f"[{m_idx}:a]aresample=48000,{norm}volume={music_vol},afade=t=in:d=0.4,afade=t=out:st={fade_start:.2f}:d=1.2[mu]")
    labels.append("[mu]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,alimiter=limit=0.95[aout]")
    vmap = "0:v"
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if credit and Path(font).exists():
        parts.append(f"[0:v]drawtext=fontfile={font}:text='{ff_text(credit.replace("🎵", "♪"))}':fontsize=22:fontcolor=white@0.85:"
                     f"box=1:boxcolor=black@0.35:boxborderw=8:x=(w-text_w)/2:y=h-70[vout]")
        vmap = "[vout]"
    cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out_path)]
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
    voice = voices.get(p.get("voz") or "mujer", VOICES["mujer"])
    rate = RATES.get(p.get("ritmo_voz") or "normal", "+4%")
    clips, durs, caps = [], [], []
    for k, line in enumerate(lines):
        if not line:
            clips.append(None)
            durs.append(0)
            caps.append([])
            continue
        out = workdir / f"voz_{k:02d}.mp3"
        wb = tts(line, voice, rate, out) or []
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
    r = requests.put(up, data=Path(path).read_bytes(), headers={"Content-Type": ct}, timeout=600)
    if r.status_code >= 300:
        raise RuntimeError(f"Subida a Zernio falló ({r.status_code})")
    return pub


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
            log("  ⚠ No se pudo anular en Zernio:", e)


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
    if not (force or redo or os.environ.get("FORCE")):
        if any(q.get("date") == today and q.get("source") == "auto" for q in queue):
            log("  Ya se planificó hoy.")
            return False
        future = [q for q in queue if q.get("status") in ("pending", "scheduled") and datetime.fromisoformat(q["publishAt"]) > now + timedelta(hours=24)]
        if modo == "smart" and future:
            log(f"  Plan en marcha ({len(future)} piezas más allá de mañana). Hoy no hace falta planificar.")
            return False
    history = load_json(hist_path, {"items": []})
    published = fetch_published(user)
    if published:
        history = merge_history(history, published)
        log(f"  Revisado lo ya publicado en sus redes: {len(published)} publicaciones.")
    allowed = plan_conf.get("formatos") or ["reel", "historia", "post", "carrusel"]
    nets = {a.get("platform") for a in (user.get("social") or [])}
    if not (user.get("ig") or {}).get("connected") and "instagram" not in nets:
        allowed = [f for f in allowed if f == "reel"] or ["reel"]  # solo TikTok/YouTube: solo vídeo
    voices = {**VOICES, **conf.get("voces", {})}
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
    }
    init_js = "window.__AGENCIA_SERVER=true;try{localStorage.clear();" + "".join(
        f"localStorage.setItem({json.dumps(k)},{json.dumps(v)});" for k, v in init.items()) + "}catch(e){}"

    page = browser.new_page(viewport={"width": 480, "height": 900})
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
        log("  Pidiendo el plan a la IA…")
        page.click("#genBtn")
        page.wait_for_function("!document.querySelector('#genBtn').disabled", timeout=300_000)
        status = page.inner_text("#genStatus")
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
        log("  Estrategia:", page.evaluate("S.today.estrategia"))
        log(f"  {n} piezas. Generando imágenes…")
        page.evaluate("prefetchAll()")
        page.wait_for_function("typeof pendingA==='undefined' || pendingA.size===0", timeout=1_200_000, polling=2000)
        page.wait_for_timeout(1000)
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
                    if p.get("audio") in ("voz_y_musica", "solo_voz"):
                        try:
                            voice_clips, vname = make_voice(page, i, p, tdp, voices)
                            log(f"    voz: {vname} ({len(voice_clips)} frases)")
                        except Exception as e:
                            log("    ⚠ No se pudo generar la voz, va solo con música:", e)
                            page.evaluate("i=>{delete S.today.videos[i]._durs;delete S.today.videos[i]._caps;}", i)
                            voice_clips = []
                    music, track = (None, None) if p.get("audio") == "solo_voz" else pick_music(p.get("musica"), tdp)
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
                    encode_video(tdp, total, folder / "video.mp4", voice_clips, music, credit if p["tipo"] == "historia" else "", sfx_path)
                media.append(str((folder / "video.mp4").relative_to(DOCS)))
            else:
                for k in range(int(info["slides"])):
                    url = page.evaluate("a=>__agRender.slide(a[0],a[1])", [i, k])
                    path = folder / f"{k + 1:02d}.jpg"
                    path.write_bytes(base64.b64decode(url.split(",", 1)[1]))
                    media.append(str(path.relative_to(DOCS)))
            caption = (p.get("caption", "") + "\n\n" + p.get("hashtags", "")).strip() if p["tipo"] != "historia" else ""
            if caption and info["video"] and credit:
                caption += "\n\n" + credit
            when = publish_time(today, p.get("dia", 0), p.get("hora"), tz, i)
            if when < now + timedelta(minutes=15):  # la hora de hoy ya pasó: se publica en un rato
                when = now + timedelta(minutes=15 + 20 * i)
            if redo:
                when = datetime.fromisoformat(redo["publishAt"]).astimezone(tz)
                if when < now + timedelta(minutes=20):
                    when = now + timedelta(minutes=30)
            new_items.append({
                "id": pid, "date": today, "source": ("chat" if redo.get("brief") is not None else "redo") if redo else "auto", "tipo": p["tipo"], "tema": p.get("tema", ""),
                "publishAt": when.isoformat(),
                "caption": caption[:2200], "media": media, "status": "pending", "attempts": 0,
                **({"cover": str((folder / "cover.jpg").relative_to(DOCS))} if (folder / "cover.jpg").exists() else {}),
            })
        hist = page.evaluate("S.hist")
        estrategia = page.evaluate("S.today.estrategia")
    finally:
        page.close()

    # Lo que se sustituye: la pieza rehecha o, si se rehace la semana, todo lo que aún no se ha publicado
    if redo:
        replaced = [q for q in queue if q.get("id") == redo["id"]]
    elif force:
        replaced = [q for q in queue if q.get("status") in ("pending", "scheduled") and datetime.fromisoformat(q["publishAt"]) > now]
    else:
        replaced = [q for q in queue if q.get("date") == today and q.get("source") == "auto" and q.get("status") in ("pending", "scheduled")]
    for q in replaced:
        zernio_cancel(q)
    gone = {q["id"] for q in replaced}
    if user.get("social"):
        for it, piece in zip(new_items, pieces):
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
        gone |= {q["id"] for q in queue if q.get("date") == today and q.get("source") == "auto"}
    queue = [q for q in queue if q["id"] not in gone] + new_items
    queue.sort(key=lambda q: q["publishAt"])
    save_json(queue_path, queue)
    save_json(hist_path, hist)
    if not redo:
        save_json(udir / "plans" / f"{today}.json", {"date": today, "estrategia": estrategia, "piezas": pieces})
    try:
        api("PUT", f"/admin/status/{uid}", {"estrategia": "" if redo else estrategia, "queue": summarize(queue)})
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
    return bool((marca.get("name") or marca.get("about")) and ((u.get("ig") or {}).get("connected") or u.get("social")))


def process_requests(browser, users, key):
    """Peticiones hechas desde la app: reiniciar cuenta, rehacer la semana o rehacer piezas sueltas."""
    errors = 0
    for u in users:
        r = u.get("requests") or {}
        if not (r.get("reset") or r.get("replan") or r.get("redo") or r.get("create")):
            continue
        label = f"{u.get('name')} ({u['id']})"
        done = {}
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


def main():
    users = api("GET", "/admin/users").get("users", [])
    has_requests = any((u.get("requests") or {}) for u in users)
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
            errors += process_requests(browser, users, key)
            users = api("GET", "/admin/users").get("users", [])
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
                log(f"— {label}: sin redes conectadas, se salta.")
                continue
            if (conf.get("plan") or {}).get("auto") is False:
                log(f"— {label}: piloto automático en pausa.")
                continue
            log(f"▶ {label}")
            try:
                plan_user(browser, u, key)
            except Exception as e:
                errors += 1
                log(f"  ✗ Error con {label}:", e)
        browser.close()
    if errors:
        sys.exit(f"{errors} persona(s) con errores; revisa el registro.")


if __name__ == "__main__":
    main()
