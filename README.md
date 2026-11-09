# 🧠 Agencia de marketing IA (gratis, en GitHub)

Una agencia de marketing que funciona sola en tu cuenta de GitHub:

1. **Planifica la semana** como lo haría una agencia de verdad. Decide qué días publicar y cuáles descansar, qué formato usar (reel, historia, post o carrusel) y a qué hora, sin saturar a tu audiencia.
2. **Crea el contenido:** textos, imágenes con IA, vídeos animados, **locución** cuando el vídeo explica algo y **música** acorde a cada pieza.
3. **Publica en Instagram** a la hora elegida, sin que tengas que abrir nada.

Todo con servicios gratuitos: GitHub Actions, GitHub Pages, Groq (IA de texto), Pollinations (imágenes), voces de Microsoft (edge-tts) y la API oficial de Instagram.

> Cada persona que quiera usarla crea **su propia copia** con el botón **Use this template**, pone sus claves y su marca, y ya está.

---

## ⚙️ Puesta en marcha (unos 20 minutos, una sola vez)

### 1. Crea tu copia del repositorio
- Pulsa **Use this template → Create a new repository**.
- Hazlo **público**: GitHub Pages es gratis en repositorios públicos. Tus claves no se ven, porque van en *Secrets*.

### 2. Activa GitHub Pages
**Settings → Pages → Build and deployment → Source: _GitHub Actions_.**
Tu web quedará en `https://TU-USUARIO.github.io/NOMBRE-DEL-REPO/`. Ahí está también la agencia en modo manual, para revisar o crear piezas a mano.

### 3. Configura tu marca
- Abre tu web de Pages, pulsa **✦ Mi marca**, pega el texto de tu web y analízala.
- Pulsa **⬇ Descargar agency.config.json (para GitHub)** y sube ese archivo a la raíz del repositorio, sustituyendo el de ejemplo.
- También puedes editar `agency.config.json` a mano. Ajustes útiles en `plan`:
  - `max_por_dia`: máximo de piezas en un mismo día (por defecto 2).
  - `max_semana`: máximo de piezas por semana, historias incluidas (por defecto 7).
  - `formatos`: quita los formatos que no quieras.

### 4. Consigue las claves
| Secreto | Para qué | Dónde se saca |
|---|---|---|
| `GROQ_API_KEY` | El cerebro de la agencia | console.groq.com → API Keys (gratis) |
| `IG_USER_ID` | Tu cuenta de Instagram | Ver paso 5 |
| `IG_ACCESS_TOKEN` | Permiso para publicar | Ver paso 5 |
| `POLLINATIONS_TOKEN` *(opcional)* | Imágenes más rápidas y sin marca de agua | enter.pollinations.ai (gratis) |
| `GH_PAT` *(opcional)* | Renovar solo el token de Instagram | GitHub → Settings → Developer settings → Fine-grained token con permiso **Secrets: Read and write** en este repo |

Guárdalas en **Settings → Secrets and variables → Actions → New repository secret**.

### 5. Conecta Instagram (API oficial)
Necesitas una **cuenta profesional** de Instagram (de empresa o de creador).
1. Entra en **developers.facebook.com**, crea una app y añade el producto **Instagram** con la opción **API con inicio de sesión de Instagram**.
2. Añade tu cuenta de Instagram y genera un **token de acceso**. Cópialo en `IG_ACCESS_TOKEN`.
3. El **ID de usuario de Instagram** aparece en esa misma pantalla. Cópialo en `IG_USER_ID`.
4. Permisos necesarios: `instagram_business_basic` e `instagram_business_content_publish`.

El token dura 60 días. Si añades `GH_PAT`, el flujo **🔑 Renovar token** lo renueva solo los días 1 y 15 de cada mes. Si no, renuévalo tú antes de que caduque.

> Si tu token es de *Facebook Login* (cuentas ligadas a una página de Facebook), crea la variable `IG_API_HOST` con el valor `graph.facebook.com`.

### 6. Música (recomendado)
Sube pistas libres de derechos (por ejemplo, de la biblioteca de música de Pixabay o de la Biblioteca de audio de YouTube) a las carpetas de `assets/music/`:
`energetica/`, `chill/`, `epica/`, `emotiva/`, `divertida/` y `tension/`.
La agencia elige el ambiente de cada vídeo y toma una pista de esa carpeta. Con 2 o 3 pistas por carpeta basta.

### 7. Pruébalo
- **Actions → 🧠 Planificar y crear contenido → Run workflow.** En unos minutos verás el plan en `content/plans/` y los archivos en `docs/media/`.
- Para probar sin publicar de verdad, crea la variable `DRY_RUN` con valor `1` (**Settings → Variables**) y ejecuta **📤 Publicar en Instagram**. Cuando todo esté bien, borra la variable.

---

## 🔁 Cómo funciona cada día
- **🧠 Planificar** (cada mañana): si aún quedan piezas programadas para los próximos días, no hace nada. Si no, planifica la siguiente semana, crea todo el contenido y lo deja en la cola (`content/queue.json`).
- **📤 Publicar** (cada 20 minutos): publica en Instagram lo que ya toca. Si algo falla, lo reintenta hasta 4 veces. El estado de cada pieza queda guardado en la cola.
- Los archivos antiguos ya publicados se borran solos a los 10 días, para que el repositorio no crezca sin fin.

## 🔊 Sonido
Para cada reel o historia, la agencia decide:
- **Voz + música:** para vídeos que explican algo (tips, errores, mitos). Escribe un guion hablado por pantalla, y cada pantalla dura lo que tarda la voz en decir su frase. La música va baja, por debajo de la voz.
- **Solo música:** para piezas rápidas, estéticas o emocionales.
- **Voz** (mujer u hombre), **ritmo** (tranquilo, normal o enérgico) y **ambiente musical**.

Para cambiar las voces, añade esto a `agency.config.json`:
```json
"voces": { "mujer": "es-ES-ElviraNeural", "hombre": "es-ES-AlvaroNeural" }
```
(Hay voces de México, Argentina y otros países: `es-MX-DaliaNeural`, `es-AR-TomasNeural`…)

## ⚠️ Límites conocidos
- **Historias:** la API de Instagram no permite añadir stickers (encuestas, enlaces…). En automático, la historia termina invitando a responder por mensaje directo. Si quieres encuestas, crea esas historias desde la web y súbelas a mano.
- **Horarios:** GitHub puede retrasar unos minutos las tareas programadas en horas punta, así que la publicación puede salir entre 5 y 30 minutos después de la hora prevista.
- **Vídeos de stock:** en automático se usan imágenes IA animadas (los clips de stock solo funcionan en la web).
- **Servicios gratuitos:** Groq, Pollinations y las voces de Microsoft son gratis con límites de uso y pueden cambiar sus condiciones. Revisa las condiciones de Pollinations sobre uso comercial.

## 🧪 Prueba sin internet (para desarrolladores)
```bash
pip install -r requirements.txt && python -m playwright install chromium
python tests/run_mock.py
```
Simula Groq, Pollinations, la voz e Instagram, y comprueba todo el recorrido: plan → render → vídeo con audio → publicación.
