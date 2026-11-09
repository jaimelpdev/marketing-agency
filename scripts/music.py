"""Música original generada al vuelo (sin derechos de autor) para cada ambiente.

Cada pista es distinta (semilla aleatoria) pero mantiene el carácter del ambiente:
energetica, chill, epica, emotiva, divertida y tension.
Uso: python scripts/music.py energetica salida.wav [segundos]
"""
import sys
import wave

import numpy as np

SR = 44100
NOTES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

# Progresiones en grados (semitonos desde la tónica) y calidad del acorde
MAJ, MIN, MAJ7, MIN7, SUS = (0, 4, 7), (0, 3, 7), (0, 4, 7, 11), (0, 3, 7, 10), (0, 5, 7)
MOODS = {
    "energetica": dict(bpm=(120, 128), prog=[[(0, MIN), (8, MAJ), (3, MAJ), (10, MAJ)], [(0, MIN), (5, MIN), (8, MAJ), (10, MAJ)]],
                       roots=["A", "F#", "G", "E"], drums="four", arp=True, pad=0.18, bass="pulse", lead="pluck"),
    "chill": dict(bpm=(78, 90), prog=[[(0, MAJ7), (9, MIN7), (5, MAJ7), (7, MAJ)], [(2, MIN7), (7, MAJ), (0, MAJ7), (9, MIN7)]],
                  roots=["C", "D", "F", "G"], drums="lofi", arp=False, pad=0.0, bass="soft", lead="keys"),
    "epica": dict(bpm=(86, 96), prog=[[(0, MIN), (8, MAJ), (3, MAJ), (10, MAJ)], [(0, MIN), (10, MAJ), (8, MAJ), (7, MAJ)]],
                  roots=["D", "C", "E", "A"], drums="epic", arp=True, pad=0.32, bass="long", lead="strings"),
    "emotiva": dict(bpm=(66, 76), prog=[[(0, MAJ), (7, MAJ), (9, MIN), (5, MAJ)], [(9, MIN), (5, MAJ), (0, MAJ), (7, SUS)]],
                    roots=["C", "G", "D", "F"], drums="none", arp=False, pad=0.22, bass="long", lead="piano"),
    "divertida": dict(bpm=(112, 122), prog=[[(0, MAJ), (7, MAJ), (9, MIN), (5, MAJ)], [(0, MAJ), (5, MAJ), (7, MAJ), (5, MAJ)]],
                      roots=["C", "D", "F", "G"], drums="bouncy", arp=True, pad=0.0, bass="bounce", lead="square"),
    "tension": dict(bpm=(96, 106), prog=[[(0, MIN), (1, MAJ), (0, MIN), (6, MIN)], [(0, MIN), (8, MAJ), (1, MAJ), (0, MIN)]],
                    roots=["E", "F", "C#", "D"], drums="tick", arp=False, pad=0.28, bass="drive", lead="none"),
}


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def env(n, a=0.005, d=0.1, s=0.6, r=0.1):
    """Envolvente ADSR de n muestras."""
    a_n, d_n, r_n = int(a * SR), int(d * SR), int(r * SR)
    s_n = max(0, n - a_n - d_n - r_n)
    e = np.concatenate([np.linspace(0, 1, max(a_n, 1)), np.linspace(1, s, max(d_n, 1)), np.full(s_n, s), np.linspace(s, 0, max(r_n, 1))])
    return e[:n] if len(e) >= n else np.pad(e, (0, n - len(e)))


def lowpass(x, cutoff):
    """Filtro paso bajo de un polo (suficiente para suavizar)."""
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    # vectorizado por bloques con scipy no disponible: bucle simple pero rápido con numpy cumsum aproximado
    b = 1 - a
    for i in range(len(x)):
        acc = b * x[i] + a * acc
        y[i] = acc
    return y


def osc(kind, f, n, detune=0.0):
    t = np.arange(n) / SR
    ph = 2 * np.pi * f * (1 + detune) * t
    if kind == "sine":
        return np.sin(ph)
    if kind == "saw":
        return 2 * ((f * (1 + detune) * t) % 1.0) - 1
    if kind == "square":
        return np.sign(np.sin(ph)) * 0.7
    if kind == "tri":
        return 2 * np.abs(2 * ((f * t) % 1.0) - 1) - 1
    raise ValueError(kind)


def add(buf, start, sig):
    s = int(start)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(sig))
    buf[s:e] += sig[: e - s]


