"""Cerebro de la agencia: memoria a largo plazo y estratega semanal.

- La memoria vive en content/users/<id>/memoria.json (y se copia a la app para verla y usarla en el chat).
- Una vez por semana (o cuando se rehace todo) un "director de estrategia" revisa los resultados reales
  de las redes, la memoria anterior y las notas del dueño, y decide la fase de la marca, el arco de la
  semana, las series, la mezcla de objetivos y lo aprendido.
- El planificador recibe todo eso como contexto (memory_text) y además las piezas dejan huella
  (series, episodios, promesas) para la siguiente vez.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

import requests

GROQ = os.environ.get("GROQ_API_BASE", "https://api.groq.com/openai/v1").rstrip("/")
MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
FASES = ["descubrimiento", "confianza", "comunidad", "conversion"]

DEFAULT = {
    "version": 1, "creada": None, "actualizada": None, "semana": 0,
    "fase": "descubrimiento", "objetivo_mes": "", "arco_semana": {"tema": "", "porque": ""},
    "mezcla": {"alcance": 50, "confianza": 30, "comunidad": 15, "conversion": 5},
    "series": [], "aprendizajes": [], "que_evitar": [], "promesas_pendientes": [], "ideas": [],
    "tono_comunidad": "", "hitos": [], "metricas": {},
}


def load(path):
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        return {**DEFAULT, **m}
    except Exception:
        return dict(DEFAULT)


def save(path, mem):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")


def metrics(published):
    """Resumen de resultados reales por formato y las mejores/peores publicaciones."""
    rows = []
    for p in published:
        m = re.search(r"❤️(\d+)", p.get("tema", ""))
        c = re.search(r"💬(\d+)", p.get("tema", ""))
        likes, com = int(m.group(1)) if m else None, int(c.group(1)) if c else 0
        if likes is None:
            continue
        rows.append({"tipo": p.get("tipo"), "date": p.get("date"), "eng": likes + 3 * com, "likes": likes, "com": com,
                     "tema": p["tema"].split("] ", 1)[-1][:110]})
    if not rows:
        return {}
    by = {}
    for r in rows:
        by.setdefault(r["tipo"], []).append(r["eng"])
    rows.sort(key=lambda r: -r["eng"])
    return {
        "publicaciones_medidas": len(rows),
        "media_por_formato": {k: round(sum(v) / len(v), 1) for k, v in by.items()},
        "mejores": rows[:5], "peores": rows[-3:] if len(rows) > 5 else [],
    }


def needs_reflection(mem, force=False):
    if force or not mem.get("actualizada"):
        return True
    try:
        return datetime.fromisoformat(mem["actualizada"]) < datetime.now() - timedelta(days=6)
    except Exception:
        return True


def groq_json(prompt, key, log=print, tries=4):
    last = None
    for i in range(tries):
        model = MODELS[0] if i < 2 else MODELS[1]
        try:
            base = os.environ.get("GROQ_API_BASE", GROQ).rstrip("/")
            r = requests.post(base + "/chat/completions", timeout=120,
                              headers={"authorization": "Bearer " + key, "content-type": "application/json"},
                              json={"model": model, "temperature": 0.7, "response_format": {"type": "json_object"},
                                    "messages": [{"role": "user", "content": prompt}]})
            if r.status_code == 429:
                last = "límite de Groq"
                log(f"  ⏳ Estratega: Groq saturado, espero… ({model})")
                time.sleep(60)
                continue
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            a, b = txt.find("{"), txt.rfind("}")
            return json.loads(txt[a:b + 1])
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5)
    raise RuntimeError(f"El estratega no ha podido pensar ({last})")


def reflect(mem, user, history, published, key, log=print):
    """El director de estrategia actualiza la memoria con resultados reales y la visión a largo plazo."""
    conf = user.get("config") or {}
    marca = conf.get("marca") or {}
    notas = ((conf.get("plan") or {}).get("notas") or conf.get("notas") or [])[-20:]
    met = metrics(published)
    past = [f"- {h.get('date', '')} · {h.get('tipo', '')}{' · ' + h['formato'] if h.get('formato') else ''}{' · serie ' + h['serie'] if h.get('serie') else ''} · {h.get('tema', '')}"
            for h in (history.get("items") or [])[-40:]]
    prompt = f"""Eres el DIRECTOR DE ESTRATEGIA de una agencia de marketing que lleva las redes (Instagram, TikTok, YouTube) de esta marca de forma automática. Piensas a largo plazo: construir una marca querida y una comunidad fiel, y promocionar los productos poco a poco y con naturalidad, nunca como un anuncio constante.

