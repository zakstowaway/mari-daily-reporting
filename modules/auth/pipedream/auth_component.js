/**
 * SHG Auth worker — one Pipedream endpoint, three jobs, all server-side:
 *
 *   POST /recipes        commit data/recipes/<venue>.yaml AS the signed-in user
 *   POST /admin/users    list users            (admin only)
 *   POST /admin/invite   invite by email + set role/venue   (admin only)
 *   POST /admin/role     set role/venue on an existing user (admin only)
 *
 * Every request is authenticated by the caller's Supabase token (verified via
 * /auth/v1/user). PRIVILEGE (role, venue) is read ONLY from app_metadata, which
 * only the service key can write.
 *
 * The admin routes use SUPABASE_SERVICE_KEY — a secret that lives ONLY here, in
 * Pipedream, never in the browser. That is the whole reason these actions are
 * server-side: a service key in a browser would let anyone do anything.
 *
 * Env vars (Project -> Variables):
 *   SUPABASE_URL           public
 *   SUPABASE_ANON_KEY      public
 *   SUPABASE_SERVICE_KEY   SECRET (Settings -> API -> service_role) — admin only
 * GitHub connected via OAuth (no PAT).
 */

const KITCHEN = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];
const ROLES = ["admin", "bigchef", "stowfood", "hgfood", "bar", "pizza"];
const VENUES = ["stowaway", "harry_gatos", "marilynas"];
const REPO = "zakstowaway/mari-daily-reporting";

const CORS = {
  "access-control-allow-origin": "https://app.stowawaybar.com",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
};

