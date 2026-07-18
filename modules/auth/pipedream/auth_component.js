/**
 * Recipe save endpoint, as a Pipedream component.
 *
 * Auth is now Supabase's job. This does exactly two things:
 *   1. verify the caller's Supabase token (by asking Supabase who it belongs to)
 *   2. commit data/recipes/<venue>.yaml to GitHub AS that person
 *
 * No passwords, no hashing, no JWT signing, no PASSWORDS env var. All of that
 * moved to Supabase when Zak chose a managed provider (2026-07-19). This file
 * is the only server-side piece left, because a GitHub write needs a token the
 * browser must never hold.
 *
 * SETUP (SHG Auth workflow, Node step):
 *   - GitHub account: already connected (OAuth, no PAT).
 *   - Project -> Variables (not secret; both are public values):
 *       SUPABASE_URL       https://<ref>.supabase.co
 *       SUPABASE_ANON_KEY  the anon public key
 *
 * ATTRIBUTION (Zak: "see who's inputting data"): the commit author is the
 * signed-in user's email/name from Supabase, so `git log data/recipes/` says
 * who entered what.
 */

const KITCHEN = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];
const REPO = "zakstowaway/mari-daily-reporting";

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

    const SUPABASE_URL = process.env.SUPABASE_URL;
    const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY;
    if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return reply(500, { error: "Supabase env vars not set" });

    // ── verify the token by asking Supabase who it is ──────────────────────
    // No local crypto, no shared secret. Supabase validates signature + expiry
    // and returns the user (with app_metadata.role, which only an admin can set).
    const token = (req.headers?.authorization || req.headers?.Authorization || "").replace(/^Bearer /, "");
    if (!token) return reply(401, { error: "Not signed in" });

    const who = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
      headers: { apikey: SUPABASE_ANON_KEY, authorization: `Bearer ${token}` },
    });
    if (!who.ok) return reply(401, { error: "Invalid or expired session" });
    const user = await who.json();
    const meta = { ...(user.app_metadata || {}), ...(user.user_metadata || {}) };
    const role = meta.role || null;
    const name = meta.name || user.email;

    // Role comes from Supabase app_metadata (admin-set), never the request body.
    if (!KITCHEN.includes(role)) return reply(403, { error: "Your role cannot edit recipes" });

    const { venue, product, yaml } = req.body || {};
    if (!venue || !product || !yaml) return reply(400, { error: "venue, product and yaml required" });
    if (!/^[a-z_]+$/.test(venue)) return reply(400, { error: "bad venue" });
    if (!["admin", "bigchef"].includes(role) && meta.venue && meta.venue !== venue) {
      return reply(403, { error: `You can only edit ${meta.venue}` });
    }

    // ── commit to GitHub, as the person ────────────────────────────────────
    const path_ = `data/recipes/${venue}.yaml`;
    const ghToken = this.github.$auth.oauth_access_token;
    const gh = (method, url, payload) =>
      fetch(`https://api.github.com/repos/${REPO}/${url}`, {
        method,
        headers: {
          authorization: `Bearer ${ghToken}`,
          accept: "application/vnd.github+json",
          "user-agent": "shg-auth",
          ...(payload ? { "content-type": "application/json" } : {}),
        },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
      });

    let sha, current = "";
    const existing = await gh("GET", `contents/${path_}`);
    if (existing.ok) {
      const j = await existing.json();
      sha = j.sha;
      current = Buffer.from(j.content, "base64").toString("utf8");
    }

    // Append a version, never overwrite — recipes are effective-dated so old
    // COGS stays reproducible. ARCHITECTURE.md decision 2.
    const stamp = new Date().toISOString().slice(0, 10);
    const block = `\n# ${product} — entered by ${name} (${user.email}) on ${stamp}\n${String(yaml).trim()}\n`;

    const put = await gh("PUT", `contents/${path_}`, {
      message: `Recipe: ${product} (${venue}) — ${name}`,
      content: Buffer.from(current + block, "utf8").toString("base64"),
      author: { name, email: user.email },   // THE ATTRIBUTION
      ...(sha ? { sha } : {}),
    });
    if (!put.ok) return reply(502, { error: `GitHub ${put.status}`, detail: await put.text() });

    return reply(200, { ok: true, path: path_, committed_as: name });
  },
});
