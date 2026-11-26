"""Cerebro de la agencia: memoria a largo plazo y estratega semanal.

- La memoria vive en content/users/<id>/memoria.json (y se copia a la app para verla y usarla en el chat).
- Una vez por semana (o cuando se rehace todo) un "director de estrategia" revisa los resultados reales
  de las redes, la memoria anterior y las notas del dueño, y decide la fase de la marca, el arco de la
  semana, las series, la mezcla de objetivos y lo aprendido.
- El planificador recibe todo eso como contexto (memory_text) y además las piezas dejan huella
  (series, episodios, promesas) para la siguiente vez.
"""
import json
from pathlib import Path
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
    if not mem.get("actualizada"):
        return True
    if force:  # rehacer la semana no es una semana nueva: como mucho una revisión al día
        try:
            return datetime.fromisoformat(mem["actualizada"]) < datetime.now() - timedelta(hours=20)
        except Exception:
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
    txt = gemini_text(prompt, log)
    if txt:
        a, b = txt.find("{"), txt.rfind("}")
        try:
            return json.loads(txt[a:b + 1])
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"El estratega no ha podido pensar ({last})")


GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
WRITER = {"gemini_ok": None}


def gemini_models(gk, log=print):
    """Los nombres de los modelos cambian con cada generación: se pregunta a Google cuáles hay y se elige el mejor «flash»."""
    try:
        r = requests.get(GEMINI + "/models", timeout=30, headers={"authorization": "Bearer " + gk})
        if r.status_code in (401, 403):
            log(f"    ⚠ Gemini rechaza la clave ({r.status_code}); uso Groq")
            return []
        r.raise_for_status()
        ids = [str(m.get("id", "")).replace("models/", "") for m in r.json().get("data", [])]
    except Exception as e:  # noqa: BLE001
        log(f"    ⚠ No pude listar los modelos de Gemini ({str(e)[:100]}); pruebo los conocidos")
        return GEMINI_MODELS
    bad = ("image", "tts", "audio", "live", "embedding", "aqa", "vision", "thinking-exp", "learnlm", "gemma", "robotics", "computer")
    cand = [m for m in ids if m.startswith("gemini-") and "flash" in m and not any(b in m for b in bad)]

    def rank(m):
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", m)
        return (float(v.group(1)) if v else 0, "lite" not in m, "preview" not in m and "exp" not in m, -len(m))
    cand.sort(key=rank, reverse=True)
    lite = [m for m in cand if "lite" in m]
    # primero el «lite»: en el plan gratuito responde siempre y escribe bien; el grande se satura a menudo
    best = lite[:1] + [m for m in cand if "lite" not in m][:1]
    log(f"    🤖 Redacción con Gemini: {', '.join(best) or 'ninguno disponible'}")
    return best


def gemini_text(prompt, log=print, system=""):
    """Texto JSON de Gemini (gratis) o "" si no hay clave o no responde. Lo usa el plan cuando Groq se queda sin cupo."""
    gk = os.environ.get("GEMINI_API_KEY", "").strip()
    if not gk or WRITER["gemini_ok"] is False:
        return ""
    if not WRITER.get("models"):
        WRITER["models"] = gemini_models(gk, log)
    for i, model in enumerate((WRITER.get("models") or [])[:2] * 2):
        try:
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
            r = requests.post(GEMINI + "/chat/completions", timeout=150,
                              headers={"authorization": "Bearer " + gk, "content-type": "application/json"},
                              json={"model": model, "temperature": 0.8, "response_format": {"type": "json_object"},
                                    "reasoning_effort": "low", "messages": msgs})
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(15 + 10 * i)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ Gemini ({model}): {str(e)[:100]}")
            time.sleep(5)
    return ""


