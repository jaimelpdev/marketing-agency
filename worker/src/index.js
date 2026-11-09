/**
 * Agencia de marketing IA · servidor (Cloudflare Worker)
 *
 * - Guarda TUS claves (Groq, Pollinations) como secretos: nunca llegan al navegador.
 * - Cuentas por invitación: cada persona entra con un código y recibe una clave de acceso.
 * - Guarda la configuración de marca de cada persona (KV).
 * - Conecta Instagram con OAuth ("Instagram API con inicio de sesión de Instagram").
 *   Los tokens de Instagram se quedan aquí; GitHub Actions publica pidiéndoselo al servidor.
 * - Renueva los tokens de Instagram con un cron diario.
 */

const IG_GRAPH = "https://graph.instagram.com";
const FB_GRAPH = "https://graph.facebook.com";
const IG_VER = "v23.0";
// App de Meta (inicio de sesión unificado / Facebook Login para empresas). No son secretos.
const DEFAULT_META_APP_ID = "1803279604212539";
const DEFAULT_META_CONFIG_ID = "1809862283611437";
const appId = env => String(env.IG_APP_ID || DEFAULT_META_APP_ID).trim();
const configId = env => String(env.META_CONFIG_ID || DEFAULT_META_CONFIG_ID).trim();
const FALLBACK_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"];

// ---------- utilidades ----------
function corsHeaders(req, env) {
  const origin = req.headers.get("Origin") || "";
  const allowed = String(env.APP_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  const ok = !allowed.length || allowed.includes(origin) || origin === "null";
  return {
    "access-control-allow-origin": ok ? (origin || "*") : allowed[0],
    "access-control-allow-headers": "authorization,content-type",
    "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
    "access-control-max-age": "86400",
    "vary": "Origin",
  };
}
const json = (data, status = 200, extra = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", ...extra } });
const err = (status, code, message) => json({ error: { code, message } }, status);

function randomId(bytes = 24) {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return Array.from(a, b => b.toString(16).padStart(2, "0")).join("");
}
function bearer(req) {
  const h = req.headers.get("authorization") || "";
  return h.startsWith("Bearer ") ? h.slice(7).trim() : "";
}
function today() { return new Date().toISOString().slice(0, 10); }
function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function getUser(env, id) { return id ? await env.KV.get("user:" + id, "json") : null; }
async function putUser(env, u) { await env.KV.put("user:" + u.id, JSON.stringify(u)); }
async function listUserIds(env) {
  const ids = [];
  let cursor;
  do {
    const r = await env.KV.list({ prefix: "user:", cursor });
    r.keys.forEach(k => ids.push(k.name.slice(5)));
    cursor = r.list_complete ? undefined : r.cursor;
  } while (cursor);
  return ids;
}
async function sessionUser(req, env) {
  const tok = bearer(req);
  if (!tok || !tok.startsWith("ak_")) return null;
  const id = await env.KV.get("session:" + tok);
  return id ? await getUser(env, id) : null;
}
function publicUser(u) {
  return {
    id: u.id, name: u.name, created: u.created, config: u.config || null,
    ig: u.ig ? { connected: true, username: u.ig.username, expires: u.ig.expires } : { connected: false },
    status: u.status || null,
    usage: u.usage && u.usage.day === today() ? u.usage : { day: today(), llm: 0, img: 0 },
  };
}
async function spend(env, u, kind) {
  const limit = Number(kind === "llm" ? env.LLM_PER_DAY || 60 : env.IMG_PER_DAY || 300);
  if (!u.usage || u.usage.day !== today()) u.usage = { day: today(), llm: 0, img: 0 };
  if ((u.usage[kind] || 0) >= limit) return false;
  u.usage[kind] = (u.usage[kind] || 0) + 1;
  await putUser(env, u);
  return true;
}

// ---------- Groq ----------
async function groq(env, prompt, model, jsonMode = true) {
  const r = await fetch("https://api.groq.com/openai/v1/chat/completions", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer " + env.GROQ_API_KEY },
    body: JSON.stringify({
      model, temperature: 0.9, max_completion_tokens: 8192,
      ...(jsonMode ? { response_format: { type: "json_object" } } : {}),
      messages: [
        { role: "system", content: "Eres un experto en marketing de Instagram en español. Respondes siempre SOLO con JSON válido." },
        { role: "user", content: prompt },
      ],
    }),
  });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, j };
}
async function askLLM(env, prompt) {
  const models = [env.GROQ_MODEL, ...FALLBACK_MODELS].filter((m, i, a) => m && a.indexOf(m) === i);
  let last = "sin respuesta";
  for (const m of models) {
    let res = await groq(env, prompt, m, true);
    const msg = (res.j.error && res.j.error.message) || "";
    if (!res.ok && res.status === 400 && /json|response_format/i.test(msg)) res = await groq(env, prompt, m, false);
    if (res.ok) return { text: res.j.choices?.[0]?.message?.content || "", model: m };
    if (res.status === 429) return { rate_limited: true, message: msg };
    last = msg || "HTTP " + res.status;
    if (!(res.status === 404 || /model/i.test(msg))) break; // solo probamos otro modelo si el problema es el modelo
  }
  throw new Error(last);
}

