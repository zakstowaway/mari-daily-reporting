/**
 * The auth + write server. A Cloudflare Worker. Free tier: 100k req/day.
 *
 * This is the ONLY place that:
 *   - sees a password
 *   - holds the password hashes  (secret PASSWORDS)
 *   - can write to the repo      (secret GITHUB_TOKEN)
 *
 * The browser never holds a secret. It holds a short-lived signed token that
 * says who you are, and this Worker checks it on every write.
 *
 * WHY A SERVER AT ALL
 * -------------------
 * The old scheme verified sha256(salt + password) IN THE BROWSER, with the salt
 * and every hash shipped in users.json. Anyone could read the hashes, and
 * anyone with devtools could skip the check. That is fine for hiding a
 * read-only dashboard; it is not fine for writing data that decides food cost.
 *
 * ATTRIBUTION IS THE POINT (Zak: "one username per person, so that we can see
 * who's inputting data"). Every write is committed AS THE PERSON. `git log
 * data/recipes/` is the audit trail — no audit table, git already does it.
 *
 * DEPLOY: see ../README.md
 */

const JSON_HEADERS = { "content-type": "application/json" };
const TOKEN_TTL_SECONDS = 12 * 60 * 60;   // one shift

// ── helpers ────────────────────────────────────────────────────────────────
const enc = new TextEncoder();
const b64url = (buf) =>
  btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64 = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

function cors(env) {
  // Only our own app may call this. Not "*" — a token is at stake.
  return {
    "access-control-allow-origin": env.ALLOWED_ORIGIN || "https://app.stowawaybar.com",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "content-type, authorization",
    "access-control-max-age": "86400",
  };
}

const json = (obj, status, env) =>
  new Response(JSON.stringify(obj), { status, headers: { ...JSON_HEADERS, ...cors(env) } });

/** Constant-time compare. Timing leaks are cheap to avoid. */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) out |= a[i] ^ b[i];
  return out === 0;
}

// ── passwords: must match modules/auth/passwords.py exactly ────────────────
async function verifyPassword(password, stored) {
  const [algo, digest, iters, saltB64, hashB64] = String(stored).split("$");
  if (algo !== "pbkdf2" || digest !== "sha256") return false;
  const salt = Uint8Array.from(atob(saltB64), (c) => c.charCodeAt(0));
  const expected = Uint8Array.from(atob(hashB64), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations: parseInt(iters, 10), hash: "SHA-256" },
    key, expected.length * 8
  );
  return timingSafeEqual(new Uint8Array(bits), expected);
}

// ── tokens: JWT HS256 ──────────────────────────────────────────────────────
async function sign(payload, secret) {
  const head = b64url(enc.encode(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(`${head}.${body}`));
  return `${head}.${body}.${b64url(sig)}`;
}

async function verify(token, secret) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return null;
  const [head, body, sig] = parts;
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("HMAC", key, unb64(sig), enc.encode(`${head}.${body}`));
  if (!ok) return null;
  const payload = JSON.parse(new TextDecoder().decode(unb64(body)));
  if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null;   // expired
  return payload;
}

// ── routes ─────────────────────────────────────────────────────────────────

/** POST /login {username, password} -> {token, user} */
async function login(request, env) {
  const { username, password } = await request.json().catch(() => ({}));
  if (!username || !password) return json({ error: "username and password required" }, 400, env);

  const passwords = JSON.parse(env.PASSWORDS || "{}");
  const people = JSON.parse(env.PEOPLE || "{}").people || {};

  const u = String(username).trim().toLowerCase();
  const stored = passwords[u];
  const person = people[u];

  // Same answer for "no such user" and "wrong password" — don't leak who exists.
  // Still run a hash on the miss so the timing doesn't leak it either.
  const DUMMY = "pbkdf2$sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  const ok = await verifyPassword(password, stored || DUMMY);
  if (!stored || !ok || !person || person.active === false) {
    return json({ error: "Invalid credentials" }, 401, env);
  }

  const now = Math.floor(Date.now() / 1000);
  const token = await sign(
    { sub: u, name: person.name, role: person.role, venue: person.venue || null,
      iat: now, exp: now + TOKEN_TTL_SECONDS },
    env.JWT_SECRET
  );
  return json({ token, user: { username: u, name: person.name, role: person.role, venue: person.venue || null } }, 200, env);
}

