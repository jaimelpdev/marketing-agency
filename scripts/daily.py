"""
Agencia de marketing IA · tarea diaria
--------------------------------------
1. Abre la web de la agencia (docs/index.html) en un Chrome sin ventana.
2. Le pide a Groq el plan del día (reels, historias, posts, carruseles) con tu marca.
3. Genera las imágenes de fondo (Pollinations) y renderiza cada pieza:
   reels/historias -> MP4 (H.264 + AAC), posts/carruseles -> JPG.
4. Guarda los archivos en docs/media/ (se publican en GitHub Pages con enlace público)
   y añade cada pieza a content/queue.json con su hora de publicación.

Variables de entorno:
  GROQ_API_KEY        (obligatoria)
  POLLINATIONS_TOKEN  (opcional, más rápido y sin marca de agua)
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

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CONTENT = ROOT / "content"
CONFIG = ROOT / "agency.config.json"
QUEUE = CONTENT / "queue.json"
HISTORY = CONTENT / "history.json"
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
    """Locución gratuita con las voces neuronales de Microsoft (edge-tts)."""
    import asyncio
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(out_path)))


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def pick_music(mood):
    pools = []
    if mood:
        pools.append([p for p in (MUSIC_DIR / mood).glob("*") if p.suffix.lower() in MUSIC_EXT])
    pools.append([p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in MUSIC_EXT])
    pools.append([p for p in MUSIC_DIR.rglob("*") if p.suffix.lower() in MUSIC_EXT])
    for pool in pools:
        if pool:
            return random.choice(pool)
    return None


def encode_video(frames_dir, total, out_path, voice_clips=(), music=None):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames_dir / "f_%05d.jpg")]
    for clip, _ in voice_clips:
        cmd += ["-i", str(clip)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    m_idx = len(voice_clips) + 1
    has_voice = bool(voice_clips)
    music_vol = 0.13 if has_voice else 0.85
    fade_start = max(0.0, total - 1.2)
    parts, labels = [], []
    for k, (_, start_s) in enumerate(voice_clips):
        ms = int(start_s * 1000)
        parts.append(f"[{k + 1}:a]aresample=48000,adelay={ms}:all=1,volume=1.15[v{k}]")
        labels.append(f"[v{k}]")
    parts.append(f"[{m_idx}:a]aresample=48000,volume={music_vol},afade=t=in:d=0.4,afade=t=out:st={fade_start:.2f}:d=1.2[mu]")
    labels.append("[mu]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,alimiter=limit=0.95[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]", "-t", f"{total:.3f}",
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
    clips, durs = [], []
    for k, line in enumerate(lines):
        if not line:
            clips.append(None)
            durs.append(0)
            continue
        out = workdir / f"voz_{k:02d}.mp3"
        tts(line, voice, rate, out)
        d = probe_duration(out)
        clips.append(out)
        durs.append(round(d + 0.55, 3))
    page.evaluate("a=>{S.today.videos[a[0]]._durs=a[1];}", [i, durs])
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
  slide(i, k){
    const p=S.today.videos[i]; const SC=buildScenes(p); const prev=ctx; ctx=this.canvas.getContext('2d'); STATIC=true;
    try{ drawFrame(SC,0,k); } finally{ STATIC=false; ctx=prev; }
    return this.canvas.toDataURL('image/jpeg',0.93);
  }
};
"""


