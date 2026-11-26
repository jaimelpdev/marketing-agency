"""Modo Veo (gratis): la agencia prepara los clips que hay que generar en Google Flow (Veo 3.1 Lite),
la persona los sube desde la app y aquí se montan con la voz, los subtítulos y la música de la agencia."""
import math
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
YELLOW = "&H000AD6FF&"  # #FFD60A en el orden BGR de los subtítulos ASS
FONT_URL = "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf"
MAX_CLIPS = {"reel": 5, "historia": 3}  # 50 créditos gratis al día en Flow = 5 clips de Veo 3.1 Lite
TAIL = ("smooth natural camera movement, vertical 9:16 video, photorealistic, bright natural light, sharp focus, "
        "no text, no captions, no logos, no brand names, no watermark")
WORDS_PER_S = 2.7


def _action(b):
    """La acción de una imagen del director de arte, sin el escenario ni la lista de qué evitar."""
    im = str(b.get("img") or "").strip()
    im = re.split(r"\.\s*Setting:", im)[0]
    im = re.split(r";\s*the person is", im)[0]
    return im.strip().rstrip(".")


def _setting(b):
    """El escenario (sin la lista de qué evitar ni recortes a medias)."""
    m = re.search(r"Setting:\s*(.+?)(?:\.\s|\.$|$)", str(b.get("img") or ""))
    st = m.group(1).strip().rstrip(".") if m else ""
    parts = [x.strip() for x in st.split(",")]
    if len(parts) > 1 and len(parts[-1].split()[-1]) <= 3 and len(parts[-1]) < 20:  # trozo cortado al final ("not a real ca")
        st = ", ".join(parts[:-1])
    return st


def _avoid(b):
    m = re.search(r"Setting:\s*.+?\.\s+(.+)$", str(b.get("img") or ""))
    avoid = m.group(1).strip().rstrip(".") if m else ""
    words = avoid.split()
    if words and len(words[-1]) <= 2:  # palabra cortada al final ("no r")
        avoid = " ".join(words[:-1]).rstrip(",")
    return avoid


def _persona(piece, beats):
    if piece.get("persona"):
        return str(piece["persona"]).strip().rstrip(".")
    for b in beats:
        m = re.search(r";\s*the person is\s*(.+?)(?:\.\s*Setting:|$)", str(b.get("img") or ""))
        if m:
            return m.group(1).strip().rstrip(".")
    return ""


PERSON = re.compile(r"\b(person|man|woman|gamer|driver|he|she|his|her|hands?|feet|foot|face|racer|player|guy|someone)\b", re.I)