MARCA
{json.dumps(marca, ensure_ascii=False)[:2500]}

NOTAS DEL DUEÑO (tienen prioridad, respétalas siempre)
{chr(10).join('- ' + str(n.get('t', n) if isinstance(n, dict) else n) for n in notas) or '(ninguna)'}

MEMORIA ANTERIOR DE LA AGENCIA
{json.dumps({k: v for k, v in mem.items() if k not in ('metricas',)}, ensure_ascii=False)[:5000]}

RESULTADOS REALES EN LAS REDES (engagement = me gusta + 3 × comentarios)
{json.dumps(met, ensure_ascii=False)[:3000] or '(aún sin datos)'}

LO PUBLICADO Y PROGRAMADO (más reciente al final)
{chr(10).join(past) or '(nada todavía)'}

CÓMO PIENSAS
1. Fases de la marca (avanza solo cuando toque; semana actual {mem.get('semana', 0) + 1}): "descubrimiento" (alcance: ganchos, tips virales, quién eres), "confianza" (enseñar con profundidad, resultados, detrás de las cámaras), "comunidad" (series, retos, preguntas, responder a la audiencia, que la gente se sienta parte), "conversion" (sin perder valor: casos, beneficios, ofertas y llamadas claras al producto). Las 2-3 primeras semanas casi todo es descubrimiento y confianza.
2. La "mezcla" (porcentaje de piezas según objetivo, suma 100) sube la conversión de forma gradual: 0-10% el primer mes, hasta 20-25% cuando ya hay comunidad. Siempre mínimo un 15% de comunidad.
3. Crea 2-4 SERIES recurrentes que den identidad y hábito (por ejemplo "El error de la semana", "Setup del seguidor", "Mito o realidad"), con su formato y día. Mantén las que funcionan y numera los episodios.
4. Aprende de los resultados: qué formatos, temas, ganchos y horas funcionan mejor, y qué evitar. Sé concreto ("los reels de errores tienen 3× más comentarios que los posts de app").
5. Lleva una lista de promesas pendientes (si se dijo "comenta X y te paso la guía", o "el próximo vídeo…", hay que cumplirlo) y de ideas para próximas semanas.
6. Define el arco de la semana: un tema o historia que conecte las piezas de la semana para que la gente quiera ver la siguiente.

Responde SOLO con JSON válido:
{{"fase":"descubrimiento|confianza|comunidad|conversion","objetivo_mes":"…","arco_semana":{{"tema":"…","porque":"…"}},"mezcla":{{"alcance":0,"confianza":0,"comunidad":0,"conversion":0}},"series":[{{"nombre":"…","formato":"reel|historia|post|carrusel","cuando":"día o frecuencia","descripcion":"…","episodios":0}}],"aprendizajes":["…"],"que_evitar":["…"],"promesas_pendientes":["…"],"ideas":["…"],"tono_comunidad":"cómo hablarle a la comunidad","hitos":["próximos hitos de la marca"],"resumen_para_dueño":"2-3 frases explicando la estrategia de esta semana"}}"""
    j = groq_json(prompt, key, log)
    out = dict(mem)
    if j.get("fase") in FASES:
        out["fase"] = j["fase"]
    for k in ("objetivo_mes", "tono_comunidad", "resumen_para_dueño"):
        if isinstance(j.get(k), str):
            out[k] = j[k][:600]
    if isinstance(j.get("arco_semana"), dict):
        out["arco_semana"] = {"tema": str(j["arco_semana"].get("tema", ""))[:200], "porque": str(j["arco_semana"].get("porque", ""))[:400]}
    mz = j.get("mezcla")
    if isinstance(mz, dict):
        vals = {k: max(0, int(float(mz.get(k, 0) or 0))) for k in ("alcance", "confianza", "comunidad", "conversion")}
        tot = sum(vals.values()) or 1
        out["mezcla"] = {k: round(v * 100 / tot) for k, v in vals.items()}
    if isinstance(j.get("series"), list):
        prev = {s.get("nombre", "").lower(): s for s in mem.get("series", [])}
        ser = []
        for s in j["series"][:4]:
            if not isinstance(s, dict) or not s.get("nombre"):
                continue
            old = prev.get(str(s["nombre"]).lower(), {})
            ser.append({"nombre": str(s["nombre"])[:60], "formato": str(s.get("formato", "reel"))[:12], "cuando": str(s.get("cuando", ""))[:60],
                        "descripcion": str(s.get("descripcion", ""))[:200], "episodios": max(int(old.get("episodios", 0) or 0), int(s.get("episodios", 0) or 0))})
        out["series"] = ser
    for k, n in (("aprendizajes", 10), ("que_evitar", 8), ("promesas_pendientes", 8), ("ideas", 12), ("hitos", 5)):
        if isinstance(j.get(k), list):
            out[k] = [str(x)[:220] for x in j[k] if str(x).strip()][:n]
    now = datetime.now().isoformat(timespec="seconds")
    out["semana"] = int(mem.get("semana", 0) or 0) + 1
    out["creada"] = mem.get("creada") or now
    out["actualizada"] = now
    out["metricas"] = met
    return out


def memory_text(mem, notas=()):
    """Bloque que se añade a la petición del planificador."""
    if not mem:
        return ""
    ser = "\n".join(f"  · «{s['nombre']}» ({s.get('formato', '')}, {s.get('cuando', '')}): {s.get('descripcion', '')} — van {s.get('episodios', 0)} episodios"
                    for s in mem.get("series", [])) or "  (aún ninguna: crea la primera)"
    lst = lambda k: "\n".join("  · " + x for x in mem.get(k, [])) or "  (nada)"  # noqa: E731
    mz = mem.get("mezcla", {})
    notes = "\n".join("  · " + str(n.get("t", n) if isinstance(n, dict) else n) for n in list(notas)[-20:]) or "  (ninguna)"
    return f"""

