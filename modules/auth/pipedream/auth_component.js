/**
 * Auth + recipe save, as a Pipedream component.
 *
 * WHY PIPEDREAM AND NOT CLOUDFLARE
 * --------------------------------
 * The first cut of this was a Cloudflare Worker. It needed: a new Cloudflare
 * account, and a long-lived GitHub PAT pasted in as a secret.
 *
 * Pipedream needs neither. It already exists (workspace `stowawaybar`, project
 * `Mari Reporting`), already runs Node steps, and already writes to this repo
 * over OAuth. PIPEDREAM_BRIDGE.md chose it for exactly that reason:
 *
 *   "no long-lived PAT stored anywhere (Pipedream handles GitHub OAuth token
 *    refresh)"
 *
 * A Worker would have reintroduced the PAT this repo deliberately avoided.
 *
 * SETUP (about 10 minutes, no new accounts)
 * -----------------------------------------
 *  1. pipedream.com -> project "Mari Reporting" -> New workflow -> "SHG Auth"
 *  2. Trigger: HTTP / Webhook Requests. Choose "Return a custom response".
 *  3. Add a Node.js step, paste this file in, name it `auth`.
 *  4. Connect the GitHub account to the step (Pipedream handles the token).
 *  5. Settings -> Environment Variables:
 *       PASSWORDS   <- paste .secrets/passwords.json
 *       PEOPLE      <- paste data/people.json
 *       JWT_SECRET  <- openssl rand -base64 32
 *  6. Deploy. Put the trigger URL in dashboard/_shared/auth.js (WORKER_URL).
 *
 * Re-paste PASSWORDS whenever you add or remove someone.
 *
 * WHAT THIS IS FOR
 * ----------------
 * Zak: "one username per person, so that we can see who's inputting data."
 * Every save is committed AS the person, so `git log data/recipes/` is the
 * audit trail. This is the only place that sees a password or holds a hash.
 */

import { createHmac, pbkdf2Sync, timingSafeEqual } from "crypto";

const TOKEN_TTL_SECONDS = 12 * 60 * 60; // one shift