def plan_clips(piece):
    """Agrupa las frases del guion en pocos clips (lo que da el cupo gratis) y escribe la descripción de cada uno."""
    beats = [b for b in (piece.get("beats") or []) if str(b.get("t", "")).strip()]
    if len(beats) < 2:  # sin frases troceadas: el guion, con las imágenes del gancho y de las escenas
        imgs = [piece.get("visual")] + [e.get("visual") for e in piece.get("escenas") or []] + [piece.get("visual_cierre")]
        imgs = [str(x) for x in imgs if x]
        beats = [{"t": str(g), "img": imgs[min(k, len(imgs) - 1)] if imgs else str(g)} for k, g in enumerate(piece.get("guion") or []) if str(g).strip()]
    if len(beats) < 2:
        return None
    n = min(MAX_CLIPS.get(piece.get("tipo"), 4), max(2, math.ceil(len(beats) / 2)))
    size = len(beats) / n
    groups = [beats[round(k * size):round((k + 1) * size)] for k in range(n)]
    persona = _persona(piece, beats)
    setting = next((_setting(b) for b in beats if _setting(b)), "")
    avoid = next((_avoid(b) for b in beats if _avoid(b)), "")
    clips = []
    for g in groups:
        if not g:
            continue
        text = " ".join(str(b["t"]).strip() for b in g)
        est = len(text.split()) / WORDS_PER_S + 0.5
        dur = 4 if est <= 4.2 else 6 if est <= 6.2 else 8
        acts = [_action(b) or str(b["t"]) for b in g]
        if len(acts) == 1:
            img = acts[0]
        else:
            # un plano por frase, con su tramo de tiempo según lo que dura cada frase: lo que se ve cambia cuando cambia lo que se dice
            ws = [max(1, len(str(b["t"]).split())) for b in g]
            tot, t0, shots = sum(ws), 0.0, []
            for k, (a, w) in enumerate(zip(acts, ws)):
                t1 = dur if k == len(acts) - 1 else round(t0 + dur * w / tot)
                shots.append(f"Shot {k + 1} ({t0:.0f}-{t1:.0f} s): {a}")
                t0 = t1
            img = f"{len(acts)} shots with hard cuts between them. " + ". ".join(shots)
        # la misma persona y el mismo sitio, con las mismas palabras, en todos los clips: así Veo no cambia de protagonista
        who = f". The person is always the same: {persona}" if persona and PERSON.search(img) else ""
        prompt = img + who + (". Setting: " + setting if setting else "") + (". Avoid: " + avoid if avoid else "") + ". " + TAIL
        clips.append({"t": text, "prompt": prompt[:1400], "dur": dur})
    return clips


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _font(tmp):
    fonts = tmp / "fonts"
    fonts.mkdir(exist_ok=True)
    try:
        urllib.request.urlretrieve(FONT_URL, fonts / "Montserrat-ExtraBold.ttf")
        return fonts, "Montserrat ExtraBold", 0
    except Exception:  # noqa: BLE001
        return fonts, "DejaVu Sans", -1


def _subcuts(s, every=2.4):
    """Puntos (en segundos desde el inicio del tramo) donde meter un corte de encuadre: en el arranque de una palabra."""
    starts = [0.15 + a for a, d, w in s["words"] if str(w).strip()]
    cuts, last = [], 0.0
    for t in starts:
        if t - last >= every and s["L"] - t >= 1.2:
            cuts.append(round(t - 0.05, 3))
            last = t
    return cuts


def _sfx(tmp):
    """Efectos sintetizados (sin licencias): un «whoosh» para los cambios de plano y un «pop» para el título."""
    wh, pop = tmp / "whoosh.wav", tmp / "pop.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.7:d=0.5:r=48000",
                    "-af", "highpass=f=500,lowpass=f=6000,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.2,volume=0.55",
                    "-ac", "2", str(wh)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=660:d=0.12:r=48000",
                    "-af", "afade=t=out:st=0.02:d=0.1,volume=0.35", "-ac", "2", str(pop)], check=True)
    return wh, pop


