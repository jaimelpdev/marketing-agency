// Prueba del servidor sin Cloudflare: KV en memoria y APIs externas simuladas.
import assert from "node:assert/strict";
import worker from "../src/index.js";

const store = new Map();
const KV = {
  async get(k, t) { const v = store.has(k) ? store.get(k) : null; return v && t === "json" ? JSON.parse(v) : v; },
  async put(k, v) { store.set(k, v); },
  async delete(k) { store.delete(k); },
  async list({ prefix }) { return { keys: [...store.keys()].filter(k => k.startsWith(prefix)).map(name => ({ name })), list_complete: true }; },
};
const env = { KV, GROQ_API_KEY: "gsk_secreta", POLLINATIONS_TOKEN: "pt", ADMIN_TOKEN: "admin123", INVITE_CODES: "hola-2026",
  IG_APP_SECRET: "sec", APP_URL: "https://x.github.io/app/", APP_ORIGINS: "https://x.github.io", LLM_PER_DAY: "2", ZERNIO_API_KEY: "zk" };

const calls = [];
const zcalls = [];
const gh = [];
globalThis.fetch = async (url, init = {}) => {
  url = String(url); calls.push(url);
  const J = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } });
  if (url.includes("api.github.com")) { assert.equal(init.headers.authorization, "Bearer ghtok"); gh.push(url); return new Response(null, { status: 204 }); }
  if (url.includes("api.groq.com")) {
    const b = JSON.parse(init.body);
    assert.equal(init.headers.authorization, "Bearer gsk_secreta");
    if (b.model === "openai/gpt-oss-120b") return J({ error: { message: "The model does not exist" } }, 404);
    return J({ choices: [{ message: { content: '{"ok":true,"model":"' + b.model + '"}' } }] });
  }
  if (url.includes("image.pollinations.ai")) { assert.ok(url.includes("token=pt")); return new Response("JPEG", { headers: { "content-type": "image/jpeg" } }); }
  if (url.includes("graph.facebook.com/v23.0/oauth/access_token")) { assert.ok(url.includes("client_secret=sec") && url.includes("client_id=1803279604212539")); return J({ access_token: "LONG" }); }
  if (url.includes("/debug_token?")) return J({ data: { granular_scopes: [{ scope: "instagram_basic", target_ids: ["1784"] }, { scope: "instagram_content_publish", target_ids: ["1784"] }] } });
  if (url.includes("graph.facebook.com/v23.0/1784?")) return J({ username: "academia_demo", id: "1784" });
  if (url.includes("/media_publish")) return J({ id: "PUB1" });
  if (url.includes("graph.facebook.com/v23.0/1784/media")) { assert.ok(String(init.body).includes("access_token=LONG")); return J({ id: "CONT1" }); }
  if (url.includes("/CONT1?")) return J({ status_code: "FINISHED" });
  if (url.includes("refresh_access_token")) return J({ access_token: "LONG2", expires_in: 5184000 });
  if (url.startsWith("https://zernio.com/api/v1")) {
    assert.equal(init.headers.authorization, "Bearer zk");
    const path = url.slice("https://zernio.com/api/v1".length);
    zcalls.push((init.method || "GET") + " " + path);
    if (path.startsWith("/profiles?externalId")) return J({ profiles: [] });
    if (path === "/profiles") return J({ profile: { _id: "prof1" } });
    if (path.startsWith("/connect/tiktok")) { assert.ok(path.includes("profileId=prof1") && path.includes("redirect_url=")); return J({ authUrl: "https://zernio.com/oauth/tiktok?x=1" }); }
    if (path.startsWith("/accounts?profileId=prof1")) return J({ accounts: [{ _id: "acc_tt", platform: "tiktok", username: "ana_tt", isActive: true }] });
    if (path.startsWith("/accounts/acc_tt") && init.method === "DELETE") return J({ ok: true });
    if (path === "/posts") return J({ post: { _id: "post1" } });
    if (path.startsWith("/posts/") && init.method === "DELETE") return J({ ok: true });
    if (path === "/accounts/acc_tt/posts") return J({ status: "ok", posts: [] });
  }
  throw new Error("fetch inesperado " + url);
};

const B = "https://agencia.workers.dev";
const call = async (method, path, { body, key, origin = "https://x.github.io" } = {}) => {
  const h = { Origin: origin }; if (key) h.authorization = "Bearer " + key; if (body) h["content-type"] = "application/json";
  const r = await worker.fetch(new Request(B + path, { method, headers: h, body: body ? JSON.stringify(body) : undefined }), env);
  const t = await r.text(); let j; try { j = JSON.parse(t); } catch { j = t; }
  return { s: r.status, j, h: r.headers };
};

