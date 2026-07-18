/**
 * Shared auth. One login per person, every app.
 *
 * TWO MODES, on purpose — this is a live migration, not a rewrite.
 *
 *   WORKER  (WORKER_URL set)  The real thing. Password goes to the Worker,
 *                             which holds the hashes and returns a signed
 *                             token. The browser never sees a hash or a salt.
 *
 *   LEGACY  (no WORKER_URL)   The old dashboard/users.json scheme:
 *                             sha256(global_salt + password), checked HERE, in
 *                             the browser, against hashes that also shipped
 *                             here. It is a speed bump, not a lock. It stays
 *                             ONLY so the live dashboard keeps working until
 *                             the Worker is deployed. Delete it after.
 *
 * The mode is visible: Auth.mode() returns 'worker' or 'legacy'. Anything that
 * WRITES must refuse in legacy mode — see requireToken(). A shared station
 * password can't tell you who entered a recipe, which is the whole point
 * (Zak: "one username per person, so that we can see who's inputting data").
 */

// The deployed SHG Auth workflow (Pipedream, project Mari Reporting).
// Live once Zak sets PASSWORDS/PEOPLE/JWT_SECRET and clicks Deploy —
// see modules/auth/README.md. Safe to set now: only the not-yet-live
// recipe page reads this; the main dashboard has its own inline auth.
export const WORKER_URL = "https://eotwefx7cim9jou.m.pipedream.net";

export const Auth = (() => {
  const KEY = "shg.session";
  let CURRENT = null;
  let USERS = null;      // legacy only

  const mode = () => (WORKER_URL ? "worker" : "legacy");

  // ── legacy (delete with users.json) ──────────────────────────────────────
  async function sha256Hex(s) {
    const buf = new TextEncoder().encode(s);
    const digest = await crypto.subtle.digest("SHA-256", buf);
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function legacyLogin(username, password) {
    if (!USERS) {
      const r = await fetch("/users.json?t=" + Date.now());
      if (!r.ok) throw new Error(`users.json ${r.status}`);
      USERS = await r.json();
    }
    const u = String(username).trim().toLowerCase();
    const rec = USERS.users[u];
    if (!rec) return null;
    if ((await sha256Hex(USERS.salt + password)) !== rec.hash) return null;
    return { username: u, name: rec.display, role: rec.role, venue: null, token: null, legacy: true };
  }

  // ── worker ───────────────────────────────────────────────────────────────
  async function workerLogin(username, password) {
    const r = await fetch(`${WORKER_URL}/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username: String(username).trim().toLowerCase(), password }),
    });
    if (r.status === 401) return null;
    if (!r.ok) throw new Error(`login failed (${r.status})`);
    const { token, user } = await r.json();
    return { ...user, token, legacy: false };
  }

  // ── public ───────────────────────────────────────────────────────────────
  async function login(username, password) {
    const session = WORKER_URL ? await workerLogin(username, password)
                               : await legacyLogin(username, password);
    if (!session) return null;
    CURRENT = session;
    sessionStorage.setItem(KEY, JSON.stringify(session));
    return session;
  }

  function current() {
    if (CURRENT) return CURRENT;
    try {
      const s = sessionStorage.getItem(KEY);
      if (s) CURRENT = JSON.parse(s);
    } catch { /* ignore */ }
    return CURRENT;
  }

  function logout() {
    CURRENT = null;
    sessionStorage.removeItem(KEY);
  }

  /**
   * The token for a write. Throws rather than letting a write proceed
   * unattributed or unauthenticated.
   */
  function requireToken() {
    const c = current();
    if (!c) throw new Error("Not signed in.");
    if (c.legacy || !c.token) {
      throw new Error(
        "Saving is disabled until the auth Worker is deployed. The current login " +
        "is a shared station password checked in the browser — it cannot prove who " +
        "you are, so a save would have no name on it. See modules/auth/README.md."
      );
    }
    return c.token;
  }

  const hasRole = (...allowed) => { const c = current(); return !!c && allowed.includes(c.role); };
  const canWrite = () => { try { requireToken(); return true; } catch { return false; } };

  const KITCHEN_ROLES = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];

  /** Gate a page: render a login card into `mount`, call onOk when in. */
  async function gate(mount, { roles = null, onOk }) {
    const go = () => {
      const c = current();
      if (!c) return false;
      if (roles && !roles.includes(c.role)) {
        mount.innerHTML = `<div class="wrap"><div class="card"><b>No access</b>
          <div class="muted">Signed in as ${c.name} (${c.role}). Needs: ${roles.join(", ")}.
          <a href="#" id="_lo">sign out</a></div></div></div>`;
        mount.querySelector("#_lo").onclick = () => { logout(); location.reload(); };
        return true;
      }
      mount.style.display = "none";
      onOk(c);
      return true;
    };
    if (go()) return;

    mount.innerHTML = `
      <div class="login-wrap"><div class="login-card">
        <form id="_lf">
          <label for="_u">Username</label>
          <input id="_u" autocomplete="username" autocapitalize="none" autofocus>
          <label for="_p">Password</label>
          <input id="_p" type="password" autocomplete="current-password">
          <button type="submit">Sign In</button>
          <div class="err" id="_e"></div>
          ${mode() === "legacy"
            ? `<div class="muted" style="margin-top:10px">Shared station login. Saving is
               off until personal accounts are live.</div>` : ""}
        </form>
      </div></div>`;
    mount.querySelector("#_lf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = mount.querySelector("#_e");
      const btn = mount.querySelector("button");
      err.textContent = ""; btn.disabled = true; btn.textContent = "Checking…";
      try {
        const c = await login(mount.querySelector("#_u").value, mount.querySelector("#_p").value);
        if (!c) { err.textContent = "Invalid credentials"; return; }
        go();
      } catch (ex) {
        err.textContent = String(ex.message || ex);
      } finally {
        btn.disabled = false; btn.textContent = "Sign In";
      }
    });
  }

  return { login, logout, current, hasRole, canWrite, requireToken, gate, mode, KITCHEN_ROLES, WORKER_URL };
})();