def compose(segments, voice, rate, mood, out, log=print, music_free_only=False, hook=""):
    """segments: [{"clip": ruta mp4, "t": frase}]. Monta un vídeo vertical 1080x1920 con voz, subtítulos y música.
    Montaje: un cambio de encuadre (plano normal / acercado) cada ~2,4 s, título del gancho al principio y efectos
    de sonido en los cambios de clip."""
    import daily  # aquí para evitar la importación circular
    tmp = Path(tempfile.mkdtemp())
    fonts, font, bold = _font(tmp)
    segs, t0 = [], 0.0
    texts = [daily.clean_speech(s["t"]) for s in segments]
    gem = None
    if os.environ.get("VOICE_ENGINE", "gemini") != "edge":
        try:
            import voz
            gender = "mujer" if any(x in str(voice) for x in ("Elvira", "Ximena", "Dalia", "Ava", "Emma", "mujer")) else "hombre"
            ritmo = {"-4%": "tranquilo", "+16%": "energico"}.get(str(rate), "normal")
            gem = voz.lines(texts, gender, ritmo, tmp, log)
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ Voz de Gemini no disponible ({str(e)[:120]}); uso edge-tts")
            gem = None
    for i, s in enumerate(segments):
        mp3 = tmp / f"v{i}.mp3"
        if gem is not None and gem[i]:
            mp3, words = gem[i]
        else:
            words = daily.tts(texts[i], voice, rate, mp3, fallback="es-ES-ElviraNeural")
        speech = daily.probe_duration(mp3)
        cd = daily.probe_duration(s["clip"])
        L = speech + 0.45  # cada clip dura lo que su frase: ni huecos ni cortes
        segs.append({"clip": s["clip"], "mp3": mp3, "words": words, "start": t0, "cd": cd, "L": L})
        t0 += L
    total = t0

    ev = []
    for s in segs:
        ws = [(s["start"] + 0.15 + a, d, w) for a, d, w in s["words"] if w.strip()]
        # trozos de hasta 3 palabras y ~16 letras: así nunca se salen de la pantalla («NEUMÁTICOS TRASEROS. NOTAR» se cortaba)
        groups, cur = [], []
        for x in ws:
            if cur and (len(cur) >= 3 or len(" ".join(c[2] for c in cur + [x])) > 16):
                groups.append(cur)
                cur = []
            cur.append(x)
        if cur:
            groups.append(cur)
        for gi, chunk in enumerate(groups):
            nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else None
            for j, (a, d, w) in enumerate(chunk):
                end = chunk[j + 1][0] if j + 1 < len(chunk) else (nxt if nxt is not None else a + d + 0.25)
                txt = " ".join(("{\\c" + YELLOW + "\\fscx108\\fscy108}" + x.upper() + "{\\c&H00FFFFFF&\\fscx100\\fscy100}") if m == j else x.upper()
                               for m, (_, _, x) in enumerate(chunk))
                ev.append(f"Dialogue: 0,{_ts(a)},{_ts(end)},Cap,,0,0,0,,{{\\pos({W // 2},{int(H * 0.62)})}}{txt}")
    hook = re.sub(r"\s+", " ", str(hook or "")).strip()
    if len(hook.split()) > 9:  # gancho largo: su primera parte, si se entiende sola
        first = re.split(r"(?<=[?!.,:])\s+", hook)[0].strip(" ,:")
        hook = first if 2 <= len(first.split()) <= 9 else ""
    if hook and len(hook.split()) <= 9:
        h = hook.upper().replace("{", "(").replace("}", ")")
        if len(h) > 16:  # en dos líneas, partido por la palabra más cercana a la mitad
            ws = h.split()
            cut = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(h) / 2))
            h = " ".join(ws[:cut]) + "\\N" + " ".join(ws[cut:])
        hend = min(2.6, segs[0]["L"] if segs else 2.6)
        ev.append(f"Dialogue: 1,{_ts(0)},{_ts(hend)},Hook,,0,0,0,,{{\\pos({W // 2},{int(H * 0.2)})\\fad(0,180)\\fscx70\\fscy70\\t(0,160,\\fscx108\\fscy108)\\t(160,260,\\fscx100\\fscy100)}}{h}")
    ass = tmp / "subs.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,{bold},0,0,0,100,100,1,0,1,9,3,5,60,60,0,1