// ── password hashing: must match modules/auth/passwords.py exactly ──────────
function verifyPassword(password, stored) {
  const [algo, digest, iters, saltB64, hashB64] = String(stored).split("$");
  if (algo !== "pbkdf2" || digest !== "sha256") return false;
  const salt = Buffer.from(saltB64, "base64");
  const expected = Buffer.from(hashB64, "base64");
  const actual = pbkdf2Sync(password, salt, parseInt(iters, 10), expected.length, "sha256");
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

// ── tokens: JWT HS256 ──────────────────────────────────────────────────────
const b64url = (b) => Buffer.from(b).toString("base64url");

function sign(payload, secret) {
  const head = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = b64url(JSON.stringify(payload));
  const sig = createHmac("sha256", secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${sig}`;
}

function verify(token, secret) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return null;
  const [head, body, sig] = parts;
  const expect = createHmac("sha256", secret).update(`${head}.${body}`).digest("base64url");
  const a = Buffer.from(sig), b = Buffer.from(expect);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  const payload = JSON.parse(Buffer.from(body, "base64url").toString());
  if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null;
  return payload;
}

const KITCHEN = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];
const REPO = "zakstowaway/mari-daily-reporting";

// Only our own app may call this — a token is at stake.
const CORS = {
  "access-control-allow-origin": "https://app.stowawaybar.com",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
};

export default defineComponent({
  props: {
    github: { type: "app", app: "github" }, // OAuth — no PAT anywhere
  },

  async run({ steps, $ }) {
    const req = steps.trigger.event;
    const reply = (status, body) => {
      $.respond({ status, headers: { "content-type": "application/json", ...CORS }, body });
      return body;
    };

    if (req.method === "OPTIONS") return reply(204, "");
    if (req.method !== "POST") return reply(405, { error: "POST only" });

    const passwords = JSON.parse(process.env.PASSWORDS || "{}");
    const people = (JSON.parse(process.env.PEOPLE || "{}") || {}).people || {};
    const JWT_SECRET = process.env.JWT_SECRET;
    if (!JWT_SECRET) return reply(500, { error: "JWT_SECRET not set" });

    // Pipedream gives the path after the trigger URL, e.g. /login
    const path = (req.path || "/login").replace(/\/+$/, "") || "/login";
    const body = req.body || {};

    // ── /login ─────────────────────────────────────────────────────────────
    if (path.endsWith("/login")) {
      const u = String(body.username || "").trim().toLowerCase();
      const stored = passwords[u];
      const person = people[u];

      // Same answer AND same timing for "no such user" and "wrong password".
      // Don't leak who has an account.
      const DUMMY =
        "pbkdf2$sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
      const ok = verifyPassword(String(body.password || ""), stored || DUMMY);
      if (!stored || !ok || !person || person.active === false) {
        return reply(401, { error: "Invalid credentials" });
      }

      const now = Math.floor(Date.now() / 1000);
      const token = sign(
        { sub: u, name: person.name, role: person.role, venue: person.venue || null,
          iat: now, exp: now + TOKEN_TTL_SECONDS },
        JWT_SECRET
      );
      return reply(200, {
        token,
        user: { username: u, name: person.name, role: person.role, venue: person.venue || null },
      });
    }

    // ── everything below needs a valid token ───────────────────────────────
    const auth = req.headers?.authorization || req.headers?.Authorization || "";
    const claims = verify(auth.replace(/^Bearer /, ""), JWT_SECRET);
    if (!claims) return reply(401, { error: "Invalid or expired token" });

    if (path.endsWith("/whoami")) {
      return reply(200, {
        user: { username: claims.sub, name: claims.name, role: claims.role, venue: claims.venue },
      });
    }

    // ── /recipes ───────────────────────────────────────────────────────────
    if (path.endsWith("/recipes")) {
      // Role comes from the SIGNED TOKEN, never the request body — otherwise
      // anyone claims admin by editing the POST.
      if (!KITCHEN.includes(claims.role)) return reply(403, { error: "Your role cannot edit recipes" });

      const { venue, product, yaml } = body;
      if (!venue || !product || !yaml) return reply(400, { error: "venue, product and yaml required" });
      if (!/^[a-z_]+$/.test(venue)) return reply(400, { error: "bad venue" });
      if (!["admin", "bigchef"].includes(claims.role) && claims.venue && claims.venue !== venue) {
        return reply(403, { error: `You can only edit ${claims.venue}` });
      }

      const path_ = `data/recipes/${venue}.yaml`;
      // Read the OAuth token OUT here, where `this` is unambiguously the
      // component instance. Inside the arrow below `this` would be lexical and
      // fragile; pulling it out is clearer and correct.
      const ghToken = this.github.$auth.oauth_access_token;
      const gh = async (method, url, payload) => {
        return await fetch(`https://api.github.com/repos/${REPO}/${url}`, {
          method,
          headers: {
            authorization: `Bearer ${ghToken}`,
            accept: "application/vnd.github+json",
            "user-agent": "shg-auth",
            ...(payload ? { "content-type": "application/json" } : {}),
          },
          ...(payload ? { body: JSON.stringify(payload) } : {}),
        });
      };

      let sha, current = "";
      const existing = await gh("GET", `contents/${path_}`);
      if (existing.ok) {
        const j = await existing.json();
        sha = j.sha;
        current = Buffer.from(j.content, "base64").toString("utf8");
      }

      // Append a version, never overwrite: recipes are effective-dated so old
      // COGS stays reproducible. ARCHITECTURE.md decision 2.
      const stamp = new Date().toISOString().slice(0, 10);
      const block = `\n# ${product} — entered by ${claims.name} (${claims.sub}) on ${stamp}\n${String(yaml).trim()}\n`;

      const put = await gh("PUT", `contents/${path_}`, {
        message: `Recipe: ${product} (${venue}) — ${claims.name}`,
        content: Buffer.from(current + block, "utf8").toString("base64"),
        // THE ATTRIBUTION. git log now says who, not 'stowfood'.
        author: { name: claims.name, email: `${claims.sub}@stowawaybar.com` },
        ...(sha ? { sha } : {}),
      });
      if (!put.ok) return reply(502, { error: `GitHub ${put.status}`, detail: await put.text() });

      return reply(200, { ok: true, path: path_, committed_as: claims.name });
    }

    return reply(404, { error: "not found" });
  },
});
