#!/usr/bin/env node
/**
 * Instalador del servidor de la agencia en Cloudflare (un solo comando):
 *     cd worker && npm install && node setup.mjs
 *
 * Hace por ti: inicio de sesión en Cloudflare, crear el almacén KV, ponerlo en wrangler.toml,
 * generar la contraseña de administración, pedirte tus claves (Groq, Pollinations, códigos de
 * invitación, app de Instagram), desplegar y escribir la dirección en ../docs/config.json.
 */
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { createInterface } from "node:readline/promises";

const isWin = process.platform === "win32";
const rl = createInterface({ input: process.stdin, output: process.stdout });

function wrangler(args, { input, capture = true } = {}) {
  const r = spawnSync(isWin ? "npx.cmd" : "npx", ["wrangler", ...args], {
    input, encoding: "utf8", shell: isWin, stdio: capture ? ["pipe", "pipe", "pipe"] : "inherit",
  });
  if (r.status !== 0) {
    if (capture) console.error(r.stdout || "", r.stderr || "");
    throw new Error("Falló: wrangler " + args.join(" "));
  }
  return (r.stdout || "") + (r.stderr || "");
}
async function ask(q, { optional = false } = {}) {
  while (true) {
    const a = (await rl.question(q)).trim();
    if (a || optional) return a;
    console.log("   (obligatorio)");
  }
}
const putSecret = (name, value) => { wrangler(["secret", "put", name], { input: value + "\n" }); console.log(`   ✓ ${name} guardado`); };

console.log("\n🧠 Instalador del servidor de la Agencia de marketing IA\n");

// 1. Sesión
try { wrangler(["whoami"]); console.log("1) Sesión de Cloudflare: ya iniciada ✓"); }
catch { console.log("1) Abriendo el navegador para iniciar sesión en Cloudflare…"); wrangler(["login"], { capture: false }); }

// 2. KV
let toml = readFileSync("wrangler.toml", "utf8");
if (toml.includes("PEGA_AQUI_EL_ID_DEL_KV")) {
  console.log("2) Creando el almacén de datos (KV)…");
  const out = wrangler(["kv", "namespace", "create", "KV"]);
  const m = out.match(/id\s*[=:]\s*"?([0-9a-f]{32})"?/i);
  if (!m) { console.log(out); throw new Error("No encontré el id del KV en la respuesta."); }
  toml = toml.replace("PEGA_AQUI_EL_ID_DEL_KV", m[1]);
  writeFileSync("wrangler.toml", toml);
  console.log("   ✓ KV creado:", m[1]);
} else console.log("2) Almacén KV: ya configurado ✓");

// 3. Primer despliegue (para poder guardar secretos y saber la dirección)
console.log("3) Desplegando el servidor…");
let out = wrangler(["deploy"]);
const url = (out.match(/https:\/\/[\w.-]+\.workers\.dev/) || [])[0];
if (!url) { console.log(out); throw new Error("No encontré la dirección del servidor."); }
console.log("   ✓ Servidor en", url);

// 4. Secretos
console.log("\n4) Tus claves (se guardan cifradas en Cloudflare; no salen de allí):");
putSecret("GROQ_API_KEY", await ask("   Clave de Groq (gsk_…): "));
const poll = await ask("   Token de Pollinations (opcional, Enter para saltar): ", { optional: true });
if (poll) putSecret("POLLINATIONS_TOKEN", poll);
const invites = await ask("   Códigos de invitación separados por comas (p. ej. jaime-2026,laura-7f3k): ");
putSecret("INVITE_CODES", invites);
const admin = "adm_" + randomBytes(24).toString("hex");
putSecret("ADMIN_TOKEN", admin);
console.log(`\n   Para la app de Instagram (Meta for Developers), usa esta URL de redirección:\n   ${url}/auth/instagram/callback`);
const igId = await ask("   ID de la app de Instagram (Enter para hacerlo más tarde): ", { optional: true });
if (igId) {
  putSecret("IG_APP_ID", igId);
  putSecret("IG_APP_SECRET", await ask("   Secreto de la app de Instagram: "));
}

// 5. Dirección en la web
writeFileSync("../docs/config.json", JSON.stringify({ api: url }, null, 2) + "\n");
console.log("\n5) ✓ Dirección guardada en docs/config.json");

rl.close();
console.log(`
✅ ¡Servidor listo!

Últimos pasos en GitHub (Settings → Secrets and variables → Actions):
  • Variable  AGENCIA_API          = ${url}
  • Secreto   AGENCIA_ADMIN_TOKEN  = ${admin}
  • Secreto   GROQ_API_KEY         = tu clave de Groq
  (guarda AGENCIA_ADMIN_TOKEN ahora: no se vuelve a mostrar)

Y sube el cambio de docs/config.json y wrangler.toml:
  git add ../docs/config.json wrangler.toml && git commit -m "Servidor desplegado" && git push
`);
