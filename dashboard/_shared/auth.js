/**
 * Auth, backed by Supabase. Self-service: users sign in, sign up (by invite),
 * reset their own password. Supabase handles the whole credential lifecycle —
 * we never see or store a password or a hash.
 *
 * WHY A PROVIDER (Zak, 2026-07-19: "passwords and logins ... completely managed
 * by the user ... forgot password and everything")
 * ------------------------------------------------------------------------------
 * The previous build was admin-provisioned: a CLI set each password into a
 * fixed env var. That cannot do self-service — there is nowhere to write a new
 * user or a changed password at runtime, and no way to email a reset link.
 * Hand-rolling reset flows (token expiry, enumeration, deliverability) is a
 * known footgun. Supabase's are battle-tested and free.
 *
 * WHAT LIVES WHERE
 * ----------------
 *   Supabase      identities + passwords + reset emails. NOT business data.
 *   this repo     recipes, COGS, everything that matters. Unchanged.
 *   Pipedream     verifies a Supabase token, then commits a recipe AS the user.
 *
 * ROLE is admin-controlled, not self-service — you don't let a chef self-assign
 * admin. It lives in the Supabase user's app_metadata.role (only settable with
 * the service key) and rides in the token. Password is the user's; role is
 * yours. See modules/auth/README.md.
 *
 * The public API (gate/current/requireToken/canWrite/logout/KITCHEN_ROLES) is
 * unchanged from the previous version, so pages that used it don't change.
 */

import { SUPABASE_URL, SUPABASE_ANON_KEY, WORKER_URL } from "./config.js";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