// ---------- Instagram ----------
async function igCall(method, path, token, params = {}, host = IG_GRAPH) {
  const url = new URL(`${host}/${IG_VER}/${path}`);
  let init = { method };
  if (method === "GET") {
    Object.entries({ ...params, access_token: token }).forEach(([k, v]) => url.searchParams.set(k, v));
  } else {
    init.body = new URLSearchParams({ ...params, access_token: token });
    init.headers = { "content-type": "application/x-www-form-urlencoded" };
  }
  const r = await fetch(url, init);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error((j.error && j.error.message) || "Instagram HTTP " + r.status);
  return j;
}

async function refreshIgTokens(env, force = false) {
  const out = [];
  for (const id of await listUserIds(env)) {
    const u = await getUser(env, id);
    if (!u || !u.ig || !u.ig.token || u.ig.host === FB_GRAPH || !u.ig.expires) continue; // los tokens de Facebook para empresas no caducan
    const left = (u.ig.expires || 0) - Date.now();
    if (!force && left > 20 * 86400e3) continue;
    try {
      const r = await fetch(`${IG_GRAPH}/refresh_access_token?grant_type=ig_refresh_token&access_token=${encodeURIComponent(u.ig.token)}`);
      const j = await r.json();
      if (!j.access_token) throw new Error((j.error && j.error.message) || "sin token");
      u.ig.token = j.access_token;
      u.ig.expires = Date.now() + (j.expires_in || 5184000) * 1000;
      u.ig.error = null;
      out.push({ id, ok: true });
    } catch (e) {
      u.ig.error = String(e.message || e);
      out.push({ id, ok: false, error: u.ig.error });
    }
    await putUser(env, u);
  }
  return out;
}

