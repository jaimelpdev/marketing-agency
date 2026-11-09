"""
Agencia de marketing IA · publicación en Instagram (multiusuario)
------------------------------------------------------------------
Revisa la cola de cada persona (content/users/<id>/queue.json) y publica lo que ya toca.
Los tokens de Instagram están en el servidor de la agencia: este script le pide al servidor
que cree y publique cada pieza. Los archivos se leen desde GitHub Pages.

Variables de entorno: AGENCIA_API, AGENCIA_ADMIN_TOKEN, PAGES_URL (opcional), DRY_RUN=1 (simula).
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from common import api, pages_url, summarize

ROOT = Path(__file__).resolve().parent.parent
MAX_ATTEMPTS = 4
MAX_DELAY_HOURS = 20  # si una pieza lleva más de 20 h de retraso, se descarta


def log(*a):
    print("[publicar]", *a, flush=True)


class IG:
    """Instagram a través del servidor de la agencia (los tokens nunca salen de allí)."""
    def __init__(self, user_id):
        self.uid = user_id

    def container(self, **params):
        return api("POST", "/admin/ig", {"userId": self.uid, "action": "container", "params": params})["id"]

    def wait(self, cid, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = api("POST", "/admin/ig", {"userId": self.uid, "action": "status", "id": cid})
            st = j.get("status_code")
            if st in ("FINISHED", "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram no pudo procesar el archivo: {j.get('status')}")
            time.sleep(6)
        raise RuntimeError("Instagram tardó demasiado en procesar el vídeo")

    def publish(self, cid):
        return api("POST", "/admin/ig", {"userId": self.uid, "action": "publish", "id": cid}).get("id")


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


def publish_user(udir, base, dry):
    uid = udir.name
    qpath = udir / "queue.json"
    queue = json.loads(qpath.read_text(encoding="utf-8")) if qpath.exists() else []
    due = [q for q in queue if q.get("status") == "pending" and datetime.fromisoformat(q["publishAt"]) <= datetime.now(ZoneInfo("UTC"))]
    if not due:
        return
    ig = IG(uid)
    for item in due:
        when = datetime.fromisoformat(item["publishAt"])
        if datetime.now(ZoneInfo("UTC")) - when > timedelta(hours=MAX_DELAY_HOURS):
            item["status"] = "expired"
            item["error"] = "Demasiado retraso; no se publicó."
            continue
        log(f"[{uid}] Publicando {item['tipo']} «{item.get('tema', '')}»")
        if dry:
            log("  DRY_RUN: se publicaría", [f"{base}/{m}" for m in item["media"]])
            continue
        try:
            mid = publish_item(ig, item, base)
            item["status"] = "published"
            item["igMediaId"] = mid
            item["publishedAt"] = datetime.now(ZoneInfo("UTC")).isoformat()
            item.pop("error", None)
            log("  ✓ Publicado:", mid)
        except Exception as e:
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["error"] = str(e)[:400]
            if item["attempts"] >= MAX_ATTEMPTS:
                item["status"] = "failed"
            log("  ✗", e)
    if dry:
        return
    qpath.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        api("PUT", f"/admin/status/{uid}", {"queue": summarize(queue)})
    except Exception as e:
        log("  ⚠ No se pudo avisar a la app:", e)


def main():
    users_dir = ROOT / "content" / "users"
    dirs = [d for d in users_dir.glob("*") if d.is_dir()] if users_dir.exists() else []
    if not dirs:
        log("Nadie tiene contenido en cola todavía.")
        return
    dry = bool(os.environ.get("DRY_RUN"))
    base = pages_url()
    for d in dirs:
        publish_user(d, base, dry)


if __name__ == "__main__":
    main()