export const Auth = (() => {
  const configured = !!(SUPABASE_URL && SUPABASE_ANON_KEY);
  const sb = configured ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY) : null;

  const KITCHEN_ROLES = ["admin", "bigchef", "stowfood", "hgfood", "pizza"];

  // Shape a Supabase user into the {username,name,role,venue,token} the app uses.
  function shape(session) {
    if (!session?.user) return null;
    const u = session.user;
    const meta = { ...(u.app_metadata || {}), ...(u.user_metadata || {}) };
    return {
      username: u.email,
      email: u.email,
      name: meta.name || u.email,
      role: meta.role || null,          // set by admin in app_metadata
      venue: meta.venue || null,
      employee: (u.app_metadata || {}).employee_id || null,  // -> wage rate
      token: session.access_token,      // the Supabase JWT the Worker verifies
    };
  }

  let CACHE = null, _refreshedForRole = false;
  async function current() {
    if (!sb) return null;
    const { data } = await sb.auth.getSession();
    let shaped = shape(data.session);
    // A role granted AFTER sign-in is not in the cached JWT (it caches
    // app_metadata at issue time). Refresh once to pick it up, so "an admin
    // just gave me access" works without a manual re-login. Verified: this was
    // exactly Zak's case after being made admin mid-session.
    if (shaped && !shaped.role && !_refreshedForRole) {
      _refreshedForRole = true;
      const { data: r } = await sb.auth.refreshSession();
      if (r?.session) shaped = shape(r.session);
    }
    CACHE = shaped;
    return CACHE;
  }

  async function login(email, password) {
    if (!sb) throw new Error("Auth not configured yet — Supabase keys missing.");
    const { data, error } = await sb.auth.signInWithPassword({ email: email.trim(), password });
    if (error) return null;
    CACHE = shape(data.session);
    return CACHE;
  }

  async function signUp(email, password, name) {
    if (!sb) throw new Error("Auth not configured yet.");
    const { error } = await sb.auth.signUp({
      email: email.trim(), password,
      options: { data: { name: name || "" } },   // -> user_metadata; role is set by admin
    });
    return error ? { ok: false, error: error.message } : { ok: true };
  }

  async function forgotPassword(email) {
    if (!sb) throw new Error("Auth not configured yet.");
    const { error } = await sb.auth.resetPasswordForEmail(email.trim(), {
      redirectTo: `${location.origin}/recipes/#reset`,
    });
    return error ? { ok: false, error: error.message } : { ok: true };
  }

  async function completePasswordReset(newPassword) {
    if (!sb) throw new Error("Auth not configured yet.");
    const { error } = await sb.auth.updateUser({ password: newPassword });
    return error ? { ok: false, error: error.message } : { ok: true };
  }

  // Microsoft 365 / Outlook "Safe Links" opens one-time recovery LINKS to scan
  // them and burns the token before the user ever clicks. The same email also
  // carries a 6-digit code ({{ .Token }}) that no scanner consumes — verifying it
  // establishes the recovery session without relying on the link (Zak 2026-07-21).
  async function verifyRecoveryCode(email, code) {
    if (!sb) throw new Error("Auth not configured yet.");
    const { error } = await sb.auth.verifyOtp({ email: (email || "").trim(), token: (code || "").trim(), type: "recovery" });
    return error ? { ok: false, error: error.message } : { ok: true };
  }

  async function logout() {
    if (sb) await sb.auth.signOut();
    CACHE = null;
  }

  /** The token for a write. Throws rather than let a write go out unattributed. */
  function requireToken() {
    if (!CACHE) throw new Error("Not signed in.");
    if (!CACHE.token) throw new Error("No session token — sign in again.");
    return CACHE.token;
  }
  const canWrite = () => !!(CACHE && CACHE.token && KITCHEN_ROLES.includes(CACHE.role));
  const hasRole = (...allowed) => !!(CACHE && allowed.includes(CACHE.role));

  /**
   * Gate a page: render a sign-in / sign-up / forgot card, call onOk when in.
   * Handles the reset-link return (#reset) too.
   */
  async function gate(mount, { roles = null, onOk }) {
    if (!configured) {
      mount.innerHTML = `<div class="wrap"><div class="card"><b>Login isn't set up yet</b>
        <div class="muted">Supabase keys are missing from _shared/config.js —
        see modules/auth/README.md.</div></div></div>`;
      return;
    }

    // Returning from a reset OR invite email? Both need the user to set a
    // password. Supabase (detectSessionInUrl) has already put a session in place.
    if (location.hash.includes("reset") || location.hash.includes("type=recovery") ||
        location.hash.includes("type=invite") ||
        // A burned/expired one-time link lands here with an error hash instead of
        // a session. Still route to the reset screen so the 6-digit-code fallback
        // is offered rather than dumping the user on a bare sign-in page.
        location.hash.includes("otp_expired") || location.hash.includes("access_denied")) {
      return renderReset(mount, onOk, roles);
    }

    const c = await current();
    if (c) return admit(c, mount, onOk, roles);
    renderSignIn(mount, onOk, roles);
  }

  function admit(c, mount, onOk, roles) {
    if (roles && !roles.includes(c.role)) {
      mount.innerHTML = `<div class="wrap"><div class="card"><b>No access</b>
        <div class="muted">Signed in as ${c.name} (${c.role || "no role set"}).
        This page needs: ${roles.join(", ")}. Ask an admin to set your role.
        <a href="#" id="_lo">sign out</a></div></div></div>`;
      mount.querySelector("#_lo").onclick = async () => { await logout(); location.reload(); };
      return;
    }
    mount.style.display = "none";
    onOk(c);
  }

  const brandMark = `
    <div class="group-mark">
      <img class="brand-logo" src="/logo_512.png" alt="Stowaway Hospitality Group">
      <div class="brand-sub">Stowaway Hospitality Group</div>
      <div class="venues">
        <span class="venue-chip stow">Stowaway</span>
        <span class="venue-chip hg">Harry Gatos</span>
        <span class="venue-chip mari">Marilyna's</span>
      </div>
    </div>`;
  const card = (inner) => `<div class="login-wrap"><div class="login-card">${brandMark}${inner}</div></div>`;

  function renderSignIn(mount, onOk, roles) {
    mount.style.display = "";
    mount.innerHTML = card(`
      <form id="_lf">
        <label for="_e">Email</label><input id="_e" type="email" autocomplete="username" autofocus>
        <label for="_p">Password</label><input id="_p" type="password" autocomplete="current-password">
        <button type="submit">Sign In</button>
        <div class="err" id="_er"></div>
        <div class="muted" style="margin-top:10px;display:flex;justify-content:space-between">
          <a href="#" id="_forgot">Forgot password?</a>
          <a href="#" id="_signup">Create account</a>
        </div>
      </form>`);
    const err = mount.querySelector("#_er");
    mount.querySelector("#_lf").addEventListener("submit", async (e) => {
      e.preventDefault(); err.textContent = "";
      const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "Checking…";
      try {
        const c = await login(mount.querySelector("#_e").value, mount.querySelector("#_p").value);
        if (!c) { err.textContent = "Invalid email or password"; return; }
        admit(c, mount, onOk, roles);
      } finally { btn.disabled = false; btn.textContent = "Sign In"; }
    });
    mount.querySelector("#_forgot").onclick = (e) => { e.preventDefault(); renderForgot(mount, onOk, roles); };
    mount.querySelector("#_signup").onclick = (e) => { e.preventDefault(); renderSignUp(mount, onOk, roles); };
  }

  function renderSignUp(mount, onOk, roles) {
    mount.innerHTML = card(`
      <form id="_sf">
        <label for="_n">Your name</label><input id="_n" autocomplete="name" autofocus>
        <label for="_e">Email</label><input id="_e" type="email" autocomplete="username">
        <label for="_p">Password (8+ chars)</label><input id="_p" type="password" autocomplete="new-password">
        <button type="submit">Create account</button>
        <div class="err" id="_er"></div>
        <div class="muted" style="margin-top:10px">An admin sets what you can access.
          <a href="#" id="_back">Back to sign in</a></div>
      </form>`);
    const err = mount.querySelector("#_er");
    mount.querySelector("#_sf").addEventListener("submit", async (e) => {
      e.preventDefault(); err.textContent = "";
      const r = await signUp(mount.querySelector("#_e").value, mount.querySelector("#_p").value,
                             mount.querySelector("#_n").value);
      err.style.color = r.ok ? "var(--green)" : "var(--red)";
      err.textContent = r.ok ? "Account created. Check your email to confirm, then sign in."
                             : r.error;
    });
    mount.querySelector("#_back").onclick = (e) => { e.preventDefault(); renderSignIn(mount, onOk, roles); };
  }

  // ONE obvious path: enter email → we send a 6-digit code → land straight on the
  // code-entry screen (below). No "link vs code" fork, no separate "have a code?"
  // step to notice. (The email still contains a link, but Outlook Safe Links burns
  // one-time links, so we lead with the code, which nothing can pre-consume.)
  function renderForgot(mount, onOk, roles) {
    mount.innerHTML = card(`
      <form id="_ff">
        <div class="muted" style="margin:-2px 0 12px">Enter your email and we'll send a 6-digit reset code.</div>
        <label for="_e">Email</label><input id="_e" type="email" autocomplete="username" autofocus>
        <button type="submit">Send reset code</button>
        <div class="err" id="_er"></div>
        <div class="muted" style="margin-top:10px"><a href="#" id="_back">Back to sign in</a></div>
      </form>`);
    const err = mount.querySelector("#_er");
    mount.querySelector("#_ff").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = mount.querySelector("#_e").value.trim();
      if (!email) { err.style.color = "var(--red)"; err.textContent = "Enter your email first."; return; }
      const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "Sending…";
      try { await forgotPassword(email); } catch (_) { /* don't leak whether the email exists */ }
      renderReset(mount, onOk, roles, email);   // advance straight to code + new password
    });
    mount.querySelector("#_back").onclick = (e) => { e.preventDefault(); renderSignIn(mount, onOk, roles); };
  }

  async function renderReset(mount, onOk, roles, prefillEmail) {
    mount.style.display = "";
    // Did the emailed LINK already establish a session? If a scanner burned the
    // one-time link (or it expired), there's no session — ask for the 6-digit code
    // from the same email, which nothing can pre-consume.
    const linked = !!(await current());
    const esc = s => (s || "").replace(/"/g, "&quot;").replace(/</g, "&lt;");
    let codeFields = "";
    if (!linked && prefillEmail) {
      // Straight from "Send reset code": we already know the email, so ask only for
      // the code + new password — no redundant email re-entry.
      codeFields = `
        <div class="muted" style="margin:-2px 0 12px">We just emailed a <b>6-digit code</b> to <b>${esc(prefillEmail)}</b>. Enter it below with a new password. (Can take a minute — check spam.)</div>
        <input id="_e" type="hidden" value="${esc(prefillEmail)}">
        <label for="_c">6-digit code</label><input id="_c" inputmode="numeric" autocomplete="one-time-code" maxlength="8" autofocus>`;
    } else if (!linked) {
      // Arrived via a reset LINK (maybe burned by a scanner) — email unknown, ask for it.
      codeFields = `
        <div class="muted" style="margin:-2px 0 12px">Enter the <b>6-digit code</b> from your reset email, plus a new password.</div>
        <label for="_e">Email</label><input id="_e" type="email" autocomplete="username" autofocus>
        <label for="_c">6-digit code</label><input id="_c" inputmode="numeric" autocomplete="one-time-code" maxlength="8">`;
    }
    mount.innerHTML = card(`
      <form id="_rf">
        ${codeFields}
        <label for="_p">New password (8+ chars)</label>
        <input id="_p" type="password" autocomplete="new-password" ${linked ? "autofocus" : ""}>
        <button type="submit">Set new password</button>
        <div class="err" id="_er"></div>
        ${linked ? "" : '<div class="muted" style="margin-top:10px"><a href="#" id="_back">Back to sign in</a></div>'}
      </form>`);
    const err = mount.querySelector("#_er");
    const _bk = mount.querySelector("#_back"); if (_bk) _bk.onclick = (e) => { e.preventDefault(); renderSignIn(mount, onOk, roles); };
    mount.querySelector("#_rf").addEventListener("submit", async (e) => {
      e.preventDefault(); err.style.color = "var(--red)"; err.textContent = "";
      const btn = e.target.querySelector("button"); btn.disabled = true;
      try {
        if (!linked) {
          const v = await verifyRecoveryCode(mount.querySelector("#_e").value, mount.querySelector("#_c").value);
          if (!v.ok) { err.textContent = v.error || "That code didn't work — check it and try again."; return; }
        }
        const r = await completePasswordReset(mount.querySelector("#_p").value);
        if (!r.ok) { err.textContent = r.error; return; }
        history.replaceState(null, "", location.pathname);   // drop the hash
        const c = await current();
        admit(c, mount, onOk, roles);
      } finally { btn.disabled = false; }
    });
  }

  // ── admin API (admin only) ───────────────────────────────────────────────
  // These POST to the Pipedream worker with the admin's token; the worker
  // verifies admin, then uses the service key (server-side) to act on Supabase.
  // The service key never touches the browser.
  async function adminCall(pathSuffix, body) {
    const token = requireToken();
    const r = await fetch(`${WORKER_URL}/admin/${pathSuffix}`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }
  const listUsers  = () => adminCall("users", {}).then((j) => j.users || []);
  const inviteUser = (email, role, venue, employee) => adminCall("invite", { email, role, venue, employee });
  const setUserRole = (email, role, venue, employee) => adminCall("role", { email, role, venue, employee });

  return {
    login, signUp, forgotPassword, completePasswordReset, logout,
    current, requireToken, canWrite, hasRole, gate, KITCHEN_ROLES,
    configured, SUPABASE_URL,
    listUsers, inviteUser, setUserRole,
  };
})();
