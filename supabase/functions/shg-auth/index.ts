// SHG Auth — Supabase Edge Function. Free, always-on replacement for the
// Pipedream worker (eotwefx7cim9jou.m.pipedream.net) which died with Pipedream's
// credits. Same contract, same routes:
//
//   POST /shg-auth/admin/users    list users            (admin only)
//   POST /shg-auth/admin/invite   invite + set role     (admin only)
//   POST /shg-auth/admin/role     set role/venue/link   (admin only)
//   POST /shg-auth/recipes        commit data/recipes/<venue>.yaml  (kitchen)
//   POST /shg-auth/prep           append data/prep_sessions/<venue>.yaml (kitchen)
//
// Every request is authenticated by the caller's Supabase token (verified via
// /auth/v1/user). PRIVILEGE (role, venue) is read ONLY from app_metadata.
//
// The admin routes use the service role key — AUTO-INJECTED by Supabase as
// SUPABASE_SERVICE_ROLE_KEY, so there is no secret to set. The repo-write routes
// use GITHUB_TOKEN (set once as a function secret). Deploy with verify_jwt=false
// (we verify the token ourselves and must answer CORS preflight).

const KITCHEN = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];
const ROLES = ["admin", "bigchef", "stowfood", "hgfood", "bar", "pizza"];
const VENUES = ["stowaway", "harry_gatos", "marilynas"];
const REPO = "zakstowaway/mari-daily-reporting";

