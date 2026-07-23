/* dashboard/_shared/data.js — async loaders that fetch feeds into STATE
   Classic <script src> loaded BEFORE the inline bootstrap, so every declaration
   here is a window global exactly as when it lived in index.html. Extracted from
   sales/index.html verbatim (byte-identical, proven by the arch guard). */

async function loadUsers() {
  const r = await fetch('users.json?t=' + Date.now());
  USERS = await r.json();
}

async function loadHistory(venue) {
  const cfg = VENUE_CONFIG[venue];
  if (!cfg.historyFile) return [];
  try {
    const res = await fetch(cfg.historyFile + '?t=' + Date.now());
    if (!res.ok) return [];
    const text = await res.text();
    return applyAssumed(stripOverhead(parseCsv(text)));
  } catch (e) { return []; }
}

async function loadAllHistories(role) {
  const roleCfg = ROLE_CONFIG[role] || ROLE_CONFIG.admin;
  const seesGroup = roleCfg.venues.includes('group');
  const realVenuesToLoad = seesGroup ? REAL_VENUES : roleCfg.venues.filter(v => VENUE_CONFIG[v] && !VENUE_CONFIG[v].synthetic);
  const results = await Promise.all(realVenuesToLoad.map(async v => [v, await loadHistory(v)]));
  const out = {};
  for (const [v, rows] of results) out[v] = rows;
  if (seesGroup) out.group = synthesizeGroupHistory(out);
  return out;
}

async function fetchLastUpdate() {
  const rows = STATE.histories[STATE.currentVenue] || [];
  if (!rows.length) { document.getElementById('last-update').textContent = 'unknown'; return; }
  const latest = rows[rows.length - 1];
  const cfg = VENUE_CONFIG[STATE.currentVenue];
  if (!cfg.dayFilePrefix) {
    const stamps = [];
    for (const v of REAL_VENUES) {
      const rrows = STATE.histories[v] || [];
      if (!rrows.length) continue;
      try {
        const r = await fetch(`${VENUE_CONFIG[v].dayFilePrefix}${rrows[rrows.length-1].date}.json?t=` + Date.now());
        const data = await r.json();
        stamps.push(new Date(data.generated_at).getTime());
      } catch (e) {}
    }
    if (stamps.length) document.getElementById('last-update').textContent = new Date(Math.max(...stamps)).toLocaleString('en-AU');
    else document.getElementById('last-update').textContent = 'unknown';
    return;
  }
  try {
    const r = await fetch(`${cfg.dayFilePrefix}${latest.date}.json?t=` + Date.now());
    const data = await r.json();
    document.getElementById('last-update').textContent = new Date(data.generated_at).toLocaleString('en-AU');
  } catch (e) {
    document.getElementById('last-update').textContent = 'unknown';
  }
}

async function loadHourlyRevenue(venue, date) {
  for (const src of (HOURLY_SOURCES[venue] || [])) {
    try {
      const r = await fetch(`/data/${src.prefix}_hourly_${date}.json?t=` + Date.now());
      if (!r.ok) continue;
      const map = extractByHour(await r.json(), src.key);
      if (map) return map;
    } catch (e) { /* try next source */ }
  }
  return null;
}

async function loadHourlyLabour(venue, date) {
  let shifts;
  try {
    const r = await fetch(`/data/deputy_${venue}_${date}.json?t=` + Date.now());
    if (!r.ok) return null;
    shifts = await r.json();
  } catch (e) { return null; }
  if (!Array.isArray(shifts) || !shifts.length) return null;
  const byHour = {};
  let totCost = 0, totHours = 0;
  for (const s of shifts) {
    const cost = toNum(s.cost);
    const st = toNum(s.start_time), en = toNum(s.end_time);
    totCost += cost;
    totHours += toNum(s.hours);
    if (!(en > st) || !cost) continue;
    const a = st + SYD_OFFSET_SEC, b = en + SYD_OFFSET_SEC;   // Sydney-local seconds
    const span = b - a;
    const h0 = Math.floor(a / 3600), h1 = Math.floor((b - 1) / 3600);
    for (let h = h0; h <= h1; h++) {
      const lo = Math.max(a, h * 3600), hi = Math.min(b, (h + 1) * 3600);
      const frac = (hi - lo) / span;
      if (frac <= 0) continue;
      const hod = ((h % 24) + 24) % 24;
      byHour[hod] = (byHour[hod] || 0) + cost * frac;
    }
  }
  return { byHour, avgRate: totHours > 0 ? totCost / totHours : null };
}