def instrument(name, midi, dur, vel=1.0):
    n = int(dur * SR)
    f = hz(midi)
    if name == "pluck":
        x = osc("saw", f, n) * 0.5 + osc("saw", f, n, 0.006) * 0.5
        return x * env(n, 0.002, 0.18, 0.0, 0.05) * vel
    if name == "keys":  # piano eléctrico suave con trémolo
        t = np.arange(n) / SR
        x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) * np.exp(-t * 6)
        return x * (1 + 0.15 * np.sin(2 * np.pi * 5 * t)) * env(n, 0.01, 0.6, 0.35, 0.3) * vel * 0.5
    if name == "piano":
        t = np.arange(n) / SR
        x = sum(np.sin(2 * np.pi * f * h * t) * (0.6 ** (h - 1)) * np.exp(-t * (1.5 + h)) for h in range(1, 6))
        return x * env(n, 0.003, 0.05, 1.0, 0.25) * vel * 0.45
    if name == "square":
        return osc("square", f, n) * env(n, 0.003, 0.12, 0.25, 0.05) * vel * 0.35
    if name == "strings":
        x = osc("saw", f, n) + osc("saw", f, n, 0.004) + osc("saw", f, n, -0.004)
        return x / 3 * env(n, 0.02, 0.1, 0.7, 0.08) * vel * 0.5
    if name == "pad":
        x = osc("saw", f, n, 0.003) + osc("saw", f, n, -0.003) + 0.5 * osc("sine", f / 2, n)
        return x / 2.5 * env(n, dur * 0.3, 0.2, 0.85, dur * 0.3)
    if name == "bass":
        x = osc("saw", f, n) * 0.4 + osc("sine", f, n) * 0.8
        return x * env(n, 0.004, 0.15, 0.6, 0.06) * vel
    raise ValueError(name)


def kick(n=int(0.4 * SR)):
    t = np.arange(n) / SR
    f = 50 + 110 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)


def snare(rng, n=int(0.25 * SR)):
    t = np.arange(n) / SR
    return (rng.standard_normal(n) * 0.55 + np.sin(2 * np.pi * 190 * t) * 0.4) * np.exp(-t * 18)


def hat(rng, n=int(0.06 * SR), open_=False):
    if open_:
        n = int(0.22 * SR)
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    x = np.diff(np.concatenate([[0], x]))  # paso alto sencillo
    return x * np.exp(-t * (14 if open_ else 60)) * 0.25


def tom(n=int(0.6 * SR), f0=95):
    t = np.arange(n) / SR
    f = f0 + 60 * np.exp(-t * 12)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)