// ---------- rutas ----------
async function handle(req, env) {
  const url = new URL(req.url);
  const p = url.pathname.replace(/\/+$/, "") || "/";
  const method = req.method;

  if (p === "/" || p === "/health") return json({ ok: true, service: "agencia-marketing-ia" });

  // ===== cuentas =====
  if (p === "/api/join" && method === "POST") {
    const b = await req.json().catch(() => ({}));
    const code = String(b.code || "").trim();
    const name = String(b.name || "").trim().slice(0, 60) || "Sin nombre";
    if (!code) return err(400, "bad_request", "Falta el código de invitación.");
    const staticCodes = String(env.INVITE_CODES || "").split(",").map(s => s.trim()).filter(Boolean);
    const dyn = await env.KV.get("invite:" + code);
    const usedStatic = await env.KV.get("invite_used:" + code);
    const valid = dyn === "1" || (staticCodes.includes(code) && !usedStatic);
    if (!valid) return err(403, "bad_invite", "Código de invitación no válido o ya usado.");
    const id = "u_" + randomId(6);
    const key = "ak_" + randomId(24);
    const user = { id, name, created: new Date().toISOString(), config: null, ig: null, status: null };
    await putUser(env, user);
    await env.KV.put("session:" + key, id);
    if (dyn === "1") await env.KV.delete("invite:" + code); else await env.KV.put("invite_used:" + code, id);
    return json({ key, user: publicUser(user) });
  }

  if (p.startsWith("/api/")) {
    const u = await sessionUser(req, env);
    if (!u) return err(401, "unauthorized", "Sesión no válida. Vuelve a entrar con tu clave de acceso.");

    if (p === "/api/me" && method === "GET") return json(publicUser(u));

    if (p === "/api/config" && method === "PUT") {
      const raw = await req.text();
      if (raw.length > 60000) return err(413, "too_large", "Configuración demasiado grande.");
      let b; try { b = JSON.parse(raw); } catch { return err(400, "bad_request", "JSON no válido."); }
      if (!b || typeof b.config !== "object") return err(400, "bad_request", "Falta config.");
      u.config = b.config;
      u.updated = new Date().toISOString();
      await putUser(env, u);
      return json({ ok: true });
    }

    if (p === "/api/llm" && method === "POST") {
      const b = await req.json().catch(() => ({}));
      const prompt = String(b.prompt || "");
      if (!prompt || prompt.length > 30000) return err(400, "bad_request", "Petición vacía o demasiado larga.");
      if (!(await spend(env, u, "llm"))) return err(429, "rate_limited", "Has llegado al límite diario de la agencia. Vuelve mañana.");
      try {
        const r = await askLLM(env, prompt);
        if (r.rate_limited) return err(429, "rate_limited", "La IA está saturada. Prueba en un minuto.");
        return json({ text: r.text, model: r.model });
      } catch (e) { return err(502, "llm_error", String(e.message || e)); }
    }

    if (p === "/api/image" && method === "GET") {
      const q = String(url.searchParams.get("q") || "").slice(0, 600);
      const seed = String(url.searchParams.get("seed") || "1").replace(/\D/g, "").slice(0, 9) || "1";
      const w = Math.min(1920, Number(url.searchParams.get("w")) || 1080), h = Math.min(1920, Number(url.searchParams.get("h")) || 1920);
      if (!q) return err(400, "bad_request", "Falta la descripción.");
      if (!(await spend(env, u, "img"))) return err(429, "rate_limited", "Límite diario de imágenes alcanzado.");
      const src = `https://image.pollinations.ai/prompt/${encodeURIComponent(q)}?width=${w}&height=${h}&nologo=true&seed=${seed}` +
        (env.POLLINATIONS_TOKEN ? `&token=${encodeURIComponent(env.POLLINATIONS_TOKEN)}` : "");
      const r = await fetch(src, { headers: env.POLLINATIONS_TOKEN ? { authorization: "Bearer " + env.POLLINATIONS_TOKEN } : {} });
      if (!r.ok) return err(r.status === 429 ? 429 : 502, "image_error", "El generador de imágenes no respondió (" + r.status + ").");
      return new Response(r.body, { status: 200, headers: { "content-type": r.headers.get("content-type") || "image/jpeg", "cache-control": "private, max-age=86400" } });
    }

    if (p === "/api/ig/start" && method === "POST") {
      if (!env.IG_APP_SECRET) return err(501, "not_configured", "Instagram aún no está configurado en el servidor (falta IG_APP_SECRET).");
      const state = randomId(16);
      await env.KV.put("oauth:" + state, u.id, { expirationTtl: 900 });
      const auth = new URL(`https://www.facebook.com/${IG_VER}/dialog/oauth`);
      auth.searchParams.set("client_id", appId(env));
      auth.searchParams.set("config_id", configId(env));
      auth.searchParams.set("redirect_uri", `${url.origin}/auth/instagram/callback`);
      auth.searchParams.set("response_type", "code");
      auth.searchParams.set("override_default_response_type", "true");
      auth.searchParams.set("state", state);
      return json({ url: auth.toString() });
    }

    if (p === "/api/ig/disconnect" && method === "POST") {
      u.ig = null;
      await putUser(env, u);
      return json({ ok: true });
    }

    if (p === "/api/logout" && method === "POST") {
      await env.KV.delete("session:" + bearer(req));
      return json({ ok: true });
    }
    return err(404, "not_found", "Ruta no encontrada.");
  }

  // ===== vuelta de Instagram =====
  if (p === "/auth/instagram/callback" && method === "GET") {
    const back = (frag) => Response.redirect(`${env.APP_URL || "/"}#${frag}`, 302);
    const state = url.searchParams.get("state") || "";
    const code = url.searchParams.get("code");
    const uid = state ? await env.KV.get("oauth:" + state) : null;
    if (!uid || !code) return back("ig-error");
    await env.KV.delete("oauth:" + state);
    const u = await getUser(env, uid);
    if (!u) return back("ig-error");
    try {
      const secret = String(env.IG_APP_SECRET || "").trim();
      const tok = await fetch(`${FB_GRAPH}/${IG_VER}/oauth/access_token?` + new URLSearchParams({
        client_id: appId(env), client_secret: secret, redirect_uri: `${url.origin}/auth/instagram/callback`, code,
      }));
      const tj = await tok.json();
      if (!tj.access_token) throw new Error(tj.error?.message || "sin token");
      const token = tj.access_token;
      // Cuentas de Instagram que la persona ha concedido
      let igIds = [];
      try {
        const dbg = await fetch(`${FB_GRAPH}/${IG_VER}/debug_token?` + new URLSearchParams({ input_token: token, access_token: `${appId(env)}|${secret}` }));
        const dj = await dbg.json();
        const scopes = (dj.data && dj.data.granular_scopes) || [];
        const pick = s => (scopes.find(x => x.scope === s) || {}).target_ids || [];
        igIds = [...pick("instagram_content_publish"), ...pick("instagram_basic")];
      } catch (e) { }
      if (!igIds.length) {
        const pages = await igCall("GET", "me/accounts", token, { fields: "instagram_business_account" }, FB_GRAPH).catch(() => ({ data: [] }));
        igIds = (pages.data || []).map(p => p.instagram_business_account && p.instagram_business_account.id).filter(Boolean);
      }
      if (!igIds.length) throw new Error("No se concedió ninguna cuenta de Instagram");
      const igId = String(igIds[0]);
      const info = await igCall("GET", igId, token, { fields: "username" }, FB_GRAPH).catch(() => ({}));
      u.ig = { id: igId, username: info.username || igId, token, host: FB_GRAPH, expires: tj.expires_in ? Date.now() + tj.expires_in * 1000 : null, connected: new Date().toISOString() };
      await putUser(env, u);
      return back("ig-ok");
    } catch (e) {
      console.log("OAuth Instagram:", e.message);
      return back("ig-error");
    }
  }

  // ===== administración (GitHub Actions) =====
  if (p.startsWith("/admin/")) {
    if (!env.ADMIN_TOKEN || !timingSafeEqual(bearer(req), env.ADMIN_TOKEN)) return err(401, "unauthorized", "Admin no autorizado.");

    if (p === "/admin/users" && method === "GET") {
      const out = [];
      for (const id of await listUserIds(env)) {
        const u = await getUser(env, id);
        if (u) out.push({ id: u.id, name: u.name, config: u.config, ig: { connected: !!(u.ig && u.ig.token), username: u.ig && u.ig.username } });
      }
      return json({ users: out });
    }
    if (p === "/admin/invite" && method === "POST") {
      const code = "inv-" + randomId(4);
      await env.KV.put("invite:" + code, "1");
      return json({ code });
    }
    if (p === "/admin/ig" && method === "POST") {
      const b = await req.json().catch(() => ({}));
      const u = await getUser(env, b.userId);
      if (!u || !u.ig || !u.ig.token) return err(400, "no_instagram", "Este usuario no tiene Instagram conectado.");
      try {
        let r;
        const host = u.ig.host || IG_GRAPH;
        if (b.action === "container") r = await igCall("POST", `${u.ig.id}/media`, u.ig.token, b.params || {}, host);
        else if (b.action === "status") r = await igCall("GET", String(b.id), u.ig.token, { fields: "status_code,status" }, host);
        else if (b.action === "publish") r = await igCall("POST", `${u.ig.id}/media_publish`, u.ig.token, { creation_id: String(b.id) }, host);
        else return err(400, "bad_request", "Acción desconocida.");
        return json(r);
      } catch (e) { return err(502, "instagram_error", String(e.message || e)); }
    }
    const m = p.match(/^\/admin\/status\/([\w-]+)$/);
    if (m && method === "PUT") {
      const u = await getUser(env, m[1]);
      if (!u) return err(404, "not_found", "Usuario no encontrado.");
      const b = await req.json().catch(() => ({}));
      u.status = { updated: new Date().toISOString(), estrategia: String(b.estrategia || (u.status && u.status.estrategia) || ""), queue: Array.isArray(b.queue) ? b.queue.slice(0, 60) : [] };
      await putUser(env, u);
      return json({ ok: true });
    }
    if (p === "/admin/refresh-tokens" && method === "POST") return json({ results: await refreshIgTokens(env, true) });
    return err(404, "not_found", "Ruta no encontrada.");
  }
  return err(404, "not_found", "Ruta no encontrada.");
}

export default {
  async fetch(req, env) {
    const cors = corsHeaders(req, env);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    let res;
    try { res = await handle(req, env); }
    catch (e) { res = err(500, "server_error", String(e.message || e)); }
    if (res.status >= 300 && res.status < 400) return res; // redirecciones
    const h = new Headers(res.headers);
    Object.entries(cors).forEach(([k, v]) => h.set(k, v));
    return new Response(res.body, { status: res.status, headers: h });
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(refreshIgTokens(env));
  },
};
