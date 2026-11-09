# 🧠 Agencia de marketing IA

Una agencia de marketing que funciona sola para varias personas a la vez:

1. **Planifica la semana** como una agencia de verdad: qué días publicar y cuáles descansar, qué formato usar (reel, historia, post o carrusel) y a qué hora, sin saturar a la audiencia.
2. **Crea el contenido:** textos, imágenes con IA, vídeos animados, **locución** cuando el vídeo explica algo y **música** acorde a cada pieza.
3. **Publica en Instagram** de cada persona a la hora elegida, sin que nadie tenga que abrir nada.

Cada persona solo tiene que **entrar en la web con un código de invitación**, pegar su web para que la agencia saque su ADN de marca y pulsar **Conectar Instagram**. La IA, las imágenes y la publicación usan **tus** claves, que están guardadas en el servidor y nunca llegan al navegador.

```
 Web (GitHub Pages)  ──►  Servidor (Cloudflare Worker)  ◄──  GitHub Actions
 cada persona entra,      guarda tus claves, cuentas,        planifica, crea los vídeos
 configura su marca y     marcas y tokens de Instagram       y publica a su hora
 conecta Instagram
```

Todo funciona con planes gratuitos: GitHub Actions y Pages, Cloudflare Workers, Groq, Pollinations, las voces de Microsoft (edge-tts) y la API oficial de Instagram.

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

### 2. App de Instagram (Meta for Developers)
1. Entra en developers.facebook.com y crea una app. Añade el producto **Instagram** con la opción **API con inicio de sesión de Instagram**.
2. En la configuración de inicio de sesión, añade esta **URL de redirección**:
   `https://agencia-marketing.TU-SUBDOMINIO.workers.dev/auth/instagram/callback`
3. Copia el **ID** y el **secreto** de la app de Instagram y guárdalos en el servidor:
   ```bash
   npx wrangler secret put IG_APP_ID
   npx wrangler secret put IG_APP_SECRET
   ```
4. Mientras la app esté **en modo desarrollo**, solo pueden conectarse las cuentas que añadas como **testers de Instagram** (en *Roles de la app*). Cada persona acepta la invitación desde Instagram: Configuración → Apps y sitios web. Para abrirlo a cualquiera sin añadirlo a mano, Meta exige pasar la **revisión de la app** (permisos `instagram_business_basic` e `instagram_business_content_publish`).
5. Las cuentas tienen que ser **profesionales** (de empresa o de creador).

Los tokens de Instagram se guardan en el servidor y se renuevan solos cada día (cron del Worker), antes de que caduquen a los 60 días.

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
Para cada reel o historia, la agencia decide si lleva **voz y música** (vídeos que explican algo) o **solo música** (piezas rápidas o emocionales). También elige la voz, el ritmo y el ambiente musical. Cada pantalla dura lo que tarda la voz en decir su frase.

## 🔒 Seguridad
- Tus claves de Groq y Pollinations y los tokens de Instagram **solo existen en el servidor** (secretos y KV de Cloudflare). La web y el repositorio no los contienen.
- Cada persona tiene un límite diario de uso de IA (`LLM_PER_DAY`, por defecto 60) y de imágenes (`IMG_PER_DAY`, por defecto 300), configurable en `wrangler.toml`, para que nadie gaste todo tu cupo gratuito.
- GitHub Actions accede al servidor con `ADMIN_TOKEN`. Si se filtra, cámbialo en ambos sitios.
- Al guardar tokens de Instagram de otras personas, eres responsable de custodiarlos. Cada persona puede desconectar su cuenta desde la app cuando quiera.

## ⚠️ Límites conocidos
- **Historias:** la API de Instagram no permite stickers. En automático, la historia termina invitando a responder por mensaje directo.
- **Horarios:** GitHub puede retrasar unos minutos las tareas programadas, así que la publicación puede salir entre 5 y 30 minutos después de la hora prevista.
- **Servicios gratuitos:** Groq, Pollinations y edge-tts son gratuitos con límites y pueden cambiar sus condiciones. Con muchas personas, el cupo de Groq se comparte entre todas.
- **El repositorio es público** (lo exige Pages gratis): los vídeos y las colas de publicación son visibles, aunque de todos modos se van a publicar en Instagram.

## 🧪 Pruebas
```bash
cd worker && node test/worker.test.mjs   # servidor: cuentas, límites, Instagram, admin
cd .. && pip install -r requirements.txt && python -m playwright install chromium
python tests/run_mock.py                 # recorrido completo simulado: plan → vídeo con voz → publicación
```