def writer_json(prompt, key, log=print):
    """Redacción (guiones, revisión, diapositivas): Gemini gratis si hay GEMINI_API_KEY (escribe y sabe bastante
    más que los modelos de Groq); si no está o falla, Groq."""
    gk = os.environ.get("GEMINI_API_KEY", "").strip()
    if gk and WRITER["gemini_ok"] is not False and not WRITER.get("models"):
        WRITER["models"] = gemini_models(gk, log)
        if not WRITER["models"]:
            WRITER["gemini_ok"] = False
    if gk and WRITER["gemini_ok"] is not False:
        for i, model in enumerate(WRITER["models"][:2] * 2):
            try:
                r = requests.post(GEMINI + "/chat/completions", timeout=100,
                                  headers={"authorization": "Bearer " + gk, "content-type": "application/json"},
                                  json={"model": model, "temperature": 0.6, "response_format": {"type": "json_object"},
                                        "reasoning_effort": "low",
                                        "messages": [{"role": "user", "content": prompt}]})
                if r.status_code == 400 and "reasoning" in r.text.lower():  # modelo sin ese ajuste
                    r = requests.post(GEMINI + "/chat/completions", timeout=240,
                                      headers={"authorization": "Bearer " + gk, "content-type": "application/json"},
                                      json={"model": model, "temperature": 0.6, "response_format": {"type": "json_object"},
                                            "messages": [{"role": "user", "content": prompt}]})
                if r.status_code in (500, 502, 503, 504):  # saturado: esperar y probar el siguiente
                    log(f"    ⏳ Gemini ({model}) saturado ({r.status_code}), reintento…")
                    time.sleep(15 + 10 * i)
                    continue
                if r.status_code in (401, 403):
                    WRITER["gemini_ok"] = False
                    log(f"    ⚠ Gemini rechaza la clave ({r.status_code}); uso Groq")
                    break
                if r.status_code == 429:
                    time.sleep(20 + 10 * i)
                    continue
                r.raise_for_status()
                txt = r.json()["choices"][0]["message"]["content"]
                a, b = txt.find("{"), txt.rfind("}")
                if not WRITER.get("gemini_ok"):
                    log(f"    ✅ Gemini responde ({model})")
                WRITER["gemini_ok"] = True
                return json.loads(txt[a:b + 1])
            except Exception as e:  # noqa: BLE001
                log(f"    ⚠ Gemini ({model}): {str(e)[:120]}")
                WRITER["fails"] = WRITER.get("fails", 0) + 1
                if WRITER["fails"] >= 6 and not WRITER.get("gemini_ok"):
                    WRITER["gemini_ok"] = False
                    break
                time.sleep(5)
    return groq_json(prompt, key, log)


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
3. Crea 2-4 SERIES recurrentes que den identidad y hábito (por ejemplo "El error de la semana", "Mito o realidad", "Tip rápido"), con su formato y día. Mantén las que funcionan y numera los episodios.
4. Aprende de los resultados: qué formatos, temas, ganchos y horas funcionan mejor, y qué evitar. Sé concreto ("los reels de errores tienen 3× más comentarios que los posts de app").
5. Lleva una lista de promesas pendientes (si se dijo "comenta X y te paso la guía", o "el próximo vídeo…", hay que cumplirlo) y de ideas para próximas semanas.
6b. No propongas series que dependan de que los seguidores envíen cosas (setups, vídeos, preguntas) salvo que las notas del dueño digan que ya las envían.
6. No inventes nada: ni seguidores, ni testimonios, ni ofertas, descuentos, precios o lanzamientos, ni funciones, apps, IA o servicios del negocio que no estén en la marca o en las notas del dueño. Las series que dependan de la comunidad empiezan pidiendo participación.
4b. Si en RESULTADOS REALES pone "(aún sin datos)", NO hay nada publicado todavía: no escribas aprendizajes sobre resultados (alcance, comentarios, horas que funcionan…), ni hitos con cifras de seguidores o visualizaciones, ni retos o desafíos con participación de usuarios. "aprendizajes" debe ir vacío salvo lo que digan las notas del dueño.
7. Define el arco de la semana: un PILAR amplio (por ejemplo "frenar mejor", "configurar el volante", "elegir y ajustar el equipo") que conecte piezas sobre temas DISTINTOS dentro de él; nunca una semana entera sobre un único detalle. El "porque" solo puede apoyarse en RESULTADOS REALES (cita qué publicación funcionó) o en las notas del dueño; si no hay datos, di que se elige por variedad. Nunca escribas "los datos muestran…" sin datos reales.
8. Copia lo que ya funciona: mira las MEJORES y PEORES publicaciones reales y di en "aprendizajes" qué tienen en común las mejores (tipo de consejo, si nombran el simulador o el equipo, si es un ajuste concreto, cómo empieza el gancho) y qué tienen las peores. La semana debe parecerse a las mejores.
9. Ni retos con premios, ni colaboraciones, ni sorteos, ni encuestas con promesas que la agencia no puede cumplir sola.

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
                        "descripcion": str(s.get("descripcion", ""))[:200], "episodios": int(old.get("episodios", 0) or 0)})  # los cuenta la agencia, no la IA
        out["series"] = ser
    for k, n in (("aprendizajes", 10), ("que_evitar", 8), ("promesas_pendientes", 8), ("ideas", 12), ("hitos", 5)):
        if isinstance(j.get(k), list):
            out[k] = [str(x)[:220] for x in j[k] if str(x).strip()][:n]
    PROMESA = re.compile(r"\b(reto|desaf[ií]o|premi|sorteo|colabora|entrevista|influencer|regal)", re.I)
    out["ideas"] = [x for x in out.get("ideas", []) if not PROMESA.search(x)]
    out["hitos"] = [x for x in out.get("hitos", []) if not PROMESA.search(x)]
    if isinstance(out.get("arco_semana"), dict) and not met and re.search(r"los datos|datos muestran|estad[ií]stic", out["arco_semana"].get("porque", ""), re.I):
        out["arco_semana"]["porque"] = "Elegido por variedad y por ser útil para quien empieza."
    if not met:  # nada publicado aún: ni aprendizajes ni hitos inventados, ni series que dependan de seguidores
        own = " ".join(str(n.get("t", n) if isinstance(n, dict) else n) for n in notas).lower()
        out["aprendizajes"] = [a for a in out.get("aprendizajes", []) if any(w in own for w in re.findall(r"\w{6,}", a.lower())[:3])][:3] if own else []
        out["hitos"] = [h for h in out.get("hitos", []) if not re.search(r"\d", h)]
        if "seguidor" not in own:
            out["series"] = [x for x in out.get("series", []) if not re.search(r"seguidor|comunidad env|vuestr|tus setups", x["nombre"] + " " + x["descripcion"], re.I)]
    now = datetime.now().isoformat(timespec="seconds")
    try:
        out["semana"] = max(1, (datetime.now() - datetime.fromisoformat(mem.get("creada") or now)).days // 7 + 1)
    except ValueError:
        out["semana"] = 1
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
- Comunidad: en cada semana debe haber al menos una pieza que haga sentir parte a la audiencia (responder a dudas típicas, "cuéntame cómo lo haces tú…", rituales de la serie), sin retos con premios ni promesas que haya que cumplir después."""


def best_examples(mem, n=4):
    """Las publicaciones reales que mejor funcionaron (para que el plan se parezca a ellas)."""
    met = (mem or {}).get("metricas") or {}
    best = [f"{b.get('tipo', '')} ({b.get('eng', 0)} interacciones): «{str(b.get('tema', ''))[:110]}»" for b in (met.get("mejores") or [])[:n]]
    worst = [f"{b.get('tipo', '')} ({b.get('eng', 0)}): «{str(b.get('tema', ''))[:90]}»" for b in (met.get("peores") or [])[:3]]
    if not best:
        return ""
    return ("\n- LO QUE MEJOR HA FUNCIONADO DE VERDAD en esta cuenta (haz piezas de este estilo: consejo concreto, a menudo un ajuste o una técnica en un simulador concreto):\n"
            + "\n".join("  · " + x for x in best)
            + ("\n- Lo que peor funcionó (no lo imites):\n" + "\n".join("  · " + x for x in worst) if worst else ""))


def used_topics(udir, days=21):
    """Temas ya publicados o preparados en las últimas semanas (cola, bolsa e historial), para no repetirse."""
    lim = (datetime.now() - timedelta(days=days)).date().isoformat()
    out = []
    for name in ("queue.json", "bolsa.json"):
        try:
            for x in json.loads((udir / name).read_text(encoding="utf-8")):
                d = str(x.get("date") or x.get("created") or x.get("publishAt") or "")[:10]
                t = str(x.get("tema") or "").strip()
                if t and d >= lim and x.get("status") not in ("removed",):
                    out.append(t)
        except Exception:  # noqa: BLE001
            pass
    seen, res = set(), []
    for t in out:
        k = t.lower()[:40]
        if k not in seen:
            seen.add(k)
            res.append(t)
    return res[-25:]


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


# voseo rioplatense: formas acentuadas de 2.ª persona que en España no se usan
VOSEO = re.compile(r"\b(?:aplicás|tenés|podés|sabés|querés|hacés|ponés|mirá|fijate|andá|vení|decí|probá|usá|elegí|subí|bajá|sentís|notás|buscás|empezás|frenás|girás|acelerás|ajustás|cambiás|tomá|dejá|pensá|imaginá|sos)\b", re.I)


_VOS_IRR = {"tenés": "tienes", "podés": "puedes", "querés": "quieres", "sentís": "sientes", "elegí": "elige", "vení": "ven",
            "decí": "di", "sos": "eres", "fijate": "fíjate", "andá": "ve", "subí": "sube", "empezás": "empiezas"}


def de_voseo(text):
    """Pasa a tuteo las formas de voseo que se cuelen (aplicás → aplicas)."""
    def fix(m):
        w = m.group(0)
        low = w.lower()
        r = _VOS_IRR.get(low) or (low[:-2] + {"á": "a", "é": "e", "í": "i"}[low[-2]] + "s" if low.endswith(("ás", "és", "ís")) else
                                 {"mirá": "mira", "probá": "prueba", "usá": "usa", "bajá": "baja", "tomá": "toma", "dejá": "deja",
                                  "pensá": "piensa", "imaginá": "imagina"}.get(low, low))
        return r.capitalize() if w[0].isupper() else r
    return VOSEO.sub(fix, str(text))


KNOW_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def knowledge_for(user):
    """Banco de conocimiento verificado del sector del negocio (knowledge/*.md, por palabras clave) + lo que el dueño haya escrito."""
    marca = ((user.get("config") or {}).get("marca") or {})
    hay = " ".join(str(marca.get(k, "")) for k in ("name", "about", "aud", "pillars", "tone")).lower()
    parts = []
    for f in sorted(KNOW_DIR.glob("*.md")):
        txt = f.read_text(encoding="utf-8")
        m = re.search(r"claves:\s*\[(.*?)\]", txt)
        claves = [c.strip().lower() for c in (m.group(1).split(",") if m else []) if c.strip()]
        if any(c and c in hay for c in claves):
            parts.append(re.sub(r"^---.*?---\s*", "", txt, flags=re.S).strip())
    own = str(marca.get("conocimiento") or "").strip()
    if own:
        parts.insert(0, "# Del dueño del negocio\n" + own[:4000])
    return "\n\n".join(parts)[:9000]


def knowledge_topics(user):
    """Lista corta de temas del banco de conocimiento, para que el plan elija temas con base real."""
    k = knowledge_for(user)
    out = []
    for t in re.findall(r"^- ([^:\n]{4,70}):", k, re.M):
        if t[:3].upper() == t[:3]:
            out.append(t.strip().capitalize())
    return out


def review_script(piece, negocio, key, log=print, saber=""):
    """Revisor experto: corrige o quita lo que sea falso, dudoso o inventado antes de grabar la voz."""
    fr = [b["t"] for b in piece.get("beats") or []]
    if len(fr) < 2:
        return
    prompt = f"""Eres un experto exigente en el tema de este negocio y revisas el guion de un vídeo corto antes de publicarlo.
NEGOCIO: {negocio}
TEMA: {piece.get('tema', '')}
GUION (una frase por línea):
""" + "\n".join(f"{k + 1}. {x}" for k, x in enumerate(fr)) + f"""
TEXTO DE LA PUBLICACIÓN: {piece.get('caption', '')}
""" + (f"""
CONOCIMIENTO VERIFICADO DEL SECTOR (la referencia para decidir qué es correcto):
{saber}
""" if saber else "") + """
Comprueba cada frase como lo haría un profesional del sector:
- Si hay CONOCIMIENTO VERIFICADO: toda afirmación técnica tiene que estar respaldada por él. Lo que no lo esté (o lo contradiga), cámbialo por lo que dice el conocimiento sobre ese mismo asunto.
- ¿Es técnicamente correcta y un buen consejo? Si es falsa, dudosa o puede llevar a error, reescríbela con un consejo correcto sobre lo mismo.
- ¿Contiene cifras, tiempos, porcentajes, estudios, nombres, funciones del negocio u ofertas que no estén en NEGOCIO? Quítalos (explica la idea sin número).
- ¿Hay relleno vacío o una frase que no aporta? Mejórala con algo concreto y útil.
- Mantén el mismo número de frases, el orden, el tono, el tuteo en español de España y una longitud parecida por frase (6-14 palabras).
- Revisa también el texto de la publicación con las mismas reglas.

Por último, puntúa de 1 a 10 el vídeo YA CORREGIDO como pieza para publicar (gancho que para el scroll, utilidad real, claridad); sé exigente: 8+ solo si de verdad destaca.

Responde SOLO con JSON: {{"frases":["…"],"caption":"…","cambios":["qué corregiste y por qué, muy breve"],"nota":7}}"""
    try:
        j = writer_json(prompt, key, log)
    except Exception as e:  # noqa: BLE001
        log(f"    ⚠ Revisor: {e}")
        return
    new = [de_voseo(re.sub(r"\s+", " ", str(x)).strip()) for x in (j.get("frases") or []) if str(x).strip()]
    if len(new) != len(fr) or sum(len(x.split()) for x in new) < 0.7 * sum(len(x.split()) for x in fr):
        log("    ⚠ Revisor: respuesta no válida; se mantiene el guion")
        return
    for b, x in zip(piece["beats"], new):
        b["t"] = x[:220]
    piece["guion"] = [b["t"] for b in piece["beats"]]
    cap = de_voseo(str(j.get("caption") or "").strip())
    if len(cap) > 40:
        piece["caption"] = cap[:600]
    try:
        piece["nota"] = max(1.0, min(10.0, float(j.get("nota"))))
    except (TypeError, ValueError):
        pass
    cambios = [str(c) for c in (j.get("cambios") or []) if str(c).strip()]
    log(f"    🔎 Revisor (nota {piece.get('nota', '-')}): {len(cambios)} corrección(es)" + (f" — {'; '.join(cambios)[:220]}" if cambios else ""))


USED = {}  # entradas del conocimiento ya usadas en esta ejecución, para no repetir tema en la semana


def write_script(piece, user, key, log=print):
    """Guionista: reescribe la narración de un vídeo faceless como una explicación útil y concreta,
    frase a frase, con la duración que toca. Una llamada dedicada por vídeo da mucho mejor guion
    que pedir toda la semana de golpe."""
    story = piece.get("tipo") == "historia"
    nmin, nmax, wmin, wmax = (4, 6, 38, 65) if story else (8, 11, 75, 115)
    marca = ((user.get("config") or {}).get("marca") or {})
    negocio = json.dumps({k: marca.get(k) for k in ("name", "about", "aud", "pillars", "tone") if marca.get(k)}, ensure_ascii=False)[:1500]
    borrador = " ".join(str(b.get("t", "")) for b in (piece.get("beats") or [])) or " ".join(piece.get("guion") or [])
    saber = knowledge_for(user)
    base = f"""Eres guionista de vídeos cortos faceless (voz en off sobre imágenes) que consiguen muchas visitas porque ENSEÑAN algo de verdad. Escribe la narración de este vídeo en el idioma del borrador.

NEGOCIO: {negocio}
TEMA: {piece.get('tema', '')}
GANCHO PROPUESTO: {piece.get('gancho', '')}
BORRADOR (mejóralo, no lo copies): {borrador[:900]}
""" + (f"""
CONOCIMIENTO VERIFICADO DEL SECTOR (tu base técnica: el consejo tiene que salir de aquí, ser concreto y no contradecirlo nunca):
{saber}
""" if saber else "") + f"""
CÓMO TIENE QUE SER
0a. No menciones circuitos, curvas, coches ni pilotos concretos (nada de "en Eau Rouge"): el consejo vale para cualquier pista. SÍ puedes nombrar el simulador (iRacing, ACC, Assetto Corsa, Le Mans Ultimate, Gran Turismo, F1) en el gancho si el consejo vale tal cual en él: es lo que mejor funciona en esta cuenta.
0b. Esta semana ya hay vídeos sobre: {', '.join(USED.get(user.get('id'), [])) or 'nada todavía'}. Elige una entrada DISTINTA.
0. Si hay CONOCIMIENTO VERIFICADO: elige UNA entrada concreta que corresponda al TEMA. Si el tema no está cubierto o contradice el conocimiento, elige la entrada más cercana y cambia el tema y el gancho para que encajen. Todo lo técnico del vídeo sale de esa entrada (puedes explicarla con tus palabras y con ejemplos, nunca añadir afirmaciones técnicas nuevas).
1. UNA sola idea por vídeo, explicada de principio a fin: gancho → por qué pasa → qué hacer exactamente (pasos concretos que el espectador puede aplicar hoy) → qué nota cuando lo hace bien → cierre.
2. Entre {nmin} y {nmax} frases y entre {wmin} y {wmax} palabras en total. Cada frase de 6 a 14 palabras, una idea y UNA imagen posible por frase (algo que se pueda ver: una acción, un objeto, un lugar).
3. Primera frase: gancho que atrapa en un segundo (error común, pregunta directa o "nadie te cuenta esto"). Sin saludar ni presentarte.
4. Habla de tú, como un amigo que sabe del tema. Lenguaje llano; si usas un término técnico, lo explicas en la misma frase. Nada de frases vacías ("es muy importante", "marca la diferencia") ni relleno.
5. Nada de vender: ni precios, ni ofertas, ni "apúntate". La última frase invita con naturalidad a seguir la cuenta para más consejos.
6. NO inventes cifras, porcentajes, tiempos, estudios, testimonios ni nombres. Si no estás seguro de un número, explica la idea sin número ("un poco antes", "con menos fuerza").
6b. NO inventes funciones, apps, herramientas, IA ni servicios del negocio: del negocio solo puedes decir lo que pone en NEGOCIO. Si el tema o el borrador hablan de algo que NEGOCIO no menciona, reconviértelo en un consejo práctico del oficio sobre ese mismo asunto (qué hacer tú, paso a paso) sin atribuírselo al negocio.
6c. Español de España: tuteo, NUNCA voseo (prohibido "aplicás", "tenés", "podés", "sabés", "querés", "mirá"…), vocabulario peninsular (coche, no carro; neumático, no llanta; conducir, no manejar).
7. Sin emojis, hashtags, comillas ni @usuarios. Números pequeños con letra.
8. "cierre_bio": frase alternativa de cierre que menciona con naturalidad el enlace del perfil.
9. "caption": texto de la publicación, 2-3 frases cortas que resumen el consejo y terminan con una pregunta a la audiencia. Mismas reglas: sin cifras inventadas, sin vender, sin hashtags.

Responde SOLO con JSON: {{"entrada":"la entrada del conocimiento que explicas (o vacío)","tema":"tema final, corto","gancho":"gancho final (sin cifras)","frases":["…", …],"cierre_bio":"…","caption":"…"}}"""
    extra = ""
    for _ in range(3):
        try:
            j = writer_json(base + extra, key, log)
        except Exception as e:
            log(f"    ⚠ Guionista: {e}")
            return False
        fr = [re.sub(r"\s+", " ", str(x)).strip() for x in (j.get("frases") or []) if str(x).strip()]
        words = sum(len(x.split()) for x in fr)
        nums = [x for x in fr + [str(j.get("gancho") or "")] if re.search(r"\d", x) and not re.search(r"\d", saber or "x")]
        if saber:
            nums = [x for x in fr + [str(j.get("gancho") or "")] if any(n not in saber for n in re.findall(r"\d+(?:[.,]\d+)?", x))]
        if nums:
            extra = f"\n\nTu respuesta anterior tenía cifras que no están en el conocimiento ({nums[0][:80]}). Quita todas las cifras."
            continue
        vos = [w for x in fr for w in VOSEO.findall(x)]
        if vos:
            extra = f"\n\nTu respuesta anterior usaba voseo ({', '.join(sorted(set(vos))[:6])}). Reescríbela en español de España, con tuteo."
            continue
        if nmin - 1 <= len(fr) <= nmax + 2 and words >= wmin - 8:
            old = piece.get("beats") or []
            bio = str(j.get("cierre_bio") or piece.get("cierre_bio") or "").strip()
            if bio and old and piece.get("cierre_bio") and old[-1].get("t") == piece.get("cierre_bio"):
                fr[-1] = bio  # a este vídeo le tocaba cerrar con el enlace del perfil
            piece["beats"] = [{"t": de_voseo(x)[:220], "img": ""} for x in fr[:14]]
            piece["guion"] = [b["t"] for b in piece["beats"]]
            if bio:
                piece["cierre_bio"] = bio[:200]
            cap = de_voseo(str(j.get("caption") or "").strip())
            if len(cap) > 40:
                piece["caption"] = cap[:600]
            for k in ("tema", "gancho"):
                v = de_voseo(str(j.get(k) or "").strip())
                if 3 < len(v) < 120:
                    piece[k] = v
            if j.get("entrada"):
                log(f"    📚 Basado en: {str(j['entrada'])[:90]}")
                USED.setdefault(user.get("id"), []).append(str(j["entrada"]).split(":")[0][:60])
            review_script(piece, negocio, key, log, saber)
            return True
        extra = f"\n\nTu respuesta anterior tenía {len(fr)} frases y {words} palabras: NO cumple. Escribe entre {nmin} y {nmax} frases y al menos {wmin} palabras."
    log("    ⚠ Guionista: no cumplió las reglas; se mantiene el original")
    for b in piece.get("beats") or []:
        b["t"] = de_voseo(b.get("t", ""))
    return False


FILLER = re.compile(r"\b(mejora tu|mejorar tu|practica|practicar|anticipa|conduce con|sigue practicando|no te rindas|la clave del éxito|ganar? tiempo|el secreto)\b", re.I)


def _overlap(a, b):
    wa = {w for w in re.findall(r"\w{4,}", a.lower())}
    wb = {w for w in re.findall(r"\w{4,}", b.lower())}
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def write_slides(piece, user, key, log=print):
    """Redactor de carruseles y posts: diapositivas que enseñan una idea concreta, sacada del conocimiento verificado."""
    carr = piece.get("tipo") == "carrusel"
    nmin, nmax = (4, 7) if carr else (1, 1)
    marca = ((user.get("config") or {}).get("marca") or {})
    negocio = json.dumps({k: marca.get(k) for k in ("name", "about", "aud", "pillars", "tone") if marca.get(k)}, ensure_ascii=False)[:1500]
    saber = knowledge_for(user)
    old = [f"{e.get('titulo', '')}: {e.get('texto', '')}" for e in piece.get("escenas") or []]
    prompt = f"""Eres redactor de {'carruseles' if carr else 'posts de imagen'} de Instagram que la gente guarda porque ENSEÑAN algo útil.
NEGOCIO: {negocio}
TEMA: {piece.get('tema', '')}
GANCHO PROPUESTO: {piece.get('gancho', '')}
BORRADOR: {' | '.join(old)[:900]}
""" + (f"""
CONOCIMIENTO VERIFICADO DEL SECTOR (elige UNA entrada que encaje con el tema, o la más cercana cambiando el tema; todo lo técnico sale de ahí):
{saber}
""" if saber else "") + f"""
REGLAS
- Entre {nmin} y {nmax} {'diapositivas' if carr else 'bloques'}: cada una con "titulo" (2-6 palabras) y "texto" (8-20 palabras) que se pueda leer de un vistazo.
- {'PORTADA (la 1ª): una promesa o un error CONCRETO del tema ("El error que te hace bloquear en cada frenada"), nada de "el secreto", "trucos" o "ganar tiempo" a secas; su texto dice qué vas a aprender. CONTENIDO (de la 2ª a la penúltima): cada diapositiva enseña UNA cosa DISTINTA y concreta sacada del CONOCIMIENTO: qué pasa, por qué pasa, cómo se hace paso a paso, cómo notar si lo haces bien, el error típico y su arreglo. PROHIBIDO rellenar: nada de diapositivas que solo motivan o repiten otra con otras palabras ("mejora tu pilotaje", "conduce con antelación", "practica cada día", "anticipa cada movimiento"). Si el conocimiento no da para más, haz MENOS diapositivas. CIERRE (la última): resume la idea en una frase y pide guardar el post o comentar.' if carr else 'Un solo bloque con una idea clara, concreta y aplicable sacada del CONOCIMIENTO: el título dice el error o el consejo y el texto explica el cómo. Nada de frases vagas ("mejora tus tiempos", "pierdes valioso tiempo").'}
- Todo lo que dices tiene que ser CORRECTO según el CONOCIMIENTO; si algo no aparece ahí, no lo digas. Nada de exageraciones ("duplica", "triplica", "x2", "el truco definitivo") ni de decir qué hace la app o el producto del negocio si NEGOCIO no lo explica.
- Español de España, tuteo, sin voseo y sin palabras de Latinoamérica (di coche, no carro).
- Nada de cifras, tiempos, porcentajes, ofertas, testimonios ni funciones del negocio que no estén en NEGOCIO o en el CONOCIMIENTO.
- Sin emojis ni hashtags. No menciones circuitos, curvas, coches ni pilotos concretos (sí puedes nombrar el simulador si el consejo vale tal cual en él).
- "caption": 2-3 frases que resumen ESTE MISMO consejo (no otro tema) y acaban con una pregunta.

Responde SOLO con JSON: {{"tema":"…","gancho":"…","escenas":[{{"titulo":"…","texto":"…"}}],"caption":"…"}}"""
    for _ in range(3):
        try:
            j = writer_json(prompt, key, log)
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ Redactor: {e}")
            return False
        es = [{"titulo": de_voseo(str(x.get("titulo", "")).strip())[:60], "texto": de_voseo(str(x.get("texto", "")).strip())[:200]}
              for x in (j.get("escenas") or []) if isinstance(x, dict) and str(x.get("titulo", "")).strip()]
        txt = " ".join(e["titulo"] + " " + e["texto"] for e in es) + " " + str(j.get("gancho", ""))
        bad = [n for n in re.findall(r"\d+(?:[.,]\d+)?", txt) if n not in (saber or "")]
        relleno = [e["titulo"] for e in es[1:-1] if FILLER.search(e["titulo"] + " " + e["texto"]) and not re.search(r"\b(porque|si |cuando|para que)\b", e["texto"], re.I)]
        dup = [es[k]["titulo"] for k in range(1, len(es)) for m in range(k) if _overlap(es[k]["titulo"] + " " + es[k]["texto"], es[m]["titulo"] + " " + es[m]["texto"]) > 0.6]
        hype = [e["titulo"] for e in es if re.search(r"\b(duplica|triplica|multiplica|x2|definitiv)", e["titulo"] + " " + e["texto"], re.I)]
        relleno += hype
        if nmin <= len(es) <= nmax + 1 and not bad and not relleno and not dup:
            prev = piece.get("escenas") or []
            piece["escenas"] = [{**(prev[k] if k < len(prev) else {}), **e} for k, e in enumerate(es[:nmax])]
            for k in ("tema", "gancho"):
                v = de_voseo(str(j.get(k) or "").strip())
                if 3 < len(v) < 120:
                    piece[k] = v
            cap = de_voseo(str(j.get("caption") or "").strip())
            if len(cap) > 40:
                piece["caption"] = cap[:600]
            return True
        why = (f"cifras inventadas: {', '.join(bad[:4])}" if bad else f"diapositivas de relleno: {', '.join(relleno[:3])}" if relleno
               else f"diapositivas repetidas: {', '.join(dup[:3])}" if dup else f"{len(es)} diapositivas")
        prompt += f"\n\nTu respuesta anterior no cumplía ({why}). Corrígelo."
    log("    ⚠ Redactor: no cumplió las reglas; se mantiene el borrador")
    return False


HASH_BAN = {"linkenbio", "linkinbio", "viral", "fyp", "foryou", "parati", "reels", "reel", "instagood", "instagram", "explore", "explorepage", "trending", "follow", "like4like"}


def _tags(raw, marca_name):
    out = []
    for h in re.findall(r"#?([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_]{3,30})", str(raw or "")):
        t = h.lower()
        if t in HASH_BAN or t in out or t.isdigit():
            continue
        out.append(t)
    brand = re.sub(r"[^a-z0-9ñ]", "", str(marca_name or "").lower())
    if brand and 3 <= len(brand) <= 25:
        out = [t for t in out if t != brand][:4] + [brand]
    return " ".join("#" + t for t in out[:5])


def write_caption(piece, user, key, log=print):
    """Pie de foto que funciona por sí solo: primera línea que engancha, el consejo resumido (sirve aunque no veas
    el vídeo), una llamada a la acción concreta y 3-5 hashtags relevantes."""
    if piece.get("tipo") == "historia":
        return False
    marca = ((user.get("config") or {}).get("marca") or {})
    negocio = json.dumps({k: marca.get(k) for k in ("name", "about", "aud", "tone") if marca.get(k)}, ensure_ascii=False)[:900]
    if piece.get("beats"):
        contenido = " ".join(str(b.get("t", "")) for b in piece["beats"])
    else:
        contenido = " | ".join(f"{e.get('titulo', '')}: {e.get('texto', '')}" for e in piece.get("escenas") or [])
    saber = knowledge_for(user)
    formato = {"reel": "un reel", "carrusel": "un carrusel", "post": "un post de imagen"}.get(piece.get("tipo"), "una publicación")
    prompt = f"""Escribe el pie de foto (caption) de Instagram para {formato}.
NEGOCIO: {negocio}
TEMA: {piece.get('tema', '')}
LO QUE DICE LA PUBLICACIÓN: {contenido[:1500]}

REGLAS
- Línea 1 (máximo 100 caracteres): engancha con el problema o el resultado, usando las palabras que alguien buscaría (por ejemplo "Si bloqueas al frenar, esto es lo que falla"). No empieces con "¿Sabías que", "Descubre" ni "En este vídeo".
- Después, en 2-4 líneas cortas, el consejo resumido para que sirva aunque no vean la publicación: qué hacer y por qué. Puedes usar líneas que empiecen por "→". Solo cosas que dice la publicación; nada nuevo.
- Última línea: UNA llamada a la acción concreta y relacionada (guardarlo para la próxima sesión, comentar cómo lo hacen ellos, o comentar una palabra). Nada de "link en la bio" ni "sígueme para más".
- Español de España, tuteo, sin voseo. Como mucho 1 emoji en todo el texto. Nada de cifras que no estén en la publicación, ni exageraciones ("duplica", "x2"), ni funciones de la app o el producto que NEGOCIO no explique.
- "hashtags": de 3 a 5, en minúsculas y sin tildes, relevantes para este tema (uno general del sector y el resto concretos del tema). Nada de #viral, #reels, #linkenbio ni parecidos.

Responde SOLO con JSON: {{"caption":"…","hashtags":"#… #… #…"}}"""
    for _ in range(2):
        try:
            j = writer_json(prompt, key, log)
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠ Caption: {e}")
            return False
        cap = de_voseo(str(j.get("caption") or "").strip())
        cap = re.sub(r"[ \t]+\n", "\n", cap)
        cap = re.sub(r"\n{3,}", "\n\n", cap)
        bad = [n for n in re.findall(r"\d+(?:[.,]\d+)?", cap) if n not in contenido and n not in (saber or "")]
        first = cap.split("\n", 1)[0]
        if 60 <= len(cap) <= 900 and len(first) <= 130 and not bad and not re.search(r"link en (la )?bio|sígueme para", cap, re.I):
            piece["caption"] = cap
            tags = _tags(j.get("hashtags"), marca.get("name"))
            if tags:
                piece["hashtags"] = tags
            log(f"    🏷 Caption: «{first[:70]}» · {piece.get('hashtags', '')}")
            return True
        prompt += "\n\nTu respuesta anterior no cumplía las reglas (" + (f"cifras inventadas: {', '.join(bad[:3])}" if bad else "longitud o llamada a la acción") + "). Corrígelo."
    log("    ⚠ Caption: no cumplió las reglas; se mantiene el anterior")
    piece["hashtags"] = _tags(piece.get("hashtags"), marca.get("name")) or piece.get("hashtags", "")
    return False


def beats_of(piece):
    """Momentos de una pieza que no es faceless (gancho, escenas, cierre), para dirigir sus imágenes."""
    out = [{"t": piece.get("gancho", ""), "img": piece.get("visual", "")}]
    for e in piece.get("escenas") or []:
        out.append({"t": f"{e.get('titulo', '')}. {e.get('texto', '')}".strip(". "), "img": e.get("visual", "")})
    return [b for b in out if b["t"]]


SHOTS = [
    "plano general que sitúa: la persona y el lugar completos (solo este uno en todo el vídeo)",
    "primerísimo plano de detalle del objeto o pieza exacta de la que se habla",
    "primer plano de las manos haciendo la acción",
    "plano medio de la persona de frente, con la cara y la expresión que pide la frase",
    "vista por encima del hombro hacia la pantalla o lo que mira la persona",
    "primer plano de los pies o de otra parte del cuerpo haciendo la acción",
    "plano cenital o lateral cercano del equipo en uso",
]


def shot_rules(user):
    """Planos obligados por el contenido de la frase (del banco de conocimiento del sector, «planos:»).
    Devuelve [(regex, plano en inglés)]: si la frase habla de los pedales, se ven los pies; del volante, las manos…"""
    marca = ((user.get("config") or {}).get("marca") or {})
    hay = " ".join(str(marca.get(k, "")) for k in ("name", "about", "aud", "pillars", "tone")).lower()
    out = []
    for f in sorted(KNOW_DIR.glob("*.md")):
        txt = f.read_text(encoding="utf-8")
        m = re.search(r"claves:\s*\[(.*?)\]", txt)
        claves = [c.strip().lower() for c in (m.group(1).split(",") if m else []) if c.strip()]
        if not any(c and c in hay for c in claves):
            continue
        for line in re.findall(r'^\s*-\s*"(.+?=>.+?)"\s*$', txt.split("\n---", 1)[0], re.M):
            keys, shot = line.split("=>", 1)
            out.append((re.compile(r"\b(" + keys.strip() + r")", re.I), shot.strip()))
    return out


def shot_for(text, rules):
    """El plano que pide la frase: la primera regla (en orden de prioridad del banco) cuya palabra clave aparece."""
    for rx, shot in rules:
        if rx.search(text):
            return shot
    return ""


def _subject(shot):
    """Lo esencial de un plano obligado (por ejemplo "feet on the pedals"), para comprobar que la imagen lo respeta."""
    m = re.search(r"(?:of|showing)\s+(?:the\s+)?(?:driver's\s+)?(.+?)(?:\s+(?:of|in|from|at)\s|,|$)", shot.lower())
    return (m.group(1) if m else shot.lower())[:30]


def art_direct(piece, user, key, log=print, beats=None):
    """Director de arte: convierte cada frase hablada de un vídeo faceless en una imagen concreta,
    fotografiable y anclada al mundo del negocio, para que lo que se ve sea lo que se dice."""
    beats = beats if beats is not None else (piece.get("beats") or [])
    if len(beats) < 2:
        return False
    marca = ((user.get("config") or {}).get("marca") or {})
    negocio = json.dumps({k: marca.get(k) for k in ("name", "about", "aud", "pillars") if marca.get(k)}, ensure_ascii=False)[:1200]
    # variedad de planos, como un vídeo editado de verdad: casi todo cerca y como mucho un plano general
    close = SHOTS[1:]
    order = [0, 2, 3, 1, 4, 5]  # detalle, manos, cara, pantalla, pies, cenital
    plan = [close[order[i % len(order)]] for i in range(len(beats))]
    if len(beats) > 3:
        plan[0] = SHOTS[0]
    rules = shot_rules(user)
    forced = [shot_for(str(b.get("t", "")), rules) for b in beats]
    # si el mismo plano obligado se repite, se mantiene el sujeto (lo que se habla) pero cambia el ángulo o el momento
    ANGULOS = ["", "side view, ", "low angle, ", "top-down view, ", "wide close-up, "]
    vistos = {}
    for k, f in enumerate(forced):
        if f:
            n = vistos.get(f, 0)
            vistos[f] = n + 1
            if n:
                forced[k] = ANGULOS[n % len(ANGULOS)] + f
    frases = "\n".join(f"{i + 1}. [PLANO {'OBLIGATORIO' if forced[i] else 'SUGERIDO'}: {forced[i] or plan[i]}] {b.get('t', '')}" for i, b in enumerate(beats))
    prompt = f"""Eres director de arte de vídeos cortos faceless (todo con imágenes generadas por IA). Para cada frase hablada eliges UNA imagen que la ilustre de forma que cualquiera entienda la relación al instante.

NEGOCIO: {negocio}
TEMA DEL VÍDEO: {piece.get('tema', '')}

FRASES (en orden):
{frases}

REGLAS
0. Cada frase trae un PLANO. Si es OBLIGATORIO, la imagen empieza EXACTAMENTE por ese plano (cópialo en inglés tal cual, con su ángulo) y luego dices qué se ve en ese momento, según la frase: si la frase habla de un error, que se vea en el cuerpo o el gesto (el pie soltando de golpe, las manos girando bruscamente, la cara de fastidio); si habla de la solución, el gesto bien hecho. NUNCA pidas que el coche del juego derrape, trompee, gire sobre sí mismo o se salga: las IAs de vídeo lo pintan mal (el coche da vueltas raras o mira al revés). En la pantalla del juego, siempre vista desde dentro del coche, avanzando suave hacia delante (por ejemplo "…, slowly easing off the brake pedal while turning in"). Si es SUGERIDO, sirve para que el vídeo cambie de plano en cada frase. Lo primero es que la imagen muestre LO QUE DICE LA FRASE (si habla de girar el volante, manos en el volante; de frenar, el pie en el freno; de ver la curva, la pantalla). Si el plano sugerido no encaja, elige otro de cerca que sí encaje, sin repetir el plano de la frase anterior y sin más planos generales. Si la frase describe un error, muestra el error; si describe la solución, muestra la solución hecha bien.
1. "mundo": en INGLÉS y en MÁXIMO 18 palabras, el escenario concreto y reconocible de este negocio que aparece en TODAS las imágenes, diciendo SIEMPRE en qué SALA o LUGAR está y con qué NO debe confundirse (por ejemplo, para simracing: "aluminium sim racing rig with bucket seat, steering wheel and pedals inside a bright home gaming room, a big monitor showing a racing game, not a real car interior"; para una tienda de deporte: "sports store with football boots and jerseys on display"; para un hotel: "bright seaside boutique hotel in Mallorca"). Si la imagen pudiera parecer otra cosa genérica (un coche real, una tienda cualquiera), el "mundo" debe dejar claro la diferencia.
2. Cada "img": en INGLÉS sencillo, máximo 28 palabras, EMPEZANDO por el tipo de plano en inglés ("extreme close-up of…", "close-up of hands…", "medium shot of…", "over-the-shoulder view of…") y luego qué se ve y qué está haciendo quién, con el SUJETO principal que se vea grande, nítido y de frente. Una sola idea por imagen. Debe mostrar literalmente la acción u objeto de la frase dentro del "mundo".
2b. PROHIBIDO lo genérico: nada de "a car", "a sports car on a road", "a person smiling", "a city", paisajes ni planos de recurso que valdrían para cualquier vídeo. Cada imagen tiene que contener algo que SOLO tiene sentido con esa frase y con este negocio: la persona del vídeo haciendo esa acción concreta con el equipo o el producto del negocio, el objeto exacto del que se habla en primer plano, o la pantalla/el lugar exacto. Si una imagen podría ilustrar otra frase del vídeo, cámbiala.
3. Si la frase es abstracta o técnica (datos, ajustes, una sensación, un consejo), tradúcela a algo fotografiable: un objeto o una pantalla con gráficos de colores sin texto legible, una mano haciendo la acción, una cara con la emoción, un antes/después visible. Nunca dejes conceptos sin cuerpo como "pressure" o "balance" sin un objeto que los muestre.
4. Sin texto, letras, números ni logos en la imagen: las pantallas solo muestran imagen del juego o gráficos de colores, NUNCA webs, perfiles, menús ni apps con texto. Para frases de cierre ("sígueme", "enlace del perfil") muestra a la persona sonriendo a cámara o un gesto, no una pantalla. Sin famosos. Las personas son anónimas y consistentes (misma persona en todo el vídeo).
5. "estilo": en INGLÉS, la misma fotografía para todo el vídeo: luz brillante y nítida, colores vivos, nada de desenfoques ni oscuridad.

7. "evitar": en INGLÉS, máximo 10 palabras, con qué se podría confundir la imagen y no debe parecerse (por ejemplo "not a real car interior, no dashboard").
6. "persona": en INGLÉS, máximo 12 palabras, cómo es la persona del vídeo (edad aproximada, pelo, ropa), para que sea siempre la misma.

Responde SOLO con JSON: {{"mundo":"…","persona":"…","evitar":"…","estilo":"…","imgs":["…", …]}} con exactamente {len(beats)} elementos en "imgs"."""
    try:
        j = writer_json(prompt, key, log)
    except Exception as e:
        log(f"    ⚠ Director de arte: {e}")
        return False
    imgs = [str(x).strip() for x in (j.get("imgs") or []) if str(x).strip()]
    if len(imgs) < len(beats):
        log(f"    ⚠ Director de arte: devolvió {len(imgs)} de {len(beats)} imágenes; se mantienen las originales")
        return False
    malas = [k for k in range(len(beats)) if forced[k] and _subject(forced[k]) not in imgs[k].lower()]
    if malas:  # una segunda oportunidad, diciéndole exactamente qué imágenes no respetan su plano
        try:
            j2 = writer_json(prompt + "\n\nEn tu respuesta anterior, las imágenes " + ", ".join(str(k + 1) for k in malas)
                             + " NO empezaban por su PLANO OBLIGATORIO. Rehaz la lista completa respetándolos.", key, log)
            imgs2 = [str(x).strip() for x in (j2.get("imgs") or []) if str(x).strip()]
            if len(imgs2) >= len(beats) and sum(1 for k in range(len(beats)) if forced[k] and _subject(forced[k]) not in imgs2[k].lower()) < len(malas):
                j, imgs = {**j, **{k2: v for k2, v in j2.items() if v}}, imgs2
        except Exception:  # noqa: BLE001
            pass
    mundo = str(j.get("mundo") or "").strip()[:260]
    persona = str(user.get("persona_veo") or j.get("persona") or "").strip()[:110]  # presentador fijo de la marca, si lo tiene
    piece["persona"] = persona
    mundo_no = str(j.get("evitar") or "").strip()[:160]
    for k, (b, im) in enumerate(zip(beats, imgs)):
        if forced[k] and _subject(forced[k]) not in im.lower():
            log(f"    ↺ Plano corregido en la frase {k + 1}: debía ser «{forced[k][:50]}…»")
            im = forced[k]  # sin el resto: mejor un plano sencillo y correcto que uno que mezcle cosas que no van
        # el plano y el sujeto primero (si el escenario va delante, todas salen como plano general); el escenario después
        detalle = re.search(r"\b(close-up|extreme close-up|macro)\b", im, re.I) and re.search(r"\b(feet|foot|hands?|pedals?|wheel|fingers?)\b", im, re.I)
        if detalle:
            # primer plano de pies o manos: nada de describir a la persona ni la sala entera (si no, la IA pinta un retrato
            # y la sala, y el detalle del que se habla desaparece)
            b["img"] = (im.rstrip(".") + ". Tight framing: only that detail fills the image, no face, no monitor, no room"
                        + (". " + mundo_no if mundo_no else ""))[:800]
            continue
        who = ("; the person is " + persona) if persona and re.search(r"\b(person|man|woman|gamer|driver|he|she|his|her|face|racer|player)\b", im, re.I) else ""
        b["img"] = (im.rstrip(".") + who + (". Setting: " + mundo if mundo else "") + (". " + mundo_no if mundo_no else ""))[:800]
    if j.get("estilo"):
        piece["estilo_visual"] = str(j["estilo"])[:200]
    return True
