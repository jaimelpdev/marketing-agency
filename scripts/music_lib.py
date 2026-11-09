"""Música real y gratuita para los vídeos (Jamendo, Freesound y Wikimedia a través de Openverse).

Solo se usan licencias que permiten uso comercial (CC0 o CC BY). Con CC BY se acredita al
artista en el texto de la publicación (o dentro del vídeo en las historias).
Las pistas descargadas se guardan en content/music/<ambiente>/ y se reutilizan.
"""
import json
import os
import random
import subprocess
from pathlib import Path

import requests

API = os.environ.get("OPENVERSE_API", "https://api.openverse.org/v1").rstrip("/")
UA = {"user-agent": "agencia-marketing-ia/1.0 (+https://github.com)"}
QUERIES = {
    "energetica": ["upbeat electronic", "energetic pop", "electro house", "upbeat rock"],
    "chill": ["lofi hip hop", "chillhop", "chill lounge", "lofi beat"],
    "epica": ["epic cinematic", "epic orchestral", "trailer cinematic"],
    "emotiva": ["emotional piano", "inspiring piano", "inspirational ambient"],
    "divertida": ["happy ukulele", "happy upbeat", "funny quirky"],
    "tension": ["suspense cinematic", "dark tension", "thriller suspense"],
}
POOL = 8           # pistas que se guardan por ambiente antes de empezar a reutilizar
REUSE_CHANCE = 0.7  # con la reserva llena, probabilidad de reutilizar en vez de buscar otra


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _index(folder):
    f = folder / "index.json"
    try:
        items = json.loads(f.read_text())
    except Exception:
        items = []
    return [t for t in items if (folder / t["file"]).exists()]


def _search(mood, log):
    q = random.choice(QUERIES.get(mood) or QUERIES["energetica"])
    params = {"q": q, "category": "music", "license": "cc0,by", "page_size": 20, "page": random.randint(1, 3)}
    r = requests.get(API + "/audio/", params=params, headers=UA, timeout=30)
    r.raise_for_status()
    res = [x for x in r.json().get("results", [])
           if x.get("url") and 60_000 <= (x.get("duration") or 0) <= 480_000 and not x.get("mature")]
    random.shuffle(res)
    log(f"    música: «{q}» → {len(res)} candidatas")
    return res


def _download(item, folder):
    ext = (item.get("filetype") or "mp3").lower().strip(".")
    ext = ext if ext in ("mp3", "ogg", "wav", "flac", "m4a") else "mp3"
    out = folder / f"{item['id']}.{ext}"
    with requests.get(item["url"], headers=UA, timeout=90, stream=True) as r:
        r.raise_for_status()
        size = 0
        with open(out, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > 25 * 1024 * 1024:
                    raise RuntimeError("archivo demasiado grande")
                fh.write(chunk)
    d = _probe(out)
    if d < 45:
        out.unlink(missing_ok=True)
        raise RuntimeError("pista no válida")
    lic = (item.get("license") or "").lower()
    return {"file": out.name, "title": item.get("title") or "Sin título", "creator": item.get("creator") or "Autor desconocido",
            "license": lic, "license_version": item.get("license_version") or "", "page": item.get("foreign_landing_url") or "",
            "source": item.get("source") or "", "duration": round(d, 1)}


def credit(track):
    """Texto de atribución (vacío si la licencia no lo exige)."""
    if not track or track.get("license") in ("cc0", "pdm", "own"):
        return ""
    v = (" " + track["license_version"]) if track.get("license_version") else ""
    return f"🎵 «{track['title']}» de {track['creator']} (CC {track['license'].upper()}{v})"


def get_track(mood, cache_root, log=print):
    """Devuelve (ruta, info) de una pista para el ambiente, o (None, None) si no hay ninguna disponible."""
    import unicodedata
    m = unicodedata.normalize("NFKD", str(mood or "")).encode("ascii", "ignore").decode().lower().strip()
    m = {"energica": "energetica", "energico": "energetica", "emocional": "emotiva", "emotivo": "emotiva", "epico": "epica",
         "tranquila": "chill", "relajada": "chill", "divertido": "divertida", "tenso": "tension", "suspense": "tension"}.get(m, m)
    mood = m if m in QUERIES else "energetica"
    folder = Path(cache_root) / mood
    folder.mkdir(parents=True, exist_ok=True)
    have = _index(folder)
    if have and (len(have) >= POOL and random.random() < REUSE_CHANCE):
        t = random.choice(have)
        return folder / t["file"], t
    try:
        known = {Path(t["file"]).stem for t in have}
        for item in _search(mood, log)[:6]:
            if item["id"] in known:
                continue
            try:
                t = _download(item, folder)
            except Exception as e:
                log(f"    ⚠ pista descartada ({e})")
                continue
            have.append(t)
            have = have[-POOL * 2:]
            (folder / "index.json").write_text(json.dumps(have, ensure_ascii=False, indent=1))
            return folder / t["file"], t
    except Exception as e:
        log(f"    ⚠ No se pudo buscar música nueva: {e}")
    if have:
        t = random.choice(have)
        return folder / t["file"], t
    for other in Path(cache_root).glob("*/index.json"):  # cualquier pista guardada antes que el silencio
        alt = _index(other.parent)
        if alt:
            t = random.choice(alt)
            return other.parent / t["file"], t
    return None, None