Style: Hook,{font},86,&H00000000,&H00000000,&H000AD6FF,&H000AD6FF,{bold},0,0,0,100,100,1,0,3,22,0,5,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n", encoding="utf-8")

    music, meta = None, {}
    if mood is not False:
        try:
            music, meta = daily.pick_music(mood, tmp, free_only=music_free_only)
        except Exception as e:  # noqa: BLE001
            log("    ⚠ sin música:", e)
        meta = meta or {}
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for s in segs:
        cmd += ["-i", str(s["clip"])]
    for s in segs:
        cmd += ["-i", str(s["mp3"])]
    cmd += (["-stream_loop", "-1", "-i", str(music)] if music else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
    n = len(segs)
    try:
        wh, pop = _sfx(tmp)
        cmd += ["-i", str(wh), "-i", str(pop)]
        fx = True
    except Exception as e:  # noqa: BLE001
        log("    ⚠ sin efectos de sonido:", e)
        fx = False
    parts, cat = [], ""
    for i, s in enumerate(segs):
        # si la frase es más larga que el clip, se ralentiza un poco (hasta 1,35x) y, si aún falta, se congela el final
        f = min(1.35, max(0.8, s["L"] / s["cd"]))  # el clip se ajusta a lo que dura la frase: sus planos caen con lo que se dice
        parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,"
                     f"setpts=(PTS-STARTPTS)*{f:.4f},fps={FPS},tpad=stop_mode=clone:stop_duration=10,trim=0:{s['L']:.3f},setpts=PTS-STARTPTS[r{i}]")
        cuts = _subcuts(s)
        if cuts:  # cortes de encuadre: plano normal y acercado (1,15x, centrado un poco arriba, donde suele estar la cara)
            b = [0.0] + cuts + [s["L"]]
            k = len(b) - 1
            parts.append(f"[r{i}]split={k}" + "".join(f"[r{i}s{j}]" for j in range(k)))
            for j in range(k):
                zoom = ",scale=iw*1.15:ih*1.15:flags=lanczos,crop={W}:{H}:(iw-{W})/2:(ih-{H})*0.38".format(W=W, H=H) if j % 2 else ""
                parts.append(f"[r{i}s{j}]trim={b[j]:.3f}:{b[j + 1]:.3f},setpts=PTS-STARTPTS{zoom},setsar=1[r{i}p{j}]")
            parts.append("".join(f"[r{i}p{j}]" for j in range(k)) + f"concat=n={k}:v=1:a=0[c{i}]")
        else:
            parts.append(f"[r{i}]null[c{i}]")
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,atempo={1 / f:.4f},apad,atrim=0:{s['L']:.3f},asetpts=PTS-STARTPTS[o{i}]")
        cat += f"[c{i}][o{i}]"
    parts.append(f"{cat}concat=n={n}:v=1:a=1[vc][amb]")
    sub = str(ass).replace(":", "\\:")
    parts.append(f"[vc]subtitles='{sub}':fontsdir='{fonts}'[vout]")
    for i, s in enumerate(segs):
        ms = int((s["start"] + 0.15) * 1000)
        parts.append(f"[{n + i}:a]aresample=48000,aformat=channel_layouts=stereo,{daily.VOICE_FX},adelay={ms}:all=1,volume=1.1[v{i}]")
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0:duration=longest,asplit=2[vox][vsc]")
    norm = "loudnorm=I=-18:TP=-2:LRA=9,aresample=48000," if music else ""
    parts.append(f"[{2 * n}:a]aresample=48000,aformat=channel_layouts=stereo,{norm}volume=0.3,"
                 f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2[mu]")
    parts.append("[mu][vsc]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=350:makeup=1[mud]")
    parts.append("[amb]volume=0.12[ambl]")  # el sonido propio de los clips, muy bajo
    mixin = "[vox][mud][ambl]"
    if fx:
        times = [max(0.0, s["start"] - 0.28) for s in segs[1:]]  # el whoosh termina justo en el corte
        if times:
            parts.append(f"[{2 * n + 1}:a]asplit={len(times)}" + "".join(f"[w{j}]" for j in range(len(times))))
            for j, t in enumerate(times):
                parts.append(f"[w{j}]adelay={int(t * 1000)}:all=1[wd{j}]")
            parts.append("".join(f"[wd{j}]" for j in range(len(times))) + f"amix=inputs={len(times)}:normalize=0[whs]")
            mixin += "[whs]"
        if hook and "Hook," in "\n".join(ev):
            parts.append(f"[{2 * n + 2}:a]adelay=40:all=1[pp]")
            mixin += "[pp]"
    k_in = mixin.count("[")
    parts.append(f"{mixin}amix=inputs={k_in}:normalize=0:duration=longest,alimiter=limit=0.84:attack=3:release=60:level=false[aout]")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    # portada: un fotograma del primer clip con la primera frase
    cover = Path(out).with_name("cover.jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.6", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(cover)], check=False)
    return total, meta
