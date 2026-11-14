"""Utilidades compartidas: servidor de la agencia (Cloudflare Worker) y GitHub Pages."""
import os
import sys

import requests


def api(method, path, body=None):
    base = os.environ.get("AGENCIA_API", "").strip().rstrip("/")
    token = os.environ.get("AGENCIA_ADMIN_TOKEN", "").strip()
    if not base or not token:
        sys.exit("Faltan AGENCIA_API y/o AGENCIA_ADMIN_TOKEN.")
    r = requests.request(method, base + path, json=body, timeout=180,
                         headers={"authorization": "Bearer " + token})
    try:
        j = r.json()
    except Exception:
        j = {}
    if r.status_code >= 400:
        raise RuntimeError((j.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return j


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


def summarize(queue):
    """Resumen de la cola para mostrar en la app (sin textos largos)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    upcoming = lambda q: q.get("status") in ("pending", "scheduled") and datetime.fromisoformat(q["publishAt"]) > now
    pend = [q for q in queue if upcoming(q)]
    done = [q for q in queue if not upcoming(q)][-8:]
    out = []
    for q in done + pend:
        item = {k: q.get(k) for k in ("id", "tipo", "tema", "publishAt", "status", "error", "zernioPostId") if q.get(k) is not None}
        if q.get("media"):
            item["media"] = [m for m in q["media"] if isinstance(m, str) and m.startswith("media/")][:6]
        if isinstance(q.get("cover"), str) and q["cover"].startswith("media/"):
            item["cover"] = q["cover"]
        out.append(item)
    return out


def zernio_reconcile(user, queue, log=print, min_age_h=2):
    """Red de seguridad: anula en Zernio cualquier publicación programada de esta persona que ya no
    esté en su cola (borrada, rehecha o sustituida). Solo toca su propio perfil y publicaciones con
    más de `min_age_h` horas, para no pisar una que se acabe de crear."""
    from datetime import datetime, timedelta, timezone
    prof = user.get("zernioProfileId")
    if not prof:
        return 0
    keep = {q.get("zernioPostId") for q in queue if q.get("zernioPostId") and q.get("status") == "scheduled"}
    try:
        r = api("POST", "/admin/zernio", {"method": "GET", "path": f"/posts?profileId={prof}&status=scheduled&limit=100"})
    except Exception as e:
        log(f"  ⚠ No se pudo revisar lo programado en Zernio: {e}")
        return 0
    posts = r.get("posts") or r.get("data") or []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_h)
    n = 0
    for p in posts:
        pid = p.get("_id") or p.get("id")
        if not pid or pid in keep or str(p.get("status", "scheduled")) != "scheduled":
            continue
        pp = p.get("profileId")
        pp = pp.get("_id") if isinstance(pp, dict) else pp
        if pp and pp != prof:
            continue
        try:
            created = datetime.fromisoformat(str(p.get("createdAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created > cutoff:
            continue
        try:
            api("POST", "/admin/zernio", {"method": "DELETE", "path": "/posts/" + pid})
            n += 1
            log(f"  🧹 Anulada en Zernio una publicación que ya no estaba en el plan ({str(p.get('scheduledFor', ''))[:16]})")
        except Exception as e:
            if "404" not in str(e) and "not found" not in str(e).lower():
                log(f"  ⚠ No se pudo anular {pid}: {e}")
    return n