let r = await call("POST", "/api/join", { body: { code: "malo", name: "X" } });
assert.equal(r.s, 403);
r = await call("POST", "/api/join", { body: { code: "hola-2026", name: "Ana" } });
assert.equal(r.s, 200); const key = r.j.key; assert.ok(key.startsWith("ak_"));
assert.equal(r.h.get("access-control-allow-origin"), "https://x.github.io");
r = await call("POST", "/api/join", { body: { code: "hola-2026", name: "Otra" } });
assert.equal(r.s, 403, "el código solo vale una vez");
r = await call("GET", "/api/me"); assert.equal(r.s, 401);
r = await call("GET", "/api/me", { key }); assert.equal(r.j.name, "Ana"); assert.equal(r.j.ig.connected, false);
r = await call("PUT", "/api/config", { key, body: { config: { marca: { name: "Academia Ana" }, plan: { modo: "smart" } } } }); assert.equal(r.s, 200);
r = await call("POST", "/api/llm", { key, body: { prompt: "hola" } });
assert.equal(r.s, 200); assert.equal(r.j.model, "openai/gpt-oss-20b", "cambia de modelo si el primero no existe");
assert.ok(!JSON.stringify(r.j).includes("gsk_secreta"), "la clave nunca sale");
await call("POST", "/api/llm", { key, body: { prompt: "hola" } });
r = await call("POST", "/api/llm", { key, body: { prompt: "hola" } }); assert.equal(r.s, 429, "límite diario");
r = await call("GET", "/api/image?q=coche&seed=5", { key }); assert.equal(r.s, 200); assert.equal(r.j, "JPEG");
r = await call("POST", "/api/ig/start", { key }); assert.ok(r.j.url.includes("client_id=1803279604212539") && r.j.url.includes("config_id=1282938940682283") && r.j.url.startsWith("https://www.facebook.com/"));
const state = new URL(r.j.url).searchParams.get("state");
const cb = await worker.fetch(new Request(B + "/auth/instagram/callback?code=abc&state=" + state + "#_"), env);
assert.equal(cb.status, 302); assert.ok(cb.headers.get("location").endsWith("#ig-ok"));
r = await call("GET", "/api/me", { key }); assert.equal(r.j.ig.username, "academia_demo"); assert.ok(!JSON.stringify(r.j).includes("LONG"), "token IG no se expone");
r = await call("GET", "/admin/users", { key }); assert.equal(r.s, 401, "una clave de usuario no es admin");
{ // imágenes de calidad (Workers AI)
  const h = { authorization: "Bearer admin123", "content-type": "application/json" };
  let rr = await worker.fetch(new Request("https://w/admin/image", { method: "POST", headers: h, body: JSON.stringify({ prompt: "a" }) }), env, {});
  assert.equal(rr.status, 501, "sin enlace AI avisa");
  env.AI = { run: async (m, i) => { assert.ok(/flux/.test(m) && i.prompt === "un simulador"); return { image: btoa("\xff\xd8jpg") }; } };
  rr = await worker.fetch(new Request("https://w/admin/image", { method: "POST", headers: h, body: JSON.stringify({ prompt: "un simulador", seed: 7 }) }), env, {});
  assert.equal(rr.status, 200); assert.equal(rr.headers.get("content-type"), "image/jpeg");
  rr = await worker.fetch(new Request("https://w/admin/image", { method: "POST", headers: { ...h, authorization: "Bearer x" }, body: "{}" }), env, {});
  assert.equal(rr.status, 401);
  delete env.AI;
}
r = await call("GET", "/admin/users", { key: "admin123" }); assert.equal(r.j.users[0].config.marca.name, "Academia Ana"); assert.ok(!JSON.stringify(r.j).includes("LONG"));
const uid = r.j.users[0].id;
r = await call("POST", "/admin/ig", { key: "admin123", body: { userId: uid, action: "container", params: { image_url: "https://x/1.jpg" } } }); assert.equal(r.j.id, "CONT1");
r = await call("POST", "/admin/ig", { key: "admin123", body: { userId: uid, action: "status", id: "CONT1" } }); assert.equal(r.j.status_code, "FINISHED");
r = await call("POST", "/admin/ig", { key: "admin123", body: { userId: uid, action: "publish", id: "CONT1" } }); assert.equal(r.j.id, "PUB1");
r = await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { estrategia: "Semana tranquila", queue: [{ tipo: "reel", status: "pending" }] } }); assert.equal(r.s, 200);
r = await call("GET", "/api/me", { key }); assert.equal(r.j.status.queue.length, 1);
r = await call("POST", "/admin/invite", { key: "admin123" }); const inv = r.j.code;
r = await call("POST", "/api/join", { body: { code: inv, name: "Luis" } }); assert.equal(r.s, 200);
r = await call("POST", "/api/join", { body: { code: inv, name: "Luis2" } }); assert.equal(r.s, 403);
r = await call("POST", "/admin/refresh-tokens", { key: "admin123" }); assert.equal(r.j.results.length, 0, "los tokens de empresa no se renuevan");
r = await call("OPTIONS", "/api/llm"); assert.equal(r.s, 204);
// Zernio
r = await call("POST", "/api/social/connect", { key, body: { platform: "tiktok" } }); assert.equal(r.s, 200); assert.ok(r.j.url.includes("zernio.com/oauth/tiktok"));
r = await call("POST", "/api/social/connect", { key, body: { platform: "myspace" } }); assert.equal(r.s, 400);
r = await call("GET", "/api/social", { key }); assert.equal(r.j.accounts[0].platform, "tiktok");
r = await call("GET", "/admin/users", { key: "admin123" }); assert.equal(r.j.users.find(x => x.zernioProfileId).social[0].id, "acc_tt");
r = await call("POST", "/admin/zernio", { key: "admin123", body: { method: "POST", path: "/posts", body: { content: "x" } } }); assert.equal(r.j.post._id, "post1");
r = await call("POST", "/admin/zernio", { key: "admin123", body: { method: "POST", path: "/profiles/../../billing", body: {} } }); assert.equal(r.s, 400, "solo rutas permitidas");
r = await call("POST", "/admin/zernio", { key: "admin123", body: { method: "GET", path: "/accounts/acc_tt/posts" } }); assert.equal(r.s, 200, "puede leer lo publicado");
r = await call("POST", "/admin/zernio", { key: "admin123", body: { method: "GET", path: "/accounts/acc_tt/billing" } }); assert.equal(r.s, 400);
r = await call("POST", "/api/social/disconnect", { key, body: { accountId: "otra" } }); assert.equal(r.s, 404, "no se puede borrar una cuenta ajena");
r = await call("POST", "/api/social/disconnect", { key, body: { accountId: "acc_tt" } }); assert.equal(r.s, 200);
// Rehacer y reiniciar
const fut = new Date(Date.now() + 864e5).toISOString(), past = new Date(Date.now() - 864e5).toISOString();
r = await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { estrategia: "E", queue: [{ id: "p1", tipo: "reel", status: "scheduled", publishAt: fut }, { id: "p0", tipo: "post", status: "published", publishAt: past }] } });
r = await call("GET", "/pending"); assert.equal(r.j.pending, false);
r = await call("POST", "/api/redo", { key, body: { id: "p0" } }); assert.equal(r.s, 400, "lo publicado no se rehace");
r = await call("POST", "/api/redo", { key, body: { id: "nada" } }); assert.equal(r.s, 404);
r = await call("POST", "/api/redo", { key, body: { id: "p1" } }); assert.equal(r.s, 200); assert.deepEqual(r.j.requests.redo, ["p1"]); assert.equal(r.j.started, false, "sin GITHUB_TOKEN no lanza nada");
env.GITHUB_TOKEN = "ghtok";
r = await call("POST", "/api/replan", { key }); assert.equal(r.j.started, true, "lanza la agencia al momento");
assert.ok(gh.length === 1 && gh[0].includes("/repos/jaimelpdev/marketing-agency/actions/workflows/planificar.yml/dispatches"));
r = await call("POST", "/api/replan", { key }); assert.equal(gh.length, 1, "no lanza dos veces seguidas");
delete env.GITHUB_TOKEN; await call("POST", "/api/cancel-request", { key, body: {} }); await call("POST", "/api/redo", { key, body: { id: "p1" } });
r = await call("GET", "/pending"); assert.equal(r.j.pending, true);
r = await call("GET", "/admin/users", { key: "admin123" }); assert.deepEqual(r.j.users.find(x => x.id === uid).requests.redo, ["p1"]);
r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { redo: ["p1"] } }); assert.equal(r.j.requests, null);
{ // modo Veo: subir clips, montar cuando están todos, descargar como admin
  await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { estrategia: "E", queue: [{ id: "v1", tipo: "reel", status: "scheduled", publishAt: fut, veo: { clips: [{ t: "a", prompt: "p", dur: 4 }, { t: "b", prompt: "q", dur: 6 }] } }] } });
  const up = (k, bytes) => worker.fetch(new Request(B + "/api/clips/v1/" + k, { method: "POST", headers: { authorization: "Bearer " + key, "content-type": "video/mp4", Origin: "https://x.github.io" }, body: new Uint8Array(bytes) }), env).then(async x => ({ s: x.status, j: await x.json() }));
  let u1 = await up(0, 50); assert.equal(u1.s, 400, "clip vacío");
  u1 = await up(5, 20000); assert.equal(u1.s, 404, "clip que no existe");
  u1 = await up(0, 20000); assert.equal(u1.s, 200); assert.deepEqual(u1.j.veo.v1, [0]); assert.ok(!(u1.j.requests && u1.j.requests.veo));
  r = await call("GET", "/pending"); assert.equal(r.j.pending, false);
  u1 = await up(1, 30000); assert.deepEqual(u1.j.veo.v1, [0, 1]); assert.deepEqual(u1.j.requests.veo, ["v1"]);
  r = await call("GET", "/pending"); assert.equal(r.j.pending, true);
  const dl = await worker.fetch(new Request(B + "/admin/clips/" + uid + "/v1/1", { headers: { authorization: "Bearer admin123" } }), env);
  assert.equal(dl.status, 200); assert.equal((await dl.arrayBuffer()).byteLength, 30000);
  r = await call("GET", "/api/me", { key }); assert.deepEqual(r.j.user ? r.j.user.veo.v1 : r.j.veo.v1, [0, 1]);
  r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { veo: ["v1"] } }); assert.equal(r.j.requests, null);
}
{ // bolsa de vídeos (Veo obligatorio): los clips se acumulan para guiones sin fecha
  await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { queue: [{ id: "h1", tipo: "reel", status: "hueco", publishAt: fut }], bolsa: [{ id: "b-x-01-reel", tipo: "reel", status: "pendiente", veo: { clips: [{ t: "a", prompt: "p", dur: 8 }, { t: "b", prompt: "q", dur: 8 }] } }] } });
  const up = (k) => worker.fetch(new Request(B + "/api/clips/b-x-01-reel/" + k, { method: "POST", headers: { authorization: "Bearer " + key, "content-type": "video/mp4", Origin: "https://x.github.io" }, body: new Uint8Array(20000) }), env).then(async x => ({ s: x.status, j: await x.json() }));
  let u1 = await up(0); assert.equal(u1.s, 200); assert.deepEqual(u1.j.veo["b-x-01-reel"], [0]);
  r = await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { queue: [] } }); // sin «bolsa»: se conserva
  r = await call("GET", "/api/me", { key }); const st = (r.j.user || r.j).status; assert.equal(st.bolsa.length, 1);
  u1 = await up(1); assert.equal(u1.s, 200); assert.deepEqual(u1.j.requests.veo, ["b-x-01-reel"]);
  r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { veo: ["b-x-01-reel"] } });
  await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { queue: [{ id: "v1", tipo: "reel", status: "scheduled", publishAt: fut, veo: { clips: [{ t: "a", prompt: "p", dur: 4 }, { t: "b", prompt: "q", dur: 6 }] } }], bolsa: [] } });
}
{ // vale de subida desde Google Flow
  r = await call("POST", "/api/clips/v1/ticket", { key }); assert.equal(r.s, 200); assert.ok(r.j.ticket);
  const tk = r.j.ticket;
  const up = (t, k, n, origin) => worker.fetch(new Request(B + "/clip-ticket/" + t + "/" + k, { method: "POST", headers: { "content-type": "video/mp4", Origin: origin || "https://labs.google" }, body: new Uint8Array(n) }), env);
  let x = await up("malo", 0, 20000); assert.equal(x.status, 401, "vale falso");
  x = await up(tk, 0, 20000); assert.equal(x.status, 200); assert.equal(x.headers.get("access-control-allow-origin"), "https://labs.google");
  x = await up(tk, 1, 20000); const jj = await x.json(); assert.deepEqual(jj.requests.veo, ["v1"]);
  r = await call("POST", "/api/clips/nada/ticket", { key }); assert.equal(r.s, 404);
  r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { veo: ["v1"] } });
}
r = await call("POST", "/api/replan", { key }); const rp = r.j.requests.replan; assert.ok(rp);
r = await call("POST", "/api/cancel-request", { key, body: {} }); assert.equal(r.j.requests.replan, undefined);
r = await call("POST", "/api/create", { key, body: {} }); assert.equal(r.s, 400);
r = await call("POST", "/api/create", { key, body: { tipo: "reel", tema: "Trail braking", cuando: new Date(Date.now() + 3 * 3600e3).toISOString() } }); assert.equal(r.s, 200); const cid = r.j.item.id; assert.ok(r.j.item.cuando);
r = await call("POST", "/api/create", { key, body: { tipo: "rara", tema: "x", cuando: "ayer" } }); assert.equal(r.j.item.tipo, "auto"); assert.equal(r.j.item.cuando, null);
r = await call("GET", "/admin/users", { key: "admin123" }); assert.equal(r.j.users.find(x => x.id === uid).requests.create.length, 2);
r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { create: [cid] } }); assert.equal(r.j.requests.create.length, 1);
await call("POST", "/api/cancel-request", { key, body: { id: r.j.requests.create[0].id } });
r = await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { estrategia: "E", queue: [{ id: "d1", tipo: "reel", status: "scheduled", publishAt: fut, zernioPostId: "zp1" }, { id: "d0", tipo: "post", status: "published", publishAt: past }] } });
zcalls.length = 0;
r = await call("POST", "/api/delete", { key, body: { id: "d0" } }); assert.equal(r.s, 400, "lo publicado no se borra desde aquí");
r = await call("POST", "/api/delete", { key, body: { id: "d1" } }); assert.equal(r.s, 200); assert.deepEqual(r.j.requests.remove, ["d1"]);
assert.ok(zcalls.some(c => c === "DELETE /posts/zp1"), "anula en Zernio al momento: " + zcalls.join(","));
r = await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { queue: [{ id: "d1", tipo: "reel", status: "scheduled", publishAt: fut }] } });
r = await call("GET", "/api/me", { key }); assert.ok(!r.j.status.queue.some(x => x.id === "d1"), "no vuelve a aparecer");
r = await call("GET", "/pending"); assert.equal(r.j.pending, true);
r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { remove: ["d1"] } });
r = await call("POST", "/api/photos", { key, body: { desc: "Camiseta roja", data: "data:text/html;base64,AAAA" } }); assert.equal(r.s, 400);
r = await call("POST", "/api/photos", { key, body: { desc: "Camiseta roja", data: "data:image/jpeg;base64,AAAA" } }); assert.equal(r.s, 200); const fid = r.j.photos[0].id;
r = await call("GET", "/admin/photos/" + uid, { key: "admin123" }); assert.equal(r.j.photos[0].src, "data:image/jpeg;base64,AAAA"); assert.equal(r.j.photos[0].desc, "Camiseta roja");
r = await call("GET", "/admin/photos/" + uid, { key }); assert.equal(r.s, 401);
r = await call("GET", "/api/me", { key }); assert.equal(r.j.photos.length, 1);
r = await call("DELETE", "/api/photos/" + fid, { key }); assert.equal(r.j.photos.length, 0);
r = await call("PUT", "/admin/status/" + uid, { key: "admin123", body: { queue: [{ id: "w1", tipo: "reel", status: "scheduled", publishAt: fut, zernioPostId: "zw1" }, { id: "w2", tipo: "post", status: "scheduled", publishAt: fut, zernioPostId: "zw2" }, { id: "w0", tipo: "post", status: "published", publishAt: past, zernioPostId: "zw0" }] } });
zcalls.length = 0;
r = await call("POST", "/api/redo", { key, body: { id: "w1" } }); assert.equal(r.s, 200);
assert.ok(zcalls.includes("DELETE /posts/zw1"), "rehacer anula ya la versión anterior: " + zcalls.join(","));
r = await call("POST", "/api/replan", { key }); assert.equal(r.s, 200);
assert.ok(zcalls.includes("DELETE /posts/zw2") && !zcalls.includes("DELETE /posts/zw0"), "rehacer semana anula lo pendiente y no toca lo publicado: " + zcalls.join(","));
r = await call("GET", "/api/me", { key }); assert.ok(!r.j.status.queue.some(x => x.zernioPostId === "zw2"));
await call("POST", "/api/cancel-request", { key, body: {} });
r = await call("POST", "/api/reset", { key }); assert.equal(r.s, 200);
r = await call("GET", "/api/me", { key }); assert.equal(r.j.config, null); assert.equal(r.j.status, null); assert.ok(r.j.requests.reset, "la clave sigue valiendo");
r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { reset: "otra-fecha" } }); assert.ok(r.j.requests.reset, "no borra una petición distinta");
r = await call("POST", "/admin/requests/" + uid + "/done", { key: "admin123", body: { reset: (await call("GET", "/api/me", { key })).j.requests.reset } }); assert.equal(r.j.requests, null);
r = await call("POST", "/admin/requests/" + uid + "/done", { key }); assert.equal(r.s, 401);
console.log("✓ Servidor: todas las pruebas pasan");
