"""
Agencia de marketing IA · publicación en Instagram
---------------------------------------------------
Revisa content/queue.json y publica en Instagram las piezas cuya hora ya ha llegado,
usando la API oficial de Instagram (Graph API). Los archivos se leen desde GitHub Pages.

Variables de entorno:
  IG_USER_ID        id de tu cuenta profesional de Instagram (obligatoria)
  IG_ACCESS_TOKEN   token de larga duración (obligatoria)
  IG_API_HOST       graph.instagram.com (por defecto) o graph.facebook.com si tu token es de Facebook Login
  IG_API_VERSION    v23.0 por defecto
  PAGES_URL         URL pública de tu GitHub Pages (si no, se deduce de GITHUB_REPOSITORY)
  DRY_RUN=1         simula sin publicar
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "content" / "queue.json"
CONFIG = ROOT / "agency.config.json"
MAX_ATTEMPTS = 4
MAX_DELAY_HOURS = 20  # si una pieza lleva más de 20 h de retraso, se descarta


def log(*a):
    print("[publicar]", *a, flush=True)


def pages_url():
    url = os.environ.get("PAGES_URL", "").strip().rstrip("/")
    if url:
        return url
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        sys.exit("Define PAGES_URL (la dirección de tu GitHub Pages).")
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io"
    return f"https://{owner.lower()}.github.io/{name}"


class IG:
    def __init__(self):
        self.uid = os.environ.get("IG_USER_ID", "").strip()
        self.token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
        host = os.environ.get("IG_API_HOST", "graph.instagram.com").strip()
        ver = os.environ.get("IG_API_VERSION", "v23.0").strip()
        self.base = (host if host.startswith("http") else "https://" + host).rstrip("/") + "/" + ver
        if not self.uid or not self.token:
            sys.exit("Faltan los secretos IG_USER_ID y/o IG_ACCESS_TOKEN.")

    def _check(self, r):
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code >= 400 or "error" in j:
            msg = (j.get("error") or {}).get("message") or r.text[:300]
            raise RuntimeError(f"Instagram {r.status_code}: {msg}")
        return j

    def container(self, **params):
        params["access_token"] = self.token
        return self._check(requests.post(f"{self.base}/{self.uid}/media", data=params, timeout=120))["id"]

    def wait(self, cid, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self._check(requests.get(f"{self.base}/{cid}", params={"fields": "status_code,status", "access_token": self.token}, timeout=60))
            st = j.get("status_code")
            if st in ("FINISHED", "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram no pudo procesar el archivo: {j.get('status')}")
            time.sleep(6)
        raise RuntimeError("Instagram tardó demasiado en procesar el vídeo")

    def publish(self, cid):
        j = self._check(requests.post(f"{self.base}/{self.uid}/media_publish", data={"creation_id": cid, "access_token": self.token}, timeout=120))
        return j.get("id")


def url_ok(url):
    try:
        r = requests.head(url, timeout=30, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def publish_item(ig, item, base):
    urls = [f"{base}/{m}" for m in item["media"]]
    for u in urls:
        if not url_ok(u):
            raise RuntimeError(f"El archivo aún no está en GitHub Pages: {u}")
    tipo = item["tipo"]
    cap = item.get("caption", "")
    if tipo == "reel":
        cid = ig.container(media_type="REELS", video_url=urls[0], caption=cap, share_to_feed="true")
        ig.wait(cid)
    elif tipo == "historia":
        if urls[0].endswith(".mp4"):
            cid = ig.container(media_type="STORIES", video_url=urls[0])
        else:
            cid = ig.container(media_type="STORIES", image_url=urls[0])
        ig.wait(cid)
    elif tipo == "post":
        cid = ig.container(image_url=urls[0], caption=cap)
        ig.wait(cid)
    elif tipo == "carrusel":
        if len(urls) == 1:
            cid = ig.container(image_url=urls[0], caption=cap)
        else:
            children = []
            for u in urls[:10]:
                c = ig.container(image_url=u, is_carousel_item="true")
                ig.wait(c)
                children.append(c)
            cid = ig.container(media_type="CAROUSEL", children=",".join(children), caption=cap)
        ig.wait(cid)
    else:
        raise RuntimeError(f"Tipo desconocido: {tipo}")
    return ig.publish(cid)


def main():
    conf = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    tz = ZoneInfo(conf.get("plan", {}).get("zona_horaria", "Europe/Madrid"))
    queue = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else []
    now = datetime.now(tz)
    due = [q for q in queue if q.get("status") == "pending" and datetime.fromisoformat(q["publishAt"]) <= now]
    if not due:
        log("Nada que publicar ahora.")
        return
    dry = bool(os.environ.get("DRY_RUN"))
    ig = None if dry else IG()
    base = pages_url()
    changed = False
    for item in due:
        when = datetime.fromisoformat(item["publishAt"])
        if now - when > timedelta(hours=MAX_DELAY_HOURS):
            item["status"] = "expired"
            item["error"] = "Demasiado retraso; no se publicó."
            changed = True
            continue
        log(f"Publicando {item['tipo']} «{item.get('tema', '')}» ({item['id']})")
        if dry:
            log("  DRY_RUN: se publicaría", [f"{base}/{m}" for m in item["media"]])
            continue
        try:
            mid = publish_item(ig, item, base)
            item["status"] = "published"
            item["igMediaId"] = mid
            item["publishedAt"] = datetime.now(tz).isoformat()
            item.pop("error", None)
            log("  ✓ Publicado:", mid)
        except Exception as e:
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["error"] = str(e)[:400]
            if item["attempts"] >= MAX_ATTEMPTS:
                item["status"] = "failed"
            log("  ✗", e)
        changed = True
    if changed:
        QUEUE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