MEMORIA Y ESTRATEGIA DE MARCA (decidida por el director de estrategia; síguela)
- Semana {mem.get('semana', 1)} · fase actual: {mem.get('fase', 'descubrimiento').upper()} · objetivo del mes: {mem.get('objetivo_mes', '-')}
- Arco de esta semana: {mem.get('arco_semana', {}).get('tema', '-')} ({mem.get('arco_semana', {}).get('porque', '')})
- Mezcla de objetivos para las piezas: alcance {mz.get('alcance', 0)}% · confianza {mz.get('confianza', 0)}% · comunidad {mz.get('comunidad', 0)}% · conversión {mz.get('conversion', 0)}%. Marca cada pieza con su "objetivo".
- Series recurrentes (úsalas: pon "serie" y el siguiente "episodio"):
{ser}
- Lo que ha funcionado:
{lst('aprendizajes')}
- Evitar:
{lst('que_evitar')}
- Promesas pendientes que hay que cumplir esta semana si encajan:
{lst('promesas_pendientes')}
- Ideas guardadas:
{lst('ideas')}
- Cómo hablarle a la comunidad: {mem.get('tono_comunidad', '-')}
- NOTAS DEL DUEÑO (obligatorio respetarlas):
{notes}
- Promoción de la marca poco a poco: en piezas de alcance y confianza, la marca aparece de forma natural (un ejemplo, un detrás de las cámaras, una mención al final), nunca como anuncio. Solo las piezas de "conversion" llevan el producto al centro, y siempre aportando valor.
- Comunidad: en cada semana debe haber al menos una pieza que haga sentir parte a la audiencia (responder a comentarios o dudas típicas, retos, "cuéntame tu…", destacar a seguidores, decisiones que vota la comunidad, rituales de la serie)."""


def after_plan(mem, pieces):
    """Las piezas nuevas dejan huella en la memoria (episodios de series)."""
    if not mem:
        return mem
    ser = {s["nombre"].lower(): s for s in mem.get("series", [])}
    for p in pieces:
        name = str(p.get("serie") or "").lower().strip()
        if name and name in ser:
            ser[name]["episodios"] = int(ser[name].get("episodios", 0) or 0) + 1
    return mem


def public(mem):
    """Lo que se enseña en la app (sin métricas en bruto)."""
    if not mem:
        return None
    keep = ("semana", "fase", "objetivo_mes", "arco_semana", "mezcla", "series", "aprendizajes", "que_evitar",
            "promesas_pendientes", "ideas", "tono_comunidad", "hitos", "resumen_para_dueño", "actualizada")
    return {k: mem.get(k) for k in keep}
