/**
 * Shared feed access. `data/` is the API (MODULES.md).
 *
 * Every app reads feeds; none of them should be reimplementing cache-busting
 * and "what if the file isn't there yet". index.html has nine hand-rolled
 * fetch() calls; recipes.html had a tenth.
 *
 * A feed is a published contract: a file in data/ with a documented shape,
 * changed additively only. Apps are deployed and reading these — renaming a
 * field breaks a live page and every stale browser tab.
 */

export const Feed = (() => {
  const cache = new Map();

  /**
   * Load a feed. Cache-busted (Pages caches aggressively and a stale feed is
   * a wrong number on a screen, which is worse than a slow one).
   *
   * A missing feed is NOT an exception — a pipeline may simply not have run
   * yet. Callers get `null` and decide. Throwing here would blank a dashboard
   * because one panel's job hasn't finished.
   */
  async function load(path, { base = '', fresh = false } = {}) {
    const key = base + path;
    if (!fresh && cache.has(key)) return cache.get(key);
    let out = null;
    try {
      const r = await fetch(`${base}${path}?t=` + Date.now());
      if (r.ok) {
        out = path.endsWith('.json') ? await r.json() : await r.text();
      } else if (r.status !== 404) {
        console.warn(`feed ${path}: HTTP ${r.status}`);
      }
    } catch (e) {
      console.warn(`feed ${path}: ${e}`);
    }
    cache.set(key, out);
    return out;
  }

  /** Load, or render a plain explanation instead of failing silently. */
  async function loadOrExplain(path, el, opts = {}) {
    const d = await load(path, opts);
    if (!d && el) {
      el.textContent = `${path} isn't available yet — its pipeline may not have run.`;
    }
    return d;
  }

  const csv = (text) => {
    if (!text) return [];
    const [head, ...rows] = text.trim().split('\n');
    const cols = head.split(',');
    return rows.map(r => {
      // good enough for our feeds: no embedded commas in quoted fields today.
      // If that ever changes, this is the line that will lie to you.
      const v = r.split(',');
      return Object.fromEntries(cols.map((c, i) => [c, v[i]]));
    });
  };

  return { load, loadOrExplain, csv, cache };
})();