def render(mood="energetica", seconds=32.0, seed=None):
    rng = np.random.default_rng(seed)
    M = MOODS.get(mood) or MOODS["energetica"]
    bpm = float(rng.uniform(*M["bpm"]))
    beat = 60.0 / bpm
    bar = 4 * beat
    root = 48 + NOTES[str(rng.choice(M["roots"]))]
    prog = M["prog"][int(rng.integers(len(M["prog"])))]
    n = int(seconds * SR)
    mus, bas, drm = np.zeros(n), np.zeros(n), np.zeros(n)
    bars = int(np.ceil(seconds / bar))
    k_s, s_s = kick(), snare(rng)
    for b in range(bars):
        deg, qual = prog[b % len(prog)]
        chord = [root + deg + iv for iv in qual]
        t0 = b * bar * SR
        intro = b == 0
        # Pad / acordes
        if M["pad"]:
            for nt in chord:
                add(mus, t0, instrument("pad", nt + 12, bar + 0.05) * M["pad"])
        lead = M["lead"]
        if lead == "keys":
            for hit in (0, 2.5):
                for nt in chord:
                    add(mus, t0 + hit * beat * SR, instrument("keys", nt + 12, beat * 1.6, 0.6))
        elif lead == "piano":
            pat = [0, 1, 2, 1, 2, 1, 2, 1] if len(chord) >= 3 else [0, 1, 2, 1]
            for i, idx in enumerate(pat):
                add(mus, t0 + i * beat / 2 * SR, instrument("piano", chord[idx % len(chord)] + 12 + (12 if i == 4 else 0), beat * 1.5, 0.8))
            add(mus, t0, instrument("piano", chord[0], bar, 0.6))
        elif lead == "strings":
            for i in range(8):
                for nt in chord:
                    add(mus, t0 + i * beat / 2 * SR, instrument("strings", nt + 12, beat * 0.42, 0.5 if i % 2 else 0.8))
        elif lead == "square":
            for i in range(8):
                if rng.random() < 0.7:
                    nt = chord[int(rng.integers(len(chord)))] + 24
                    add(mus, t0 + i * beat / 2 * SR, instrument("square", nt, beat * 0.4, 0.8))
        if M["arp"] and not intro:
            step = beat / 4 if mood == "energetica" else beat / 2
            seq = chord + [chord[0] + 12]
            for i in range(int(bar / step)):
                add(mus, t0 + i * step * SR, instrument("pluck", seq[i % len(seq)] + 24, step * 0.9, 0.35))
        # Bajo
        bstyle, bn = M["bass"], chord[0] - 12
        if bstyle == "pulse":
            for i in range(8):
                add(bas, t0 + (i * beat / 2) * SR, instrument("bass", bn, beat * 0.45, 0.9 if i % 2 else 0.6))
        elif bstyle == "soft":
            add(bas, t0, instrument("bass", bn, beat * 1.8, 0.7))
            add(bas, t0 + 2.5 * beat * SR, instrument("bass", bn + 7, beat * 1.2, 0.55))
        elif bstyle == "long":
            add(bas, t0, instrument("bass", bn, bar * 0.98, 0.6))
        elif bstyle == "bounce":
            for i, off in enumerate((0, 1.5, 2, 3)):
                add(bas, t0 + off * beat * SR, instrument("bass", bn + (12 if i == 2 else 0), beat * 0.45, 0.85))
        elif bstyle == "drive":
            for i in range(16):
                add(bas, t0 + i * beat / 4 * SR, instrument("bass", bn - (0 if i % 8 else 0), beat * 0.2, 0.7 if i % 4 else 1.0))
        # Batería
        d = M["drums"]
        if intro and d not in ("none", "tick"):
            continue
        for q in range(4):
            tq = t0 + q * beat * SR
            if d == "four":
                add(drm, tq, k_s)
                add(drm, tq + beat / 2 * SR, hat(rng, open_=True))
                if q in (1, 3):
                    add(drm, tq, s_s * 0.6)
            elif d == "lofi":
                if q in (0,) or (q == 2 and rng.random() < 0.6):
                    add(drm, tq, k_s * 0.8)
                if q in (1, 3):
                    add(drm, tq, s_s * 0.35)
                for h in (0, 0.58):
                    add(drm, tq + h * beat * SR, hat(rng) * 0.7)
            elif d == "epic":
                if q in (0, 2):
                    add(drm, tq, tom(f0=80) * 0.9 + 0 * tq)
                if q == 3:
                    add(drm, tq + beat / 2 * SR, tom(f0=120) * 0.6)
                    add(drm, tq, s_s * 0.5)
            elif d == "bouncy":
                if q in (0, 2):
                    add(drm, tq, k_s)
                if q == 2:
                    add(drm, tq + 0.75 * beat * SR, k_s * 0.6)
                if q in (1, 3):
                    add(drm, tq, s_s * 0.55)
                add(drm, tq + beat / 2 * SR, hat(rng))
                add(drm, tq, hat(rng) * 0.6)
            elif d == "tick":
                for h in range(4):
                    add(drm, tq + h * beat / 4 * SR, hat(rng) * (0.9 if h == 0 else 0.4))
                if q == 0 and b % 2 == 1:
                    add(drm, tq, kick() * 0.7)
    cut = {"chill": 2200, "emotiva": 3000, "epica": 4200, "tension": 2600}.get(mood)
    if cut:
        mus = lowpass(mus, cut)
    mix = mus * 0.55 + lowpass(bas, 900) * 0.7 + drm * 0.6
    # Fundidos de entrada y salida, y normalización
    fi, fo = int(0.3 * SR), int(1.5 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)
    mix[-fo:] *= np.linspace(1, 0, fo)
    mix = np.tanh(mix / (np.max(np.abs(mix)) + 1e-9) * 1.4) * 0.85
    stereo = np.stack([mix, np.roll(mix, int(0.012 * SR)) * 0.96 + mix * 0.04], axis=1)
    return stereo


def save_wav(path, stereo):
    data = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def make(mood, path, seconds=32.0, seed=None):
    save_wav(path, render(mood, seconds, seed))
    return path


if __name__ == "__main__":
    mood = sys.argv[1] if len(sys.argv) > 1 else "energetica"
    out = sys.argv[2] if len(sys.argv) > 2 else f"{mood}.wav"
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 32
    make(mood, out, secs)
    print(out)
