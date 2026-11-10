# 🧠 Agencia de marketing IA

Una agencia de marketing que funciona sola para varias personas a la vez:

1. **Planifica la semana** como una agencia de verdad: qué días publicar y cuáles descansar, qué formato usar (reel, historia, post o carrusel) y a qué hora, sin saturar a la audiencia.
2. **Crea el contenido:** textos, imágenes con IA, vídeos animados, **locución** cuando el vídeo explica algo y **música** acorde a cada pieza.
3. **Publica en Instagram, TikTok y YouTube** de cada persona a la hora elegida, sin que nadie tenga que abrir nada.

Cada persona solo tiene que **entrar en la web con un código de invitación**, pegar su web para que la agencia saque su ADN de marca y conectar sus redes con un clic (cualquier cuenta de Instagram, TikTok o YouTube). La IA, las imágenes y la publicación usan **tus** claves, que están guardadas en el servidor y nunca llegan al navegador.

```
 Web (GitHub Pages)  ──►  Servidor (Cloudflare Worker)  ◄──  GitHub Actions
 cada persona entra,      guarda tus claves, cuentas,        planifica, crea los vídeos
 configura su marca y     marcas y tokens de Instagram       y publica a su hora
 conecta Instagram
```

Todo funciona con planes gratuitos: GitHub Actions y Pages, Cloudflare Workers, Groq, Pollinations, las voces de Microsoft (edge-tts) y y [Zernio](https://zernio.com) para publicar en redes (gratis para las 2 primeras cuentas conectadas, publicaciones ilimitadas).

---

## 🛠️ Puesta en marcha (solo tú, una vez)

### 1. Servidor (Cloudflare Workers, gratis)
Necesitas Node.js instalado y una cuenta gratuita de Cloudflare.

**Opción rápida (recomendada):** un instalador lo hace todo y te va pidiendo las claves:
```bash
cd worker
npm install
npm run setup
```

**Opción manual:**
```bash
cd worker
npm install
npx wrangler login
npx wrangler kv namespace create KV        # copia el "id" que te devuelve en wrangler.toml
npx wrangler secret put GROQ_API_KEY       # tu clave de console.groq.com
npx wrangler secret put POLLINATIONS_TOKEN # opcional: token gratis de enter.pollinations.ai
npx wrangler secret put ADMIN_TOKEN        # inventa una contraseña larga (la usará GitHub Actions)
npx wrangler secret put INVITE_CODES       # códigos de invitación separados por comas: "jaime-2026,laura-7f3k"
npx wrangler deploy
```
Al terminar te dará una dirección como `https://agencia-marketing.TU-SUBDOMINIO.workers.dev`.
- Revisa en `worker/wrangler.toml` que `APP_URL` y `APP_ORIGINS` apunten a tu GitHub Pages.
- Pon esa dirección en **`docs/config.json`** (campo `api`) y súbelo.

### 2. Redes sociales (Zernio, recomendado)
Zernio hace de intermediario aprobado por Instagram, TikTok y YouTube, así que **cualquier cuenta** se conecta con un clic, sin apps de Meta ni revisiones.
1. Crea una cuenta gratis en [zernio.com](https://zernio.com) y genera una **API key** (*Settings → API Keys*).
2. Guárdala en el servidor y vuelve a desplegar:
   ```bash
   cd worker
   npx wrangler secret put ZERNIO_API_KEY
   npx wrangler deploy
   ```
3. Listo: en la web, **👤 Cuenta → 📲 Redes sociales → ＋ Instagram / ＋ TikTok / ＋ YouTube**.

Cada persona tiene su propio perfil en Zernio. La agencia sube los vídeos e imágenes y deja cada pieza **programada** a su hora: reels y vídeos van a las tres redes (TikTok y YouTube solo admiten vídeo), posts, carruseles e historias solo a Instagram.

### 3. Que la agencia arranque al momento (recomendado)
GitHub a veces retrasa horas (o no lanza) las tareas programadas de un repositorio nuevo. Con este token el servidor pone a trabajar a la agencia en cuanto alguien pulsa un botón en la app, y cada mañana lanza la planificación diaria:
1. github.com → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token*.
2. *Repository access*: **Only select repositories** → `marketing-agency`. *Permissions → Actions*: **Read and write**. Caducidad: la que quieras (al caducar, crea otro).
3. Guárdalo en el servidor y despliega:
   ```bash
   cd worker
   npx wrangler secret put GITHUB_TOKEN
   npx wrangler deploy
   ```

### 2b. Alternativa: app propia de Meta (solo Instagram, opcional)
Meta ya no permite en apps nuevas el inicio de sesión "solo Instagram". Se usa el **inicio de sesión unificado** (Facebook Login para empresas):
1. developers.facebook.com → crea una app con el caso de uso **Administrar mensajes y contenido en Instagram** y vincúlala a tu porfolio empresarial.
2. En *Casos de uso → API de Instagram → API setup with Unified login*: crea la configuración (plantilla) y registra la URL de redirección
   `https://TU-WORKER.workers.dev/auth/instagram/callback`.
3. Guarda en el servidor el **secreto de la app** (*Configuración de la app → Información básica → Clave secreta*):
   `npx wrangler secret put IG_APP_SECRET`
   (El ID de la app y el de la configuración ya vienen en `worker/src/index.js`; si creas otra app, cámbialos con las variables `IG_APP_ID` y `META_CONFIG_ID`).
4. Mientras la app no pase la revisión de Meta, solo pueden conectarse personas con un **rol en la app** (*Roles de la app → Roles → Testers*). Cada persona necesita una cuenta de Instagram **profesional** vinculada a un porfolio empresarial que administre.

Los tokens de este sistema no caducan.

### 3. GitHub
- **Settings → Pages → Source: GitHub Actions.** Tu web queda en `https://jaimelpdev.github.io/marketing-agency/`.
- **Settings → Secrets and variables → Actions:**
  - Secretos: `GROQ_API_KEY`, `AGENCIA_ADMIN_TOKEN` (la misma contraseña que `ADMIN_TOKEN`) y, si quieres, `POLLINATIONS_TOKEN`.
  - Variable: `AGENCIA_API` = la dirección de tu Worker.
- Sube pistas de música libres de derechos a `assets/music/<ambiente>/`. Los ambientes son `energetica`, `chill`, `epica`, `emotiva`, `divertida` y `tension`; con 2 o 3 pistas por carpeta basta.

### 4. Invitar a alguien
Pásale la dirección de la web y uno de tus códigos de invitación. Cada código sirve una sola vez. También puedes crear códigos nuevos al momento:
```bash
curl -X POST -H "Authorization: Bearer TU_ADMIN_TOKEN" https://agencia-marketing.TU-SUBDOMINIO.workers.dev/admin/invite
```

---

## 👤 Para quien usa la agencia (2 minutos)
1. Abre la web y entra con tu **código de invitación** y tu nombre.
2. Pega tu web y pulsa **Analizar mi marca**. Revisa el ADN de marca y pulsa **Todo listo**.
3. Pulsa **Conectar Instagram**.
4. ¡Listo! En **👤 Cuenta** puedes:
   - pausar o activar el piloto automático;
   - poner el máximo de publicaciones por semana y por día;
   - elegir qué formatos puede usar;
   - ver lo programado y lo publicado.

Guarda tu **clave de acceso** (en Cuenta) para entrar desde otro dispositivo.

---

## 🔁 Cómo funciona cada día
- **🧠 Planificar** (cada mañana): para cada persona con Instagram conectado y el piloto automático activado, si no le quedan piezas programadas más allá de mañana, planifica su próxima semana y crea todo el contenido.
- **📤 Publicar** (cada 20 minutos): publica lo que ya toca, a través del servidor. Si algo falla, lo reintenta hasta 4 veces. La app muestra el estado de cada pieza.
- Los archivos ya publicados se borran solos a los 10 días.
- Para lanzarlo a mano: *Actions → 🧠 Planificar → Run workflow*. Puedes elegir una sola persona y forzar un plan nuevo.
- Para probar sin publicar: crea la variable `DRY_RUN` con valor `1`.

## 🔊 Sonido
- En **👤 Cuenta → Sonido de los vídeos** cada persona elige: 🤖 que decida la agencia, 🎙 solo voz, 🎵 solo música o 🎙+🎵 voz y música.
- **Voz:** locuciones neuronales gratuitas de Microsoft (edge-tts), con el guion que escribe la agencia.
- **Música:** si pones tus pistas en `assets/music/<ambiente>/` (energetica, chill, epica, emotiva, divertida, tension) se usan esas. Si no, la agencia busca **canciones reales con licencia libre para uso comercial** (CC0 o CC BY, sobre todo de Jamendo, a través de Openverse) según el ambiente de cada vídeo, empieza la canción ya dentro (sin la intro lenta) y nivela el volumen. Con CC BY se acredita al artista en el texto del post (o abajo del todo en las historias). Las pistas se guardan en `content/music/` y se van reutilizando.

## 🚀 Contenido para enganchar
- **Subtítulos dinámicos** palabra a palabra, sincronizados con la voz (la palabra que se dice se ilumina en el color de tu marca), porque la mayoría ve los vídeos sin sonido.
- **Gancho con impacto:** tras la portada, el texto entra de golpe con destello, sacudida y rayos; confeti en el cierre.
- **Guiones pensados para retener:** fórmulas de gancho probadas, bucle abierto ("el último es el mejor"), re-enganche a mitad, final con recompensa y CTA de comentar una palabra clave.
- **Variedad obligatoria:** cada pieza usa un formato narrativo distinto (top, mito vs realidad, POV, antes y después, reto, comparativa, polémica…) y la agencia alterna estilo, fondo, animación del texto, música y gráficos para que ningún día se parezca al anterior.

## 🔎 No repetir contenido
Antes de planificar, la agencia lee las últimas publicaciones de cada red conectada (Instagram, TikTok y YouTube, **incluido lo que subiste a mano**) con sus me gusta y comentarios, y lo junta con lo que ya creó o programó ella. Con eso evita repetir temas, ganchos o enfoques, y aprovecha lo que mejor funciona con ángulos nuevos.

## 🎬 Vídeos animados
- **Gráficos animados en cada consejo** (como los creadores de vídeos educativos): comparación ❌/✅ que entra deslizando, checklist que se va marcando, velocímetro con aguja, barra de progreso, ruta con el coche recorriéndola, diana con flecha y contador. La agencia elige el que mejor explica cada escena.
- **Transiciones** con barrido de color entre escenas, zoom de impacto, acercamiento lento de cámara y partículas de fondo.
- **Efectos de sonido** sincronizados (whoosh en las transiciones, pop al aparecer pegatinas, ding al marcar un paso, golpe de la flecha), mezclados por debajo de la voz y la música.
- Los guiones siguen el estilo de los mejores reels: gancho en el primer segundo, una idea por escena y cierre con recompensa.
- Todo se puede desactivar en *Personaliza*.

## 🏎️ Pegatinas animadas y portadas
- En cada pieza la agencia elige de 3 a 5 emojis que ilustran el tema (por ejemplo 🏎️ 🏁 🛞 para tips de conducción) y salen en el vídeo **recortados como pegatinas** (borde blanco y sombra): rebotan, flotan, aparecen con un pop y los vehículos **cruzan la pantalla** con líneas de velocidad. Se pueden quitar en *Personaliza → Stickers animados*.
- **Portada:** cada reel lleva una portada llamativa (el gancho con la "mascota" enorme y un halo de color). Va como primer fotograma del vídeo, que es el que Instagram y TikTok usan de miniatura, y también se ve en la app.

## 📊 Stickers en historias
Instagram no deja que ningún programa ponga stickers interactivos de verdad (encuestas, preguntas, enlaces): ni esta agencia ni Metricool, Zernio o similares. Por eso la agencia **dibuja el sticker dentro de la historia** (una pregunta abierta para responder por mensaje directo, «link en la bio» o cuenta atrás; nada de encuestas, porque no se podrían votar), y la historia se publica sola.

## 🔄 Rehacer y reiniciar
- **🔄 en una pieza programada:** la agencia la vuelve a crear distinta (mismo formato y hora) y sustituye la anterior también en las redes.
- **🔄 Rehacer la semana:** rehace todo lo que aún no se ha publicado.
- **♻️ Reiniciar cuenta:** borra marca, plan, historial y archivos, y anula lo programado. Se mantienen la clave de acceso y las redes conectadas.
- Las peticiones se atienden solas: cada 20 minutos GitHub mira si hay alguna y pone a trabajar a la agencia (tarda entre 20 y 40 minutos en total).

## 🔒 Seguridad
- Tus claves de Groq y Pollinations y los tokens de Instagram **solo existen en el servidor** (secretos y KV de Cloudflare). La web y el repositorio no los contienen.
- Cada persona tiene un límite diario de uso de IA (`LLM_PER_DAY`, por defecto 60) y de imágenes (`IMG_PER_DAY`, por defecto 300), configurable en `wrangler.toml`, para que nadie gaste todo tu cupo gratuito.
- GitHub Actions accede al servidor con `ADMIN_TOKEN`. Si se filtra, cámbialo en ambos sitios.
- Al guardar tokens de Instagram de otras personas, eres responsable de custodiarlos. Cada persona puede desconectar su cuenta desde la app cuando quiera.

## ⚠️ Límites conocidos
- **Historias:** la API de Instagram no permite stickers interactivos; van dibujados dentro de la historia (ver arriba).
- **Horarios:** GitHub puede retrasar unos minutos las tareas programadas, así que la publicación puede salir entre 5 y 30 minutos después de la hora prevista.
- **Servicios gratuitos:** Groq, Pollinations y edge-tts son gratuitos con límites y pueden cambiar sus condiciones. Con muchas personas, el cupo de Groq se comparte entre todas.
- **El repositorio es público** (lo exige Pages gratis): los vídeos y las colas de publicación son visibles, aunque de todos modos se van a publicar en Instagram.

## 🧪 Pruebas
```bash
cd worker && node test/worker.test.mjs   # servidor: cuentas, límites, Instagram, admin
cd .. && pip install -r requirements.txt && python -m playwright install chromium
python tests/run_mock.py                 # recorrido completo simulado: plan → vídeo con voz → publicación
```
