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
        item = {k: q.get(k) for k in ("id", "tipo", "tema", "publishAt", "status", "error") if q.get(k) is not None}
        if q.get("media"):
            item["media"] = [m for m in q["media"] if isinstance(m, str) and m.startswith("media/")][:6]
        out.append(item)
    return out
