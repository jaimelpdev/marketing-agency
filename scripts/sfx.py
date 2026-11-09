"""Efectos de sonido cortos (whoosh, pop, ding, golpe) sincronizados con las animaciones del vídeo."""
import wave

import numpy as np

SR = 48000


def _env(n, attack, decay):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / decay)
    return e


def whoosh(rng):
    n = int(0.42 * SR)
    noise = rng.standard_normal(n)
    # barrido de un filtro paso banda muy simple (dos filtros de un polo)
    t = np.linspace(0, 1, n)
    cut = 300 + 5200 * np.sin(np.pi * t) ** 1.5
    out = np.zeros(n)
    lp = hp = 0.0
    for i in range(n):
        a = np.exp(-2 * np.pi * cut[i] / SR)
        lp = (1 - a) * noise[i] + a * lp
        hp = 0.92 * (hp + lp - (out[i - 1] if i else 0))
        out[i] = lp
    out = out - np.convolve(out, np.ones(24) / 24, mode="same")
    return out * np.sin(np.pi * t) ** 2 * 3.2


def pop(rng):
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    f = 950 * np.exp(-t * 38) + 180
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 32)
    x[:120] += rng.standard_normal(120) * np.linspace(0.5, 0, 120)
    return x * 0.9


def ding(rng):
    n = int(0.7 * SR)
    t = np.arange(n) / SR
    f = 1568
    x = (np.sin(2 * np.pi * f * t) + 0.45 * np.sin(2 * np.pi * f * 2.01 * t) * np.exp(-t * 6) + 0.25 * np.sin(2 * np.pi * f * 3.02 * t) * np.exp(-t * 9))
    return x * _env(n, 0.003, 0.22) * 0.45


def thud(rng):
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    f = 40 + 120 * np.exp(-t * 22)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 11)
    x[:600] += rng.standard_normal(600) * np.linspace(0.6, 0, 600)
    return x * 1.1


KINDS = {"whoosh": whoosh, "pop": pop, "ding": ding, "thud": thud}


def track(events, total, out_path, seed=7):
    """Crea una pista WAV con los efectos colocados en sus tiempos. Devuelve la ruta o None si no hay eventos."""
    if not events:
        return None
    rng = np.random.default_rng(seed)
    cache = {k: f(rng) for k, f in KINDS.items()}
    n = int((total + 1) * SR)
    mix = np.zeros(n)
    for ev in events:
        snd = cache.get(ev.get("k"))
        if snd is None:
            continue
        s = int(max(0.0, float(ev.get("t", 0))) * SR)
        e = min(n, s + len(snd))
        if s < n:
            mix[s:e] += snd[: e - s]
    peak = np.max(np.abs(mix)) or 1.0
    mix = np.tanh(mix / peak * 1.2) * 0.8
    data = (np.stack([mix, mix], axis=1) * 32767).astype("<i2")
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return out_path