export default defineComponent({
  props: { github: { type: "app", app: "github" } },

  async run({ steps, $ }) {
    const req = steps.trigger.event;
    // MUST await $.respond — Pipedream flushes the custom response only when the
    // promise resolves. reply returns that promise; callers `return reply(...)`.
    const reply = (status, body) =>
      $.respond({ status, headers: { "content-type": "application/json", ...CORS }, body })
        .then(() => body);

    if (req.method === "OPTIONS") return reply(204, "");
    if (req.method !== "POST") return reply(405, { error: "POST only" });

    const SUPABASE_URL = process.env.SUPABASE_URL;
    const SUPABASE_ANON_KEY = process.env.SUPABASE_ANON_KEY;
    if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return reply(500, { error: "Supabase env vars not set" });

    // ── who is calling? verify their token with Supabase ───────────────────
    const token = (req.headers?.authorization || req.headers?.Authorization || "").replace(/^Bearer /, "");
    if (!token) return reply(401, { error: "Not signed in" });
    const who = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
      headers: { apikey: SUPABASE_ANON_KEY, authorization: `Bearer ${token}` },
    });
    if (!who.ok) return reply(401, { error: "Invalid or expired session" });
    const user = await who.json();
    const app = user.app_metadata || {};
    const usr = user.user_metadata || {};
    // PRIVILEGE from app_metadata ONLY (service-writable). Name is not a
    // privilege, so it may come from either.
    const role = app.role || null;
    const allowedVenue = app.venue || null;
    const name = usr.name || app.name || user.email;

    const path = (req.path || "").replace(/\/+$/, "");
    const body = req.body || {};

    // ── admin routes — service key, admin caller only ──────────────────────
    if (path.endsWith("/admin/users") || path.endsWith("/admin/invite") || path.endsWith("/admin/role")) {
      if (role !== "admin") return reply(403, { error: "Admins only" });
      const svc = process.env.SUPABASE_SERVICE_KEY;
      if (!svc) return reply(500, { error: "SUPABASE_SERVICE_KEY not set" });
      const sbAdmin = (method, p, payload) =>
        fetch(`${SUPABASE_URL}/auth/v1/${p}`, {
          method,
          headers: { apikey: svc, authorization: `Bearer ${svc}`, "content-type": "application/json" },
          ...(payload ? { body: JSON.stringify(payload) } : {}),
        });
      const findId = async (email) => {
        const r = await sbAdmin("GET", "admin/users?per_page=200");
        const j = await r.json();
        return (j.users || []).find((u) => (u.email || "").toLowerCase() === email.toLowerCase())?.id;
      };
      const shapeUser = (u) => ({
        id: u.id, email: u.email, name: u.user_metadata?.name || "",
        role: u.app_metadata?.role || null, venue: u.app_metadata?.venue || null,
        confirmed: !!u.email_confirmed_at, last_sign_in: u.last_sign_in_at || null,
      });

      if (path.endsWith("/admin/users")) {
        const r = await sbAdmin("GET", "admin/users?per_page=200");
        if (!r.ok) return reply(502, { error: "list failed", detail: await r.text() });
        const j = await r.json();
        return reply(200, { users: (j.users || []).map(shapeUser) });
      }

      if (path.endsWith("/admin/invite")) {
        const email = String(body.email || "").trim().toLowerCase();
        const newRole = body.role || null;
        const newVenue = body.venue || null;
        if (!email) return reply(400, { error: "email required" });
        if (newRole && !ROLES.includes(newRole)) return reply(400, { error: "bad role" });
        if (newVenue && !VENUES.includes(newVenue)) return reply(400, { error: "bad venue" });

        // Supabase emails an invite link -> the user sets their own password.
        const inv = await sbAdmin("POST", "invite", { email });
        if (!inv.ok) return reply(502, { error: "invite failed", detail: await inv.text() });
        const invited = await inv.json();
        if (newRole || newVenue) {
          const meta = {};
          if (newRole) meta.role = newRole;
          if (newVenue) meta.venue = newVenue;
          await sbAdmin("PUT", `admin/users/${invited.id}`, { app_metadata: meta });
        }
        return reply(200, { ok: true, invited: email, role: newRole, venue: newVenue });
      }

      if (path.endsWith("/admin/role")) {
        const email = String(body.email || "").trim().toLowerCase();
        const newRole = body.role || null;      // null clears the role (disables access)
        const newVenue = body.venue ?? null;
        if (newRole && !ROLES.includes(newRole)) return reply(400, { error: "bad role" });
        if (newVenue && !VENUES.includes(newVenue)) return reply(400, { error: "bad venue" });
        const uid = body.id || (email ? await findId(email) : null);
        if (!uid) return reply(404, { error: "user not found" });
        const upd = await sbAdmin("PUT", `admin/users/${uid}`, {
          app_metadata: { role: newRole, venue: newVenue },
        });
        if (!upd.ok) return reply(502, { error: "update failed", detail: await upd.text() });
        return reply(200, { ok: true });
      }
    }

    // ── /recipes — kitchen role, commit AS the person ──────────────────────
    if (!KITCHEN.includes(role)) return reply(403, { error: "Your role cannot edit recipes" });

    const { venue, product, yaml } = body;
    if (!venue || !product || !yaml) return reply(400, { error: "venue, product and yaml required" });
    if (!/^[a-z_]+$/.test(venue)) return reply(400, { error: "bad venue" });
    if (!["admin", "bigchef"].includes(role) && allowedVenue && allowedVenue !== venue) {
      return reply(403, { error: `You can only edit ${allowedVenue}` });
    }

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
    const stamp = new Date().toISOString().slice(0, 10);
    const block = `\n# ${product} — entered by ${name} (${user.email}) on ${stamp}\n${String(yaml).trim()}\n`;
    const put = await gh("PUT", `contents/${path_}`, {
      message: `Recipe: ${product} (${venue}) — ${name}`,
      content: Buffer.from(current + block, "utf8").toString("base64"),
      author: { name, email: user.email },
      ...(sha ? { sha } : {}),
    });
    if (!put.ok) return reply(502, { error: `GitHub ${put.status}`, detail: await put.text() });
    return reply(200, { ok: true, path: path_, committed_as: name });
  },
});
