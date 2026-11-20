"""Locución con las voces de Gemini (gratis con GEMINI_API_KEY): suenan mucho más naturales que edge-tts.

Se pide TODO el guion en una sola llamada (así la entonación tiene sentido de principio a fin y se gasta una sola
petición del cupo gratis), con una pausa entre frases; luego se corta por los silencios en un audio por frase.
Gemini no da los tiempos de cada palabra: se reparten según la longitud de cada palabra (para los subtítulos).
Si Gemini falla, quien llama usa edge-tts como siempre."""
import base64
import os
import re
import subprocess
import time
from pathlib import Path

import requests

API = "https://generativelanguage.googleapis.com/v1beta"
# voces de Gemini por género: enérgicas pero naturales (se pueden cambiar con GEMINI_VOZ_HOMBRE / GEMINI_VOZ_MUJER)
VOCES = {"hombre": os.environ.get("GEMINI_VOZ_HOMBRE", "Puck"), "mujer": os.environ.get("GEMINI_VOZ_MUJER", "Zephyr")}
ESTILO = {
    "tranquilo": "con calma, cercano y seguro, como quien explica algo a un amigo",
    "normal": "cercano y con energía, como un creador de contenido que explica un truco útil a cámara",
    "energico": "con mucha energía y ritmo ágil, entusiasmado, como un creador de contenido que engancha desde la primera frase",
}
STATE = {"models": None, "off": False, "err": ""}


def _key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def models():
    if STATE["models"] is None:
        try:
            r = requests.get(f"{API}/models", params={"pageSize": 200}, headers={"x-goog-api-key": _key()}, timeout=30)
            r.raise_for_status()
            names = [m["name"].split("/")[-1] for m in r.json().get("models", []) if "tts" in m["name"].lower()]
            names.sort(key=lambda n: (("flash" not in n), ("preview" in n), n), reverse=False)
            STATE["models"] = names or ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]
        except Exception as e:  # noqa: BLE001
            STATE["err"] = str(e)[:200]
            STATE["models"] = ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]
    return STATE["models"]


def _pcm(text, voice, log):
    last = ""
    for model in models()[:3]:
        for intento in range(2):
            try:
                r = requests.post(f"{API}/models/{model}:generateContent", timeout=180,
                                  headers={"x-goog-api-key": _key(), "content-type": "application/json"},
                                  json={"contents": [{"parts": [{"text": text}]}],
                                        "generationConfig": {"responseModalities": ["AUDIO"],
                                                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}})
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"{model}: HTTP {r.status_code}"
                    time.sleep(8 + 8 * intento)
                    continue
                if r.status_code in (401, 403):
                    STATE["off"] = True
                    raise RuntimeError(f"clave rechazada ({r.status_code})")
                if r.status_code == 404:
                    last = f"{model}: no existe"
                    break
                r.raise_for_status()
                parts = r.json()["candidates"][0]["content"]["parts"]
                data = next(p["inlineData"]["data"] for p in parts if p.get("inlineData"))
                return base64.b64decode(data), model
            except (KeyError, IndexError, StopIteration) as e:
                last = f"{model}: respuesta sin audio ({e})"
                break
            except requests.RequestException as e:
                last = f"{model}: {str(e)[:120]}"
    raise RuntimeError(last or "sin respuesta")


def _silences(wav):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "silencedetect=noise=-38dB:d=0.28", "-f", "null", "-"],
                       capture_output=True, text=True)
    st, out = None, []
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            st = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and st is not None:
            out.append((st, float(m.group(1))))
            st = None
    return out


def _dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def _words(text, dur):
    ws = [w for w in re.split(r"\s+", text.strip()) if w]
    if not ws:
        return []
    weight = [len(re.sub(r"\W", "", w)) + 2 for w in ws]
    tot, t, out = sum(weight), 0.0, []
    for w, k in zip(ws, weight):
        d = dur * k / tot
        out.append((round(t, 3), round(d, 3), w))
        t += d
    return out


def _tempo(rate):
    try:
        pct = float(str(rate).replace("%", ""))
    except ValueError:
        pct = 0.0
    return max(0.92, min(1.12, 1 + pct / 100 * 0.6))  # Gemini ya habla a buen ritmo: el ajuste es más suave


def lines(texts, gender, ritmo, outdir, log=print):
    """Devuelve [(ruta_mp3, palabras)] (una por frase; None si la frase está vacía) o lanza excepción."""
    if not _key() or STATE["off"]:
        raise RuntimeError("Gemini no disponible")
    idx = [k for k, t in enumerate(texts) if str(t).strip()]
    if not idx:
        return [None] * len(texts)
    voice = VOCES.get(gender, VOCES["mujer"])
    guion = "\n".join(texts[k].strip() for k in idx)
    prompt = ("Lee en voz alta este guion de un vídeo corto de Instagram, en español de España (acento castellano peninsular, "
              f"tuteando), {ESTILO.get(ritmo, ESTILO['normal'])}. Entonación natural, nada robótica, sin exagerar. "
              "Haz una pausa clara de un segundo al final de cada línea. Lee solo el texto, sin añadir nada.\n\n" + guion)
    pcm, model = _pcm(prompt, voice, log)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    wav = outdir / "gemini_full.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "-", str(wav)], input=pcm, check=True)
    total = _dur(wav)
    n = len(idx)
    sil = [s for s in _silences(wav) if s[0] > 0.2 and s[1] < total - 0.1]
    cuts = sorted(sorted(sil, key=lambda s: s[1] - s[0], reverse=True)[:n - 1])
    if len(cuts) < n - 1:
        raise RuntimeError(f"no se pudieron separar las {n} frases ({len(cuts) + 1} trozos)")
    bounds = [0.0] + [(a + b) / 2 for a, b in cuts] + [total]
    tempo = _tempo({"tranquilo": "-4%", "normal": "+8%", "energico": "+16%"}.get(ritmo, "+8%"))
    res = [None] * len(texts)
    for j, k in enumerate(idx):
        mp3 = outdir / f"gem_{k:02d}.mp3"
        af = ("silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.05,"
              "areverse,silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.08,areverse,"
              f"atempo={tempo:.3f}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{bounds[j]:.3f}", "-to", f"{bounds[j + 1]:.3f}", "-i", str(wav),
                        "-af", af, "-ar", "44100", "-ac", "1", "-b:a", "160k", str(mp3)], check=True)
        res[k] = (mp3, _words(texts[k], _dur(mp3)))
    log(f"    🎙 Voz de Gemini «{voice}» ({model}): {n} frases en una sola toma")
    return res
