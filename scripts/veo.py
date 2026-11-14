"""Modo Veo (gratis): la agencia prepara los clips que hay que generar en Google Flow (Veo 3.1 Lite),
la persona los sube desde la app y aquí se montan con la voz, los subtítulos y la música de la agencia."""
import math
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
    clips = []
    for g in groups:
        if not g:
            continue
        text = " ".join(str(b["t"]).strip() for b in g)
        est = len(text.split()) / WORDS_PER_S + 0.5
        dur = 4 if est <= 4.2 else 6 if est <= 6.2 else 8
        img = str(g[0].get("img") or g[0]["t"]).strip().rstrip(".")
        if len(g) > 1 and g[-1].get("img"):
            # la segunda frase del grupo aporta la acción final del clip
            last = str(g[-1]["img"]).split(". ")[-1].strip().rstrip(".")
            img += f". Then: {last}"
        clips.append({"t": text, "prompt": f"{img}. {TAIL}"[:1400], "dur": dur})
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


def compose(segments, voice, rate, mood, out, log=print, music_free_only=False):
    """segments: [{"clip": ruta mp4, "t": frase}]. Monta un vídeo vertical 1080x1920 con voz, subtítulos y música."""
    import daily  # aquí para evitar la importación circular
    tmp = Path(tempfile.mkdtemp())
    fonts, font, bold = _font(tmp)
    segs, t0 = [], 0.0
    for i, s in enumerate(segments):
        mp3 = tmp / f"v{i}.mp3"
        words = daily.tts(daily.clean_speech(s["t"]), voice, rate, mp3, fallback="es-ES-ElviraNeural")
        speech = daily.probe_duration(mp3)
        cd = daily.probe_duration(s["clip"])
        L = speech + 0.45  # cada clip dura lo que su frase: ni huecos ni cortes
        segs.append({"clip": s["clip"], "mp3": mp3, "words": words, "start": t0, "cd": cd, "L": L})
        t0 += L
    total = t0

    ev = []
    for s in segs:
        ws = [(s["start"] + 0.15 + a, d, w) for a, d, w in s["words"] if w.strip()]
        for k in range(0, len(ws), 3):
            chunk = ws[k:k + 3]
            for j, (a, d, w) in enumerate(chunk):
                end = chunk[j + 1][0] if j + 1 < len(chunk) else (ws[k + 3][0] if k + 3 < len(ws) else a + d + 0.25)
                txt = " ".join(("{\\c" + YELLOW + "\\fscx108\\fscy108}" + x.upper() + "{\\c&H00FFFFFF&\\fscx100\\fscy100}") if m == j else x.upper()
                               for m, (_, _, x) in enumerate(chunk))
                ev.append(f"Dialogue: 0,{_ts(a)},{_ts(end)},Cap,,0,0,0,,{{\\pos({W // 2},{int(H * 0.62)})}}{txt}")
    ass = tmp / "subs.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,{bold},0,0,0,100,100,1,0,1,9,3,5,60,60,0,1

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
    parts, cat = [], ""
    for i, s in enumerate(segs):
        # si la frase es más larga que el clip, se ralentiza un poco (hasta 1,35x) y, si aún falta, se congela el final
        f = min(1.35, max(1.0, s["L"] / s["cd"]))
        parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1,"
                     f"setpts=(PTS-STARTPTS)*{f:.4f},fps={FPS},tpad=stop_mode=clone:stop_duration=10,trim=0:{s['L']:.3f},setpts=PTS-STARTPTS[c{i}]")
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
    parts.append("[vox][mud][ambl]amix=inputs=3:normalize=0:duration=longest,alimiter=limit=0.84:attack=3:release=60:level=false[aout]")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    # portada: un fotograma del primer clip con la primera frase
    cover = Path(out).with_name("cover.jpg")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.6", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(cover)], check=False)
    return total, meta