/** POST /whoami  Authorization: Bearer <token> */
async function whoami(request, env) {
  const claims = await verify((request.headers.get("authorization") || "").replace(/^Bearer /, ""), env.JWT_SECRET);
  if (!claims) return json({ error: "Invalid or expired token" }, 401, env);
  return json({ user: { username: claims.sub, name: claims.name, role: claims.role, venue: claims.venue } }, 200, env);
}

/**
 * POST /recipes  Authorization: Bearer <token>
 * Commits data/recipes/<venue>.yaml AS THE PERSON.
 *
 * The browser is a suggestion. Role is taken from the TOKEN, never the body —
 * otherwise anyone could claim to be an admin by editing the request.
 */
async function saveRecipe(request, env) {
  const claims = await verify((request.headers.get("authorization") || "").replace(/^Bearer /, ""), env.JWT_SECRET);
  if (!claims) return json({ error: "Invalid or expired token" }, 401, env);

  const KITCHEN = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];
  if (!KITCHEN.includes(claims.role)) return json({ error: "Your role cannot edit recipes" }, 403, env);

  const { venue, product, yaml } = await request.json().catch(() => ({}));
  if (!venue || !product || !yaml) return json({ error: "venue, product and yaml required" }, 400, env);
  if (!/^[a-z_]+$/.test(venue)) return json({ error: "bad venue" }, 400, env);

  // A stowfood chef may not write Harry Gatos' recipes.
  if (!["admin", "bigchef"].includes(claims.role) && claims.venue && claims.venue !== venue) {
    return json({ error: `You can only edit ${claims.venue}` }, 403, env);
  }

  const path = `data/recipes/${venue}.yaml`;
  const api = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`;
  const gh = { "authorization": `Bearer ${env.GITHUB_TOKEN}`, "user-agent": "shg-auth-worker",
               "accept": "application/vnd.github+json" };

  const existing = await fetch(api, { headers: gh });
  let sha, current = "";
  if (existing.ok) {
    const j = await existing.json();
    sha = j.sha;
    current = atob(j.content.replace(/\n/g, ""));
  }

  // Append a version, never overwrite. See ARCHITECTURE.md decision 2:
  // recipes are effective-dated so old COGS stays reproducible.
  const stamp = new Date().toISOString().slice(0, 10);
  const block = `\n# ${product} — entered by ${claims.name} (${claims.sub}) on ${stamp}\n${yaml.trim()}\n`;
  const body = {
    message: `Recipe: ${product} (${venue}) — ${claims.name}`,
    content: btoa(unescape(encodeURIComponent(current + block))),
    // THE ATTRIBUTION. git log data/recipes/ now says who, not 'stowfood'.
    author: { name: claims.name, email: `${claims.sub}@stowawaybar.com` },
    ...(sha ? { sha } : {}),
  };
  const put = await fetch(api, { method: "PUT", headers: { ...gh, "content-type": "application/json" }, body: JSON.stringify(body) });
  if (!put.ok) return json({ error: `GitHub ${put.status}`, detail: await put.text() }, 502, env);

  return json({ ok: true, path, committed_as: claims.name }, 200, env);
}

// ── entry ──────────────────────────────────────────────────────────────────
export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors(env) });
    if (request.method !== "POST") return json({ error: "POST only" }, 405, env);

    const { pathname } = new URL(request.url);
    try {
      if (pathname === "/login")   return await login(request, env);
      if (pathname === "/whoami")  return await whoami(request, env);
      if (pathname === "/recipes") return await saveRecipe(request, env);
      return json({ error: "not found" }, 404, env);
    } catch (e) {
      // Never leak internals to the client; log for us.
      console.error(e);
      return json({ error: "server error" }, 500, env);
    }
  },
};