const CORS = {
  "access-control-allow-origin": "https://app.stowawaybar.com",
  "access-control-allow-methods": "POST, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const GITHUB_TOKEN = Deno.env.get("GITHUB_TOKEN") || "";

const reply = (status: number, body: unknown) =>
  new Response(status === 204 ? null : (typeof body === "string" ? body : JSON.stringify(body)), {
    status,
    headers: { "content-type": "application/json", ...CORS },
  });

// utf8 <-> base64 (chunked; recipe/prep files are small but be safe)
function toB64(str: string): string {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode(...bytes.subarray(i, i + CH));
  return btoa(bin);
}
function fromB64(b64: string): string {
  const bin = atob((b64 || "").replace(/\n/g, ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return reply(204, "");
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return reply(500, { error: "Supabase env vars not set" });

  // who is calling? verify their token
  const token = (req.headers.get("authorization") || "").replace(/^Bearer /, "");
  if (!token) return reply(401, { error: "Not signed in" });
  const who = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { apikey: SUPABASE_ANON_KEY, authorization: `Bearer ${token}` },
  });
  if (!who.ok) return reply(401, { error: "Invalid or expired session" });
  const user = await who.json();
  const app = user.app_metadata || {};
  const usr = user.user_metadata || {};
  const role = app.role || null;
  const allowedVenue = app.venue || null;
  const name = usr.name || app.name || user.email;

  const path = new URL(req.url).pathname.replace(/\/+$/, "");
  let body: Record<string, any> = {};
  try { body = await req.json(); } catch { /* empty body ok */ }

  // ── admin routes — service role key, admin caller only ───────────────────
  if (path.endsWith("/admin/users") || path.endsWith("/admin/invite") || path.endsWith("/admin/role")) {
    if (role !== "admin") return reply(403, { error: "Admins only" });
    if (!SERVICE_KEY) return reply(500, { error: "service role key not available" });
    const sbAdmin = (method: string, p: string, payload?: unknown) =>
      fetch(`${SUPABASE_URL}/auth/v1/${p}`, {
        method,
        headers: { apikey: SERVICE_KEY, authorization: `Bearer ${SERVICE_KEY}`, "content-type": "application/json" },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
      });
    const findId = async (email: string) => {
      const r = await sbAdmin("GET", "admin/users?per_page=200");
      const j = await r.json();
      return (j.users || []).find((u: any) => (u.email || "").toLowerCase() === email.toLowerCase())?.id;
    };
    const getUser = async (uid: string) => {
      const r = await sbAdmin("GET", `admin/users/${uid}`);
      return r.ok ? r.json() : null;
    };
    const shapeUser = (u: any) => ({
      id: u.id, email: u.email, name: u.user_metadata?.name || "",
      role: u.app_metadata?.role || null, venue: u.app_metadata?.venue || null,
      employee: u.app_metadata?.employee_id || null,
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
      const inv = await sbAdmin("POST", "invite", { email });
      if (!inv.ok) return reply(502, { error: "invite failed", detail: await inv.text() });
      const invited = await inv.json();
      const meta: Record<string, unknown> = {};
      if (newRole) meta.role = newRole;
      if (newVenue) meta.venue = newVenue;
      if (body.employee) meta.employee_id = String(body.employee);
      if (Object.keys(meta).length) await sbAdmin("PUT", `admin/users/${invited.id}`, { app_metadata: meta });
      return reply(200, { ok: true, invited: email, role: newRole, venue: newVenue });
    }

    if (path.endsWith("/admin/role")) {
      const email = String(body.email || "").trim().toLowerCase();
      const newRole = body.role || null;
      const newVenue = body.venue ?? null;
      if (newRole && !ROLES.includes(newRole)) return reply(400, { error: "bad role" });
      if (newVenue && !VENUES.includes(newVenue)) return reply(400, { error: "bad venue" });
      const uid = body.id || (email ? await findId(email) : null);
      if (!uid) return reply(404, { error: "user not found" });
      const cur = await getUser(uid);
      const meta: Record<string, unknown> = { ...(cur?.app_metadata || {}) };
      if ("role" in body) meta.role = newRole;
      if ("venue" in body) meta.venue = newVenue;
      if ("employee" in body) meta.employee_id = body.employee ? String(body.employee) : null;
      const upd = await sbAdmin("PUT", `admin/users/${uid}`, { app_metadata: meta });
      if (!upd.ok) return reply(502, { error: "update failed", detail: await upd.text() });
      return reply(200, { ok: true });
    }
  }

  // ── repo writes — kitchen role, committed AS the person ──────────────────
  if (!KITCHEN.includes(role)) return reply(403, { error: "Your role cannot edit recipes" });
  if (!GITHUB_TOKEN) return reply(500, { error: "GITHUB_TOKEN not set on the function" });

  const { venue, product } = body;
  if (!venue || !product) return reply(400, { error: "venue and product required" });
  if (!/^[a-z_]+$/.test(venue)) return reply(400, { error: "bad venue" });
  if (!["admin", "bigchef"].includes(role) && allowedVenue && allowedVenue !== venue) {
    return reply(403, { error: `You can only edit ${allowedVenue}` });
  }

  const gh = (method: string, url: string, payload?: unknown) =>
    fetch(`https://api.github.com/repos/${REPO}/${url}`, {
      method,
      headers: {
        authorization: `Bearer ${GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "user-agent": "shg-auth",
        ...(payload ? { "content-type": "application/json" } : {}),
      },
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
  const stamp = new Date().toISOString().slice(0, 10);
  const appendCommit = async (path_: string, block: string, message: string) => {
    let sha: string | undefined, current = "";
    const existing = await gh("GET", `contents/${path_}`);
    if (existing.ok) {
      const j = await existing.json();
      sha = j.sha;
      current = fromB64(j.content);
    }
    const put = await gh("PUT", `contents/${path_}`, {
      message,
      content: toB64(current + block),
      author: { name, email: user.email },
      ...(sha ? { sha } : {}),
    });
    if (!put.ok) return { ok: false, detail: await put.text(), status: put.status };
    return { ok: true, path: path_ };
  };

  if (path.endsWith("/prep")) {
    const minutes = Number(body.minutes);
    if (!(minutes > 0) || minutes > 600) return reply(400, { error: "minutes must be 0-600" });
    const whoId = app.employee_id || name;
    const safe = String(product).replace(/"/g, "'");
    const block =
      `- product: "${safe}"\n  who: "${whoId}"\n  who_name: "${name}"\n` +
      `  minutes: ${minutes}\n  recorded_on: ${stamp}\n  recorded_by: "${user.email}"\n`;
    const res = await appendCommit(`data/prep_sessions/${venue}.yaml`, block,
      `Prep: ${safe} ${minutes}min (${venue}) - ${name}`);
    if (!res.ok) return reply(502, { error: `GitHub ${res.status}`, detail: res.detail });
    return reply(200, { ok: true, path: res.path, minutes, who: whoId });
  }

  const { yaml } = body;
  if (!yaml) return reply(400, { error: "yaml required" });
  const block = `\n# ${product} - entered by ${name} (${user.email}) on ${stamp}\n${String(yaml).trim()}\n`;
  const res = await appendCommit(`data/recipes/${venue}.yaml`, block,
    `Recipe: ${product} (${venue}) - ${name}`);
  if (!res.ok) return reply(502, { error: `GitHub ${res.status}`, detail: res.detail });
  return reply(200, { ok: true, path: res.path, committed_as: name });
});
