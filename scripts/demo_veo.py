"""Demo: reel faceless con clips de vídeo de Veo 3.1 (demo/veo/01-04.mp4) y la voz, los subtítulos y la música de la agencia.
Salida: docs/demo/veo-reel.mp4"""
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import daily  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "demo" / "veo"
OUT = ROOT / "docs" / "demo" / "veo-reel.mp4"
W, H, FPS = 1080, 1920, 30
YELLOW = "&H000AD6FF&"  # #FFD60A en el orden BGR de los subtítulos ASS
FONT_URL = "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf"


def ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def main():
    g = json.loads((SRC / "guion.json").read_text())
    clips = sorted(SRC.glob("0*.mp4"))
    assert len(clips) == len(g["frases"]), "un clip por frase"
    tmp = Path(tempfile.mkdtemp())
    fonts = tmp / "fonts"
    fonts.mkdir()
    font = "Montserrat ExtraBold"
    try:
        urllib.request.urlretrieve(FONT_URL, fonts / "Montserrat-ExtraBold.ttf")
    except Exception as e:  # noqa: BLE001
        print("sin Montserrat, uso DejaVu:", e)
        font = "DejaVu Sans"

    segs, t0 = [], 0.0
    for i, (clip, frase) in enumerate(zip(clips, g["frases"])):
        mp3 = tmp / f"v{i}.mp3"
        words = daily.tts(daily.clean_speech(frase), g["voz"], g["ritmo"], mp3)
        speech = daily.probe_duration(mp3)
        cd = daily.probe_duration(clip)
        L = max(cd, speech + 0.45)
        segs.append({"clip": clip, "mp3": mp3, "words": words, "start": t0, "cd": cd, "L": L})
        print(f"[{i + 1}] {clip.name}: clip {cd:.2f}s · voz {speech:.2f}s → tramo {L:.2f}s")
        t0 += L
    total = t0

    # subtítulos: bloques de hasta 3 palabras, centrados, la palabra que suena en amarillo y un poco más grande
    ev = []
    for s in segs:
        ws = [(s["start"] + 0.15 + a, d, w) for a, d, w in s["words"] if w.strip()]
        for k in range(0, len(ws), 3):
            chunk = ws[k:k + 3]
            for j, (a, d, w) in enumerate(chunk):
                end = chunk[j + 1][0] if j + 1 < len(chunk) else (ws[k + 3][0] if k + 3 < len(ws) else a + d + 0.25)
                txt = " ".join(("{\\c" + YELLOW + "\\fscx108\\fscy108}" + x.upper() + "{\\c&H00FFFFFF&\\fscx100\\fscy100}") if m == j else x.upper()
                               for m, (_, _, x) in enumerate(chunk))
                ev.append(f"Dialogue: 0,{ts(a)},{ts(end)},Cap,,0,0,0,,{{\\pos({W // 2},{int(H * 0.62)})}}{txt}")
    ass = tmp / "subs.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,{0 if font.startswith("Montserrat") else -1},0,0,0,100,100,1,0,1,9,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n", encoding="utf-8")

    try:
        music, meta = daily.pick_music(g.get("musica", "energetica"))
    except Exception as e:  # noqa: BLE001
        print("sin música:", e)
        music, meta = None, {}
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
        f = s["L"] / s["cd"]
        # el clip se estira un poco si la frase es más larga que él (nunca se congela la imagen)
        parts.append(f"[{i}:v]scale={W}:{H}:flags=lanczos,setsar=1,setpts=(PTS-STARTPTS)*{f:.4f},fps={FPS},trim=0:{s['L']:.3f},setpts=PTS-STARTPTS[c{i}]")
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
                 f"afade=t=in:d=0.6,afade=t=out:st={total - 1.2:.2f}:d=1.2[mu]")
    parts.append("[mu][vsc]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=350:makeup=1[mud]")
    parts.append("[amb]volume=0.12[ambl]")  # el sonido propio de los clips, muy bajo
    parts.append("[vox][mud][ambl]amix=inputs=3:normalize=0:duration=longest,alimiter=limit=0.84:attack=3:release=60:level=false[aout]")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(OUT)]
    subprocess.run(cmd, check=True)
    print(f"✓ {OUT} · {total:.1f}s · música: {meta.get('title', '')}")


if __name__ == "__main__":
    main()