def main():
    conf = load_json(CONFIG, None)
    if not conf:
        sys.exit("Falta agency.config.json en la raíz del repositorio.")
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        sys.exit("Falta el secreto GROQ_API_KEY.")
    plan_conf = conf.get("plan", {})
    tz = ZoneInfo(plan_conf.get("zona_horaria", "Europe/Madrid"))
    today = datetime.now(tz).date().isoformat()
    queue = load_json(QUEUE, [])
    modo = str(plan_conf.get("modo", "smart"))
    now = datetime.now(tz)
    if not os.environ.get("FORCE"):
        if any(q.get("date") == today and q.get("source") == "auto" for q in queue):
            log("Ya se planificó hoy. Usa FORCE=1 para rehacerlo.")
            return
        if modo == "smart":
            future = [q for q in queue if q.get("status") == "pending" and datetime.fromisoformat(q["publishAt"]) > now + timedelta(hours=24)]
            if future:
                log(f"Plan de la semana en marcha ({len(future)} piezas programadas más allá de mañana). Hoy no hace falta planificar.")
                return
    history = load_json(HISTORY, {"items": []})
    allowed = plan_conf.get("formatos", ["reel", "historia", "post", "carrusel"])
    voices = {**VOICES, **conf.get("voces", {})}

    cfg = dict(conf.get("marca", {}))
    cfg["v"] = dict(conf.get("visual", {}))
    if cfg["v"].get("bgMode") == "stock":
        cfg["v"]["bgMode"] = "ai"  # en el servidor solo usamos imágenes IA (no clips de stock)

    init = {
        "ma_cfg": json.dumps(cfg),
        "ma_groq_key": json.dumps(key),
        "ma_groq_model": json.dumps(conf.get("modelo", "openai/gpt-oss-120b")),
        "ma_hist": json.dumps(history),
        "ma_poll_token": json.dumps(os.environ.get("POLLINATIONS_TOKEN", "")),
    }
    init_js = "try{localStorage.clear();" + "".join(
        f"localStorage.setItem({json.dumps(k)},{json.dumps(v)});" for k, v in init.items()) + "}catch(e){}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
                                     executable_path=os.environ.get("CHROME_PATH") or None)
        page = browser.new_page(viewport={"width": 480, "height": 900})
        page.on("pageerror", lambda e: log("Error en la página:", e))
        install_routes(page)
        page.add_init_script(init_js)
        page.goto((DOCS / "index.html").as_uri())
        page.wait_for_function("typeof S!=='undefined' && typeof buildScenes==='function'")
        page.evaluate("ensureFonts()")
        page.wait_for_timeout(1500)

        # Sin clips de stock ni fotos propias en el servidor; formatos permitidos
        page.evaluate("""(allowed)=>{
          delete FONDOS.stock; delete FONDOS.mis;
          window.__allowed = allowed;
        }""", allowed)
        page.evaluate("a=>{S.count=a[0]; PLAN_LIMITS={maxDia:a[1],maxSemana:a[2]}; AUDIO_CAPS=true;}",
                      [modo, int(plan_conf.get("max_por_dia", 2)), int(plan_conf.get("max_semana", 7))])

        log("Pidiendo el plan del día a Groq…")
        page.click("#genBtn")
        page.wait_for_function("!document.querySelector('#genBtn').disabled", timeout=300_000)
        status = page.inner_text("#genStatus")
        n = page.evaluate("(S.today&&S.today.videos||[]).length")
        if "recomienda no publicar" in status:
            log(status)
            browser.close()
            return
        if not n or "No he podido" in status:
            browser.close()
            sys.exit("No se pudo crear el plan: " + status)
        log("Estrategia:", page.evaluate("S.today.estrategia"))

        # Ajustes para publicación automática: formatos permitidos y stickers (la API no los admite)
        page.evaluate("""()=>{
          S.today.videos = S.today.videos.filter(p=>window.__allowed.includes(p.tipo));
          S.today.videos.forEach(p=>{ if(p.tipo==='historia' && p.sticker){ p.cta = p.sticker.texto + ' · respóndeme por DM'; p.sticker=null; } });
        }""")
        n = page.evaluate("S.today.videos.length")
        log(f"{n} piezas en el plan.")

        log("Generando imágenes de fondo…")
        page.evaluate("prefetchAll()")
        page.wait_for_function("typeof pendingA==='undefined' || pendingA.size===0", timeout=1_200_000, polling=2000)
        page.wait_for_timeout(1000)
        page.evaluate(RENDER_JS)

        pieces = page.evaluate("JSON.parse(JSON.stringify(S.today.videos))")
        out_root = DOCS / "media" / today
        if out_root.exists():
            shutil.rmtree(out_root)
        out_root.mkdir(parents=True, exist_ok=True)
        new_items = []
        for i, p in enumerate(pieces):
            pid = f"{today}-{i + 1:02d}-{p['tipo']}"
            folder = out_root / pid
            folder.mkdir(parents=True, exist_ok=True)
            info = page.evaluate("i=>__agRender.info(i)", i)
            log(f"[{i + 1}/{n}] {p['tipo']} · {p.get('hora', '')} · {p.get('tema', '')}")
            media = []
            if info["video"]:
                with tempfile.TemporaryDirectory() as td:
                    tdp = Path(td)
                    voice_clips = []
                    if p.get("audio") == "voz_y_musica":
                        try:
                            voice_clips, vname = make_voice(page, i, p, tdp, voices)
                            log(f"  voz: {vname} ({len(voice_clips)} frases)")
                        except Exception as e:
                            log("  ⚠ No se pudo generar la voz, va solo con música:", e)
                            page.evaluate("i=>{delete S.today.videos[i]._durs;}", i)
                            voice_clips = []
                    music = pick_music(p.get("musica"))
                    log(f"  música: {music.relative_to(MUSIC_DIR) if music else 'ninguna (añade pistas en assets/music/)'} · ambiente {p.get('musica') or '-'}")
                    total = float(page.evaluate("i=>buildScenes(S.today.videos[i]).total", i))
                    count = int(total * FPS + 0.999)
                    f = 0
                    while f < count:
                        batch = page.evaluate("a=>__agRender.frames(a[0],a[1],a[2],a[3])", [i, f, 45, FPS])
                        if not batch:
                            break
                        write_dataurls(batch, tdp, f)
                        f += len(batch)
                    encode_video(tdp, total, folder / "video.mp4", voice_clips, music)
                media.append(str((folder / "video.mp4").relative_to(DOCS)))
            else:
                for k in range(int(info["slides"])):
                    url = page.evaluate("a=>__agRender.slide(a[0],a[1])", [i, k])
                    path = folder / f"{k + 1:02d}.jpg"
                    path.write_bytes(base64.b64decode(url.split(",", 1)[1]))
                    media.append(str(path.relative_to(DOCS)))
            caption = (p.get("caption", "") + "\n\n" + p.get("hashtags", "")).strip() if p["tipo"] != "historia" else ""
            new_items.append({
                "id": pid, "date": today, "source": "auto", "tipo": p["tipo"], "tema": p.get("tema", ""),
                "publishAt": publish_time(today, p.get("dia", 0), p.get("hora"), tz, i).isoformat(),
                "caption": caption[:2200], "media": media, "status": "pending", "attempts": 0,
            })
        hist = page.evaluate("S.hist")
        estrategia = page.evaluate("S.today.estrategia")
        browser.close()

    queue = [q for q in queue if not (q.get("date") == today and q.get("source") == "auto")] + new_items
    queue.sort(key=lambda q: q["publishAt"])
    save_json(QUEUE, queue)
    save_json(HISTORY, hist)
    save_json(CONTENT / "plans" / f"{today}.json", {"date": today, "estrategia": estrategia, "piezas": pieces})

    # Limpieza: borra media antigua ya publicada para no llenar el repositorio
    keep = int(plan_conf.get("dias_guardar_media", 10))
    limit = (datetime.now(tz) - timedelta(days=keep)).date().isoformat()
    busy = {m.split("/")[1] for q in queue if q.get("status") == "pending" for m in q.get("media", []) if m.count("/") >= 2}
    for d in (DOCS / "media").glob("*"):
        if d.is_dir() and d.name < limit and d.name not in busy:
            shutil.rmtree(d)
            log("Borrada media antigua:", d.name)
    log("Listo:", len(new_items), "piezas en la cola.")


if __name__ == "__main__":
    main()
