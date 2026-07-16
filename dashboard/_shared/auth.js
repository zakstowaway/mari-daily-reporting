/**
 * Shared auth. One login, six roles, every app.
 *
 * Extracted from index.html 2026-07-17 because recipes.html had already copied
 * it and a prep timer would have been the fourth copy. Four copies of an auth
 * check is how one of them silently stops matching users.json.
 *
 * ── READ THIS BEFORE TRUSTING IT ─────────────────────────────────────────────
 * THIS IS NOT REAL AUTHENTICATION.
 *
 * users.json ships to the browser with the salt in it, and the comparison runs
 * in JS on the client. Anyone can fetch users.json, read the salt, and anyone
 * with devtools can set CURRENT_ROLE by hand. It keeps honest people out of a
 * dashboard on an obscure URL. That is all it does, and for a READ-ONLY
 * dashboard that is a reasonable trade.
 *
 * It is NOT sufficient for anything that WRITES. When the recipe UI starts
 * saving, the write must authenticate AT THE ENDPOINT (Pipedream holds the
 * secret) and Actions must re-validate server-side. The browser is a
 * suggestion, never an authority. See MODULES.md.
 * ─────────────────────────────────────────────────────────────────────────────
 */

export const Auth = (() => {
  let USERS = null;
  let CURRENT = null;

  const KEY = 'shg.session';

  async function sha256Hex(s) {
    const buf = new TextEncoder().encode(s);
    const digest = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  async function loadUsers(base = '') {
    if (USERS) return USERS;
    const r = await fetch(`${base}users.json?t=` + Date.now());
    if (!r.ok) throw new Error(`users.json ${r.status}`);
    USERS = await r.json();
    return USERS;
  }

  async function login(username, password, base = '') {
    await loadUsers(base);
    const u = (username || '').trim().toLowerCase();
    const user = USERS.users[u];
    // Same message for unknown user and bad password -- don't leak which.
    if (!user) return null;
    const hash = await sha256Hex(USERS.salt + password);
    if (hash !== user.hash) return null;
    CURRENT = { username: u, role: user.role, display: user.display };
    sessionStorage.setItem(KEY, JSON.stringify(CURRENT));
    return CURRENT;
  }

  function restore() {
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

  const current = () => CURRENT || restore();

  /** Roles that may see kitchen tooling (recipes, prep). From users.json. */
  const KITCHEN_ROLES = ['admin', 'bigchef', 'stowfood', 'hgfood', 'pizza'];

  function hasRole(...allowed) {
    const c = current();
    return !!c && allowed.includes(c.role);
  }

  /**
   * Gate a page. Renders a login card into `mount` and calls onOk when in.
   * Deliberately plain: this is a lock on a cupboard, not a vault.
   */
  async function gate(mount, { base = '', roles = null, onOk }) {
    const go = () => {
      const c = current();
      if (!c) return false;
      if (roles && !roles.includes(c.role)) {
        mount.innerHTML = `<div class="card"><b>No access</b><div class="muted">
          Signed in as ${c.display} (${c.role}). This page needs: ${roles.join(', ')}.
          </div></div>`;
        return true;
      }
      mount.style.display = 'none';
      onOk(c);
      return true;
    };
    if (go()) return;

    mount.innerHTML = `
      <div class="login-wrap"><div class="login-card">
        <form id="_shared-login">
          <label for="_u">Username</label><input id="_u" autocomplete="username" autofocus>
          <label for="_p">Password</label><input id="_p" type="password" autocomplete="current-password">
          <button type="submit">Sign In</button>
          <div class="err" id="_e"></div>
        </form>
      </div></div>`;
    mount.querySelector('#_shared-login').addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = mount.querySelector('#_e');
      err.textContent = '';
      try {
        const c = await Auth.login(mount.querySelector('#_u').value,
                                   mount.querySelector('#_p').value, base);
        if (!c) { err.textContent = 'Invalid credentials'; return; }
        go();
      } catch (ex) { err.textContent = String(ex); }
    });
  }

  return { login, logout, current, hasRole, gate, loadUsers, KITCHEN_ROLES, sha256Hex };
})();
