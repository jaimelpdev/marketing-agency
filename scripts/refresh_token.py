"""
Renueva el token de Instagram (dura 60 días) y lo guarda de nuevo como secreto del repositorio.
Solo funciona con tokens de "Instagram API con inicio de sesión de Instagram" (graph.instagram.com).

Variables de entorno:
  IG_ACCESS_TOKEN   token actual
  GH_TOKEN          un token personal de GitHub (secreto GH_PAT) con permiso para escribir secretos
  GITHUB_REPOSITORY lo pone GitHub Actions automáticamente
"""
import os
import subprocess
import sys

import requests

token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
if not token:
    sys.exit("Falta IG_ACCESS_TOKEN")
r = requests.get("https://graph.instagram.com/refresh_access_token",
                 params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
new = j.get("access_token")
if not new:
    sys.exit(f"No se pudo renovar el token: {r.status_code} {r.text[:300]}")
days = int(j.get("expires_in", 0)) // 86400
print(f"Token renovado. Caduca en {days} días.")
if not os.environ.get("GH_TOKEN"):
    sys.exit("Token renovado, pero falta el secreto GH_PAT para guardarlo. Actualiza IG_ACCESS_TOKEN a mano.")
subprocess.run(["gh", "secret", "set", "IG_ACCESS_TOKEN", "--repo", os.environ["GITHUB_REPOSITORY"], "--body", new], check=True)
print("Secreto IG_ACCESS_TOKEN actualizado ✓")
