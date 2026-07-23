/* dashboard/_shared/render.js — all DOM rendering + UI event handlers
   Classic <script src> loaded BEFORE the inline bootstrap, so every declaration
   here is a window global exactly as when it lived in index.html. Extracted from
   sales/index.html verbatim (byte-identical, proven by the arch guard). */

async function doLogin(evt) {
  evt.preventDefault();
  const u = document.getElementById('username').value.trim().toLowerCase();
  const p = document.getElementById('password').value;
  const errEl = document.getElementById('login-err');
  errEl.textContent = '';
  if (!USERS) await loadUsers();
  const user = USERS.users[u];
  if (!user) { errEl.textContent = 'Invalid credentials'; return false; }
  const hash = await sha256Hex(USERS.salt + p);
  if (hash !== user.hash) { errEl.textContent = 'Invalid credentials'; return false; }
  CURRENT_USER = { username: u, ...user };
  CURRENT_ROLE = user.role;
  sessionStorage.setItem('mari_session', JSON.stringify(CURRENT_USER));
  showDashboard();
  return false;
}

function logout() {
  (window.__shgAuth ? window.__shgAuth.logout() : Promise.resolve())
    .then(() => location.replace('/'));
}

async function showDashboard() {
  document.getElementById('login').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
  document.getElementById('who-name').textContent = CURRENT_USER.display;
  await bootstrap();
}

async function tryResume() {
  // Supabase is the auth now (one login, at /). No session -> go sign in there.
  const u = await window.__shgSession;
  if (!u) { location.replace('/'); return; }
  CURRENT_USER = { username: u.email, display: u.name || u.email, role: u.role };
  CURRENT_ROLE = u.role;
  showDashboard();
}

function renderVenueTabs() {
  const roleCfg = ROLE_CONFIG[CURRENT_ROLE] || ROLE_CONFIG.admin;
  const tabsEl = document.getElementById('venue-tabs');
  const inner = document.getElementById('venue-tabs-inner');
  tabsEl.classList.remove('hidden');
  if (roleCfg.venues.length <= 1) { inner.innerHTML = ''; return; }
  inner.innerHTML = roleCfg.venues.map(v => {
    const cfg = VENUE_CONFIG[v];
    const active = v === STATE.currentVenue ? `active ${cfg.chipClass}` : '';
    return `<button class="venue-tab ${active}" onclick="switchVenue('${v}')">${cfg.label}</button>`;
  }).join('');
  const toolNav = document.getElementById('tool-nav');
  if (toolNav) toolNav.innerHTML = CURRENT_ROLE === 'admin'
    ? `<a href="rg.html">Menu trends</a><a href="eatclub.html">EatClub</a><a href="/recipes/">Recipes</a><a href="/admin/">Team</a>`
    : `<a href="eatclub.html">EatClub</a>`;   // EatClub is open to all roles
}

function renderVenueStrip() {
  const el = document.getElementById('venue-strip');
  const cfg = VENUE_CONFIG[STATE.currentVenue];
  el.className = 'venue-strip ' + cfg.chipClass;
}

function renderTimeframeToggle() {
  const el = document.getElementById('timeframe-toggle');
  if (!el) return;
  const tf = STATE.currentTimeframe;
  const todayIso = isoDate(sydneyToday());
  const btn = (t, lbl) => `<button class="tf-btn${tf === t ? ' active' : ''}" onclick="switchTimeframe('${t}')">${lbl}</button>`;
  // Day scrubber: Mon->Sun of the CURRENT week. Clicking a day selects that date
  // (day view); future days are muted and not clickable. Restored 2026-07-23 —
  // the week's day buttons are the quickest way to flick day-to-day. The whole
  // toggle wraps to its own line when the venue row runs out of width, so the
  // scrubber never overflows. A day outside this week comes from "Pick a day..."
  const wkStart = weekStart(sydneyToday());
  const DOW = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  let days = '';
  for (let i = 0; i < 7; i++) {
    const d = addDays(wkStart, i);
    const iso = isoDate(d);
    const future = iso > todayIso;
    const on = tf === 'day' && STATE.currentDay === iso;
    days += `<button class="tf-day${on ? ' active' : ''}${future ? ' future' : ''}" ` +
      (future ? 'disabled' : `onclick="switchDay('${iso}')"`) +
      ` title="${iso}">${DOW[i]}<span class="tf-dnum">${d.getDate()}</span></button>`;
  }
  const moreOpts = [['pickday', 'Pick a day…'], ['lastmonth', 'Last month'], ['quarter', 'This quarter'],
    ['lastquarter', 'Last quarter'], ['thisfy', 'This FY'], ['lastfy', 'Last FY'],
    ['last12', 'Last 12 months'], ['all', 'All time'], ['range', 'Custom range…']];
  // A past day outside this week still shows as "Day ▾" so the user can see a
  // day (not week) is selected even when no scrubber pill is lit.
  const dayOutside = tf === 'day' && STATE.currentDay && (STATE.currentDay < isoDate(wkStart) || STATE.currentDay > todayIso);
  const moreOn = dayOutside || tf === 'range' || moreOpts.some(o => o[0] === tf);
  const moreSel = `<select id="tf-more" class="${moreOn ? 'active' : ''}" onchange="tfMore(this.value)">` +
    `<option value="">${dayOutside ? 'Day ▾' : 'More ▾'}</option>` +
    moreOpts.map(o => `<option value="${o[0]}"${tf === o[0] ? ' selected' : ''}>${o[1]}</option>`).join('') +
    `</select>`;
  el.innerHTML = `<span class="tf-days">${days}</span><span class="tf-sep"></span>` +
    btn('week', 'This week') + btn('lastweek', 'Last week') + btn('month', 'This month') + moreSel;
}

function tfMore(v) {
  if (!v) return;
  if (v === 'pickday') switchDay(STATE.currentDay || isoDate(sydneyToday()));
  else switchTimeframe(v);
}

function renderRangePicker() {
  const el = document.getElementById('range-picker');
  const show = STATE.currentTimeframe === 'range';
  el.style.display = show ? 'flex' : 'none';
  if (!show) return;
  const rows = STATE.histories[STATE.currentVenue] || [];
  const minD = rows.length ? rows[0].date : '';
  const maxD = rows.length ? rows[rows.length - 1].date : '';
  const si = document.getElementById('range-start'), ei = document.getElementById('range-end');
  si.min = minD; si.max = maxD; ei.min = minD; ei.max = maxD;
  if (!si.value) si.value = STATE.rangeStart || (rows.length ? rows[Math.max(0, rows.length - 28)].date : '');
  if (!ei.value) ei.value = STATE.rangeEnd || maxD;
}

function applyRange() {
  const si = document.getElementById('range-start'), ei = document.getElementById('range-end');
  if (!si.value || !ei.value) return;
  STATE.rangeStart = si.value <= ei.value ? si.value : ei.value;
  STATE.rangeEnd   = si.value <= ei.value ? ei.value : si.value;
  render();
}

function renderDayPicker() {
  const picker = document.getElementById('day-picker');
  if (picker) {
    if (STATE.currentTimeframe === 'day') {
      const rows = STATE.histories[STATE.currentVenue] || [];
      const minD = rows.length ? rows[0].date : '';
      const maxD = isoDate(sydneyToday());
      picker.style.display = 'flex';
      picker.innerHTML = `<span class="dp-lbl">Day</span>` +
        `<input type="date" id="day-pick-input" ${minD ? `min="${minD}" ` : ''}max="${maxD}" ` +
        `value="${STATE.currentDay || maxD}" onchange="if(this.value)switchDay(this.value)">`;
    } else { picker.style.display = 'none'; picker.innerHTML = ''; }
  }
  renderRangePicker();
}

function renderSnapshot() {
  const venueCfg = VENUE_CONFIG[STATE.currentVenue];
  const roleCfg = ROLE_CONFIG[CURRENT_ROLE] || ROLE_CONFIG.admin;
  const cards = cardsForCurrentView();
  const cfg = { show_dollar_amounts: roleCfg.show_dollar_amounts };
  const rows = STATE.histories[STATE.currentVenue] || [];
  let anchorDay = STATE.currentDay;
  if (!anchorDay && rows.length) anchorDay = rows[rows.length - 1].date;
  const rangeRows = rowsForTimeframe(rows, STATE.currentTimeframe, anchorDay);
  const day = rollup(rangeRows);
  const titleEl = document.getElementById('snapshot-title');
  const dateEl  = document.getElementById('latest-date');
  const staleEl = document.getElementById('stale-badge');
  const tfLabel = { day: 'Selected day', week: 'This Week', lastweek: 'Last Week', month: 'This Month', lastmonth: 'Last Month', quarter: 'This Quarter', lastquarter: 'Last Quarter', thisfy: 'This FY', lastfy: 'Last FY', last12: 'Last 12 months', all: 'All time', range: 'Custom range' }[STATE.currentTimeframe];
  titleEl.textContent = `${venueCfg.label} · ${tfLabel}`;
  const snapshotEl = document.getElementById('snapshot');
  snapshotEl.className = 'grid' + (cards.length > 4 ? ' six' : '');
  // Empty card list (admin) — collapse the grid entirely so no empty bordered
  // box or leftover bottom margin sits between the profit card and the trend.
  snapshotEl.style.display = cards.length ? '' : 'none';
  if (!day) {
    dateEl.textContent = 'No data';
    staleEl.innerHTML = '';
    // No-data state respects the split card layout so the venue tab shows
    // exactly what it'll look like once the venue's Insights schedule fires.
    const ghostCards = cards.map(key => {
      const c = CARD_DEFS[key]({}, cfg);
      return `<div class="card unknown"><p class="label">${c.label}</p><p class="value">—</p><p class="sub">no data yet</p></div>`;
    }).join('');
    snapshotEl.innerHTML = ghostCards;
    const pcEl0 = document.getElementById('profit-card'); if (pcEl0) pcEl0.innerHTML = '';
    document.getElementById('dollar-strip').innerHTML = '';
    document.getElementById('split-snapshot').innerHTML = '';
    const waEl0 = document.getElementById('week-ahead'); if (waEl0) waEl0.innerHTML = '';
    const dhEl0 = document.getElementById('data-health'); if (dhEl0) dhEl0.innerHTML = '';
    const vbEl0 = document.getElementById('venue-breakdown'); if (vbEl0) vbEl0.innerHTML = '';
    const vEl0 = document.getElementById('verdict'); if (vEl0) vEl0.innerHTML = '';
    document.getElementById('trend-section').classList.add('hidden');
    return;
  }
  document.getElementById('trend-section').classList.remove('hidden');
  if (STATE.currentTimeframe === 'day') {
    dateEl.textContent = new Date(day.date).toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' });
  } else {
    // A one-day range collapses to a single raw row (rollup returns it as-is),
    // so day.date has no " — " and no days_included — handle that, else the
    // label read "Invalid Date (undefined days)".
    const [s, e] = String(day.date).includes(' — ') ? day.date.split(' — ') : [day.date, day.date];
    const fmt = d => new Date(d).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
    const nDays = day.days_included || 1;
    dateEl.textContent = s === e ? `${fmt(s)} (1 day)` : `${fmt(s)} — ${fmt(e)} (${nDays} days)`;
  }
  staleEl.innerHTML = '';
  if (STATE.currentTimeframe === 'day') {
    // Staleness = how far the LATEST data is behind — never triggered by
    // deliberately browsing to an older day with the day pills.
    const latest = rows.length ? rows[rows.length - 1].date : null;
    const behind = latest ? Math.floor((sydneyToday() - new Date(latest)) / 86400000) - 1 : 0;
    if (behind > 0) staleEl.innerHTML = `<span class="stale">Latest data ${behind}d behind</span>`;
  }
  // Verdict line retired in the redesign — the profit hero block carries the
  // "did we make money?" answer now. Keep the container clear.
  const vEl = document.getElementById('verdict');
  if (vEl) vEl.innerHTML = '';
  const cardEls = cards.map(key => ({ key, ...CARD_DEFS[key](day, cfg) }));
  const clickable = CURRENT_ROLE === 'admin';
  // 30-day sparklines for the metrics with honest daily series
  const spark = {};
  if (CURRENT_ROLE === 'admin') {
    const last30 = rows.slice(-30);
    spark.revenue = last30.map(r => toNum(r.revenue_ex_gst) || null);
    spark.wages_hero = last30.map(r => hasVal(r.wages_pct) && toNum(r.revenue_ex_gst) ? toNum(r.wages_pct) : null);
    spark.cogs_merged = last30.map(r => toNum(r.revenue_ex_gst) ? toNum(r.cogs_pct) : null);
  }
  snapshotEl.innerHTML = cardEls.map(c =>
    `<div class="card ${c.status || 'unknown'}${clickable ? ' clickable' : ''}${STATE.focusMetric === c.key ? ' focused' : ''}"` +
    (clickable ? ` onclick="focusMetric('${c.key}')" title="Click to focus the chart on this metric"` : '') +
    `>${spark[c.key] ? sparkline(spark[c.key]) : ''}<p class="label">${c.label}</p><p class="value">${c.value}</p>${c.vs ? `<p class="vs">${c.vs}</p>` : ''}<p class="sub">${c.sub || ''}</p></div>`).join('');
  renderExtras(day, cfg);
}

function renderExtras(day, cfg) {
  // De-noised in the redesign: the dollar strip now lives inside the profit
  // card, and the verdict / kitchen-FOH split / week-ahead / data-health blocks
  // are retired (their render functions stay defined but are no longer called).
  document.getElementById('dollar-strip').innerHTML = '';
  document.getElementById('split-snapshot').innerHTML = '';
  const waEl = document.getElementById('week-ahead'); if (waEl) waEl.innerHTML = '';
  const dhEl = document.getElementById('data-health'); if (dhEl) dhEl.innerHTML = '';
  const vbEl = document.getElementById('venue-breakdown'); if (vbEl) vbEl.innerHTML = '';
  const pcEl = document.getElementById('profit-card'); if (pcEl) pcEl.innerHTML = '';
  if (!day) return;
  renderProfitCard(day, cfg);
  const srEl = document.getElementById('story-row');
  if (CURRENT_ROLE !== 'admin') { if (srEl) srEl.style.gridTemplateColumns = '1fr'; return; }
  renderVenueBreakdown();   // kept: the group -> venue table
  if (srEl) srEl.style.gridTemplateColumns = '1fr';
}

function renderProfitCard(day, cfg) {
  const el = document.getElementById('profit-card');
  if (!el) return;
  el.innerHTML = '';
  if (CURRENT_ROLE !== 'admin' || !day) return;
  const fmtP = x => (x < 0 ? '−$' : '$') + Math.round(Math.abs(x)).toLocaleString();

  // DAILY = instant operational read: turnover, COGS, labour only. Fixed costs
  // (overheads, rent, owners' salary, interest, depreciation) and the profit line
  // are deliberately left off the day view — rent is paid whether or not we trade,
  // so folding it into a single day paints a false picture. Those live on the
  // weekly view. (Zak, 2026-07: "daily KPIs re wages, turnover and COGS are the
  // most important — instant reflection, then adjust labour.")
  if (periodIsOpen()) {
    const kpiLbl = STATE.currentTimeframe === 'day' ? 'Daily KPIs'
      : STATE.currentTimeframe === 'week' ? 'This week — KPIs'
      : STATE.currentTimeframe === 'month' ? 'This month — KPIs'
      : 'KPIs — period in progress';
    const w = pnlWindow(day);
    if (!w) {
      el.innerHTML = `<div class="pcard"><div class="pcard-head"><span class="pcard-lbl">${kpiLbl}</span></div>` +
        `<div class="pcard-hero" style="color:var(--ink-soft);font-size:22px">—</div>` +
        `<div class="pbrk-row"><span class="pbrk-l" style="font-size:12px;color:var(--ink-soft)">needs revenue + wages</span></div></div>`;
      return;
    }
    const eat = toNum(day.eatclub_giveaway_ex_gst);
    const gross = w.rev + eat;
    const pct = x => gross ? x / gross * 100 : 0;
    const cogsD = w.rev * w.cogsPct / 100;
    const cogsPctG = pct(cogsD), wagesPctG = pct(w.wages);
    const wTgt = wageTargetFor(day);
    const grpDisp = grossRev(day);
    const rows = STATE.histories[STATE.currentVenue] || [];
    let anchorDay = STATE.currentDay; if (!anchorDay && rows.length) anchorDay = rows[rows.length - 1].date;
    const yy = yoyRevenueDelta(rows, STATE.currentTimeframe, anchorDay);
    const revSub = yy !== null
      ? `<span class="${yy >= 0 ? 'vs-good' : 'vs-bad'}">${(yy >= 0 ? '+' : '−') + Math.abs(yy).toFixed(1)}%</span><span class="vs-t">vs LY</span>`
      : '';
    const oc = overheadChips();   // group-only admin + leave toggles (sit in the card head)
    const wagesLbl = 'Labour' + (STATE.currentVenue === 'group' ? ' (excl. admin)' : '');
    // Marilyna's is delivery-led, so platform/delivery cost is a headline KPI here
    // (Uber Direct + Uber Eats commission + marketing). w.df is the same delivery
    // dollars used in the full P&L, so the KPI and the profit line never disagree.
    const showDelivery = STATE.currentVenue === 'mari';
    const deliveryCard = showDelivery
      ? `<div class="pmini-c"><p class="pmini-l">Delivery cost</p><p class="pmini-v">${fmtPct(pct(w.df))}</p><p class="pmini-s">${fmtDollars(w.df)} of turnover</p></div>`
      : '';
    const gridStyle = showDelivery ? ' style="grid-template-columns:repeat(4,1fr)"' : '';
    el.innerHTML =
      `<div class="pcard">` +
      `<div class="pcard-head"><span class="pcard-lbl">${kpiLbl}</span>${oc.chips}</div>` +
      oc.note +
      `<div class="pmini"${gridStyle}>` +
      `<div class="pmini-c"><p class="pmini-l">Turnover (gross)</p><p class="pmini-v">${fmtDollars(grpDisp)}</p><p class="pmini-s"></p></div>` +
      `<div class="pmini-c"><p class="pmini-l">COGS</p><p class="pmini-v">${fmtPct(cogsPctG)}</p><p class="pmini-s">${fmtDollars(cogsD)} · ${vsTarget(cogsPctG, COGS_TARGET_PCT)}</p></div>` +
      `<div class="pmini-c"><p class="pmini-l">${wagesLbl}</p><p class="pmini-v">${fmtPct(wagesPctG)}</p><p class="pmini-s">${fmtDollars(w.wages)}${wTgt !== null ? ' · ' + vsTarget(wagesPctG, wTgt) : ''}</p></div>` +
      deliveryCard +
      `</div></div>`;
    return;
  }

  const ap = actualProfitWindow(day);
  if (ap) {
    // Closed period — actuals from Xero. Kept working, lightly restyled.
    const w0 = pnlWindow(day);
    const dlt = w0 ? ap.profit - w0.profit : null;
    const margin = ap.rev ? ap.profit / ap.rev * 100 : 0;
    const bits = [`COS ${fmtPct(ap.cos / ap.rev * 100)}`, `payroll ${fmtPct(ap.wages / ap.rev * 100)}`, `OH ${fmtDollars(ap.oh)}`];
    if (ap.df) bits.push(`platform fees ${fmtDollars(ap.df)}`);
    if (ap.fin) bits.push(`interest & dep ${fmtDollars(ap.fin)}`);
    if (dlt !== null) bits.push(`expected said ${fmtP(w0.profit)} (Δ ${fmtP(dlt)})`);
    bits.push(`${ap.nMonths} closed month${ap.nMonths > 1 ? 's' : ''}, stock journals posted`);
    const oc = overheadChips();
    el.innerHTML =
      `<div class="pcard${ap.profit >= 0 ? '' : ' neg'}">` +
      `<div class="pcard-head"><span class="pcard-lbl">Actual profit</span> ${pill('closed · Xero', 'green')}${oc.chips}</div>` +
      `<div class="pcard-hero${ap.profit >= 0 ? '' : ' neg'}">${fmtP(ap.profit)}<span class="pcard-margin"> · ${fmtPct(margin)} margin</span></div>` +
      oc.note +
      `<div class="pbrk"><div class="pbrk-row"><span class="pbrk-l" style="font-size:12px;color:var(--ink-soft)">${bits.join(' · ')}</span></div></div>` +
      `</div>`;
    return;
  }

  const w = pnlWindow(day);
  if (!w) {
    el.innerHTML = `<div class="pcard"><div class="pcard-head"><span class="pcard-lbl">Expected profit</span></div>` +
      `<div class="pcard-hero" style="color:var(--ink-soft);font-size:22px">—</div>` +
      `<div class="pbrk-row"><span class="pbrk-l" style="font-size:12px;color:var(--ink-soft)">needs revenue + wages</span></div></div>`;
    return;
  }

  const eat = toNum(day.eatclub_giveaway_ex_gst);
  const covers = toNum(day.eatclub_covers);
  const cogsD = w.rev * w.cogsPct / 100;
  // Gross base for this window. gross = net + give-away, so subtracting the
  // give-away as a cost leaves the profit $ EXACTLY equal to the net-based
  // value: (rev + eat) - cogs - wages - oh - df - cp - eat - fin = w.profit.
  const gross = w.rev + eat;
  const pct = x => gross ? x / gross * 100 : 0;
  const margin = pct(w.profit);
  const wTgt = wageTargetFor(day);
  const cogsPctG = pct(cogsD), wagesPctG = pct(w.wages);
  const grpDisp = grossRev(day);   // full-window gross for the headline revenue card

  // --- small cards: Revenue (gross) / COGS % vs tgt / Wages % vs tgt ---
  const rows = STATE.histories[STATE.currentVenue] || [];
  let anchorDay = STATE.currentDay;
  if (!anchorDay && rows.length) anchorDay = rows[rows.length - 1].date;
  const yy = yoyRevenueDelta(rows, STATE.currentTimeframe, anchorDay);
  const revSub = yy !== null
    ? `<span class="${yy >= 0 ? 'vs-good' : 'vs-bad'}">${(yy >= 0 ? '+' : '−') + Math.abs(yy).toFixed(1)}%</span><span class="vs-t">vs LY</span>`
    : '';
  const wagesLbl = 'Wages' + (STATE.currentVenue === 'group' ? ' (excl. admin)' : '');
  // Delivery cost is a headline KPI for Marilyna's (delivery-led venue).
  const showDeliveryM = STATE.currentVenue === 'mari';
  const deliveryCardM = showDeliveryM
    ? `<div class="pmini-c"><p class="pmini-l">Delivery cost</p><p class="pmini-v">${fmtPct(pct(w.df))}</p><p class="pmini-s">${fmtDollars(w.df)} of turnover</p></div>`
    : '';
  const gridStyleM = showDeliveryM ? ' style="grid-template-columns:repeat(4,1fr)"' : '';
  const mini =
    `<div class="pmini"${gridStyleM}>` +
    `<div class="pmini-c"><p class="pmini-l">Revenue (gross)</p><p class="pmini-v">${fmtDollars(grpDisp)}</p><p class="pmini-s">${revSub}</p></div>` +
    `<div class="pmini-c"><p class="pmini-l">COGS</p><p class="pmini-v">${fmtPct(cogsPctG)}</p><p class="pmini-s">${vsTarget(cogsPctG, COGS_TARGET_PCT)}</p></div>` +
    `<div class="pmini-c"><p class="pmini-l">${wagesLbl}</p><p class="pmini-v">${fmtPct(wagesPctG)}</p><p class="pmini-s">${wTgt !== null ? vsTarget(wagesPctG, wTgt) : ''}</p></div>` +
    deliveryCardM +
    `</div>`;

  // --- 100%-of-gross stacked bar (reuses the old dollar-strip logic) ---
  const barDefs = [
    ['COGS', cogsD, '#D85A30'],
    ['Wages', w.wages, '#7F77DD'],
    ['Delivery', w.df, '#EF9F27'],
    ['EatClub give-away', eat, '#C86FA0'],
    ["Owners' salary", w.cp, '#D4537E'],
    ['Overheads', w.oh, '#888780'],
    ['Interest & dep', w.fin, '#5A5A70'],
  ].map(s => [s[0], pct(s[1]), s[2]]).filter(s => s[1] > 0.05);
  const profitPct = 100 - barDefs.reduce((t, s) => t + s[1], 0);
  const barSegs = barDefs.concat([['Expected profit', profitPct, profitPct >= 0 ? '#1D9E75' : '#E24B4A']]);
  const bar = '<div class="pcard-bar"><div class="strip-bar">' +
    barSegs.map(s => `<div style="width:${Math.max(0, Math.min(100, s[1])).toFixed(2)}%;background:${s[2]};" title="${s[0]} ${s[1].toFixed(1)}%"></div>`).join('') +
    '</div></div>';

  // --- breakdown list: each cost as a % of gross, with its target reference ---
  const refCol = st => st === 'red' ? 'var(--red)' : st === 'green' ? 'var(--green)' : 'var(--amber)';
  const line = (color, label, p, dollars, ref) =>
    `<div class="pbrk-row"><span class="pbrk-dot" style="background:${color}"></span>` +
    `<span class="pbrk-l">${label}</span><span class="pbrk-p">${fmtPct(p)}</span>` +
    `<span class="pbrk-d">${fmtP(dollars)}</span><span class="pbrk-r">${ref || ''}</span></div>`;
  const cogsRef = `<span style="color:${refCol(cogsStatus(cogsPctG))}">tgt ${fmtPct(COGS_TARGET_PCT)}</span>`;
  const wageSt = wTgt === null ? 'amber' : wagesPctG > wTgt + 2 ? 'red' : wagesPctG < wTgt - 2 ? 'green' : 'amber';
  const wageRef = wTgt !== null ? `<span style="color:${refCol(wageSt)}">tgt ${fmtPct(wTgt)}</span>` : '';
  let list = '';
  list += line('#D85A30', 'COGS', cogsPctG, cogsD, cogsRef);
  list += line('#7F77DD', wagesLbl, wagesPctG, w.wages, wageRef);
  list += line('#888780', 'Overheads', pct(w.oh), w.oh, '');
  list += line('#EF9F27', 'Delivery platforms', pct(w.df), w.df, '');
  if (eat > 0.005) list += line('#C86FA0', 'EatClub give-away', pct(eat), eat, `· ${covers} cover${covers === 1 ? '' : 's'}`);
  if (w.cp > 0.005) list += line('#D4537E', "Owners' salary", pct(w.cp), w.cp, '');
  if (w.fin > 0.005) list += line('#5A5A70', 'Interest & dep', pct(w.fin), w.fin, '');
  list +=
    `<div class="pbrk-row total"><span class="pbrk-dot" style="background:${profitPct >= 0 ? '#1D9E75' : '#E24B4A'}"></span>` +
    `<span class="pbrk-l">Expected profit</span><span class="pbrk-p">${fmtPct(margin)}</span>` +
    `<span class="pbrk-d">${fmtP(w.profit)}</span><span class="pbrk-r"></span></div>`;

  const oc = overheadChips();
  el.innerHTML =
    `<div class="pcard${w.profit >= 0 ? '' : ' neg'}">` +
    `<div class="pcard-head"><span class="pcard-lbl">Expected profit</span> ${pill('open period', 'warn')}` +
    (w.partial ? ` <span style="font-size:11px;color:var(--ink-soft)">${w.wdays} of ${w.daysAll} days costed</span>` : '') + oc.chips + `</div>` +
    `<div class="pcard-hero${w.profit >= 0 ? '' : ' neg'}">${fmtP(w.profit)}<span class="pcard-margin"> · ${fmtPct(margin)} margin</span></div>` +
    oc.note +
    mini + bar + `<div class="pbrk">${list}</div>` +
    `</div>`;
}

function renderWeekAhead() {
  const el = document.getElementById('week-ahead');
  if (!el || !STATE.roster || !STATE.roster.days) return;
  const v = STATE.currentVenue;
  const todayIso = isoDate(sydneyToday());
  // Dept-scoped roles see THEIR department's forecast, not the venue's:
  // chef roles get Kitchen, bar gets FOH — judged against that dept's slice
  // of the DOW-shaped target. Admin + pizza see the whole venue.
  const ROLE_DEPT = { stowfood: 'Kitchen', hgfood: 'Kitchen', bigchef: 'Kitchen', bar: 'FOH' };
  const deptOnly = ROLE_DEPT[CURRENT_ROLE] || null;

  const thisMon = weekStart(sydneyToday());
  const blocks = [];
  for (let w = 0; w < 2; w++) {
    const b = renderWaWeek(addDays(thisMon, w * 7), w, v, deptOnly, todayIso);
    if (b) blocks.push(b);
  }
  if (!blocks.length) return;
  const gen = STATE.roster.generated ? new Date(STATE.roster.generated) : null;
  el.innerHTML = `<p class="strip-label">Payroll weeks — ${deptOnly ? deptOnly.toLowerCase() + ' ' : ''}roster vs target for the WEEK (elapsed days are actuals; the rest you can still change)</p>`
    + blocks.join('')
    + `<p style="font-size:11px;opacity:.55;margin:10px 0 0;">Deputy roster incl 12% super, salaried synthesized · typical rev = median of last 8 same weekdays · target 30% weekly, shaped by weekday (quiet days carry prep, so their day-target runs higher) and split across depts by that weekday's own wage mix · admin wages count at group level only${gen ? ` · roster as at ${gen.toLocaleString('en-AU', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}` : ''}</p>`;
}

function renderVenueBreakdown() {
  const el = document.getElementById('venue-breakdown');
  if (!el) return;
  el.innerHTML = '';
  if (STATE.currentVenue !== 'group') return;
  const open = periodIsOpen();
  const showProfit = !open;   // open period (this week / today) = KPIs only, no profit/margin
  const showLY = !open;       // vs-LY on an in-progress period is misleading (partial vs full)
  const body = [['stow', 'Stowaway'], ['hg', 'Harry Gatos'], ['mari', "Marilyna's"]].map(([vk, lbl]) => {
    const rows = STATE.histories[vk] || [];
    let anchorDay = STATE.currentDay;
    if (!anchorDay && rows.length) anchorDay = rows[rows.length - 1].date;
    const day = rollup(rowsForTimeframe(rows, STATE.currentTimeframe, anchorDay));
    if (!day) return '';
    const rev = grossRev(day);   // gross, to match the rest of the redesign
    const yoy = yoyRevenueDelta(rows, STATE.currentTimeframe, anchorDay);
    const w = pnlWindow(day, vk);
    const a = actualCogs(vk, winEnd(day));
    const yoyCell = yoy !== null
      ? `<span class="${yoy >= 0 ? 'vb-pos' : 'vb-neg'}">${(yoy >= 0 ? '+' : '−') + Math.abs(yoy).toFixed(1)}%</span>` : '—';
    const profitCell = w
      ? `<span class="${w.profit >= 0 ? 'vb-pos' : 'vb-neg'}">${(w.profit < 0 ? '−$' : '$') + Math.round(Math.abs(w.profit)).toLocaleString()}</span>` : '—';
    const marginPct = w && w.rev ? w.profit / w.rev * 100 : null;
    const marginCell = marginPct !== null
      ? `<span class="${marginPct >= 0 ? 'vb-pos' : 'vb-neg'}">${fmtPct(marginPct)}</span>` : '—';
    return `<tr class="vbx-row ${vk}" onclick="switchVenue('${vk}')">
      <td class="vbx-venue"><span class="vbx-dot"></span>${lbl}</td>
      <td>${fmtDollars(rev)}</td>
      ${showLY ? '<td>' + yoyCell + '</td>' : ''}
      <td>${hasVal(day.wages_pct) ? fmtPct(toNum(day.wages_pct)) : '—'}</td>
      <td>${a ? fmtPct(a.pct) : hasVal(day.cogs_pct) ? fmtPct(toNum(day.cogs_pct)) : '—'}</td>
      ${showProfit ? `<td>${profitCell}</td><td>${marginCell}</td>` : ''}
    </tr>`;
  }).join('');
  if (!body) return;
  el.innerHTML = `<div class="vbx">
    <p class="vbx-title">Per-venue breakdown</p>
    <div class="vbx-scroll"><table class="vb-table vbx-table">
    <thead><tr><th>Venue</th><th>Revenue</th>${showLY ? '<th>vs LY</th>' : ''}<th>Wages</th><th>COGS</th>${showProfit ? '<th>Exp. profit</th><th>Margin</th>' : ''}</tr></thead>
    <tbody>${body}</tbody></table></div></div>`;
}

function renderDataHealth() {
  const el = document.getElementById('data-health');
  if (!el) return;
  dhChip._cls = [];
  dhChip._items = [];
  const chips = [];
  const y = isoDate(addDays(sydneyToday(), -1));
  for (const [pk, lbl] of [['stow', 'Stow'], ['hg', 'HG'], ['mari', 'Mari']]) {
    const r = (STATE.histories[pk] || []).find(x => x.date === y);
    // Closed-day allowance: if this venue's typical take for that weekday is
    // ~nothing (HG is shut Tuesdays), an empty day is normal, not a failure.
    const typ = dowForecast(pk, new Date(y).getDay());
    if ((!r || !toNum(r.revenue_ex_gst)) && typ < 300) chips.push(dhChip(lbl, 'ok', 'closed ' + new Date(y).toLocaleDateString('en-AU', { weekday: 'long' }) + 's'));
    else if (!r || !toNum(r.revenue_ex_gst)) chips.push(dhChip(lbl, 'bad', 'no sales for yesterday yet'));
    else if (!hasVal(r.wages_dollars)) chips.push(dhChip(lbl, 'warn', 'sales in · wages pending approval (12:10pm re-pull)'));
    else chips.push(dhChip(lbl, 'ok', 'yesterday complete'));
  }
  const wks = [...new Set(STATE.xeroCogs.map(r => r.week_ending))].sort();
  if (wks.length) {
    const age = Math.floor((sydneyToday() - new Date(wks[wks.length - 1])) / 86400000);
    chips.push(dhChip('Xero purchases', age <= 13 ? 'ok' : 'warn', `to w/e ${wks[wks.length - 1]}` + (age > 13 ? ' — stale, check the Monday pull' : '')));
  } else chips.push(dhChip('Xero purchases', 'bad', 'feed missing'));
  const closed = closedMonthsSet();
  const curMonth = isoDate(sydneyToday()).slice(0, 7);
  const mos = STATE.xeroOH.map(r => r.month).filter(m => m && m < curMonth).sort();
  const missing = mos.filter(m => !closed.has(m));
  if (missing.length) chips.push(dhChip('Stock journals', 'warn', 'missing ' + missing.map(m => new Date(m + '-01').toLocaleDateString('en-AU', { month: 'short' })).join(', ') + ' — chase accounts; Actual Profit unlocks when posted'));
  else if (mos.length) chips.push(dhChip('Stock journals', 'ok', 'posted through ' + new Date(mos[mos.length - 1] + '-01').toLocaleDateString('en-AU', { month: 'short' })));
  if (STATE.roster && STATE.roster.generated) {
    const ageH = (Date.now() - new Date(STATE.roster.generated).getTime()) / 3600000;
    chips.push(dhChip('Roster feed', ageH < 26 ? 'ok' : 'warn', ageH < 26 ? 'fresh' : Math.round(ageH) + 'h old'));
  } else chips.push(dhChip('Roster feed', 'warn', 'not pulled yet — first run 6:45am'));
  const bad = dhChip._items.filter(i => i.cls !== 'ok');
  const dot = bad.some(i => i.cls === 'bad') ? '#E24B4A' : bad.length ? '#EF9F27' : '#1D9E75';
  const msg = bad.length
    ? bad.map(i => `<strong>${i.lbl}</strong> ${i.detail}`).join(' · ') + ' — everything else healthy'
    : 'All pipelines healthy — sales, wages, Xero purchases, stock journals, roster';
  el.innerHTML = `<p class="dh-line"><span class="dh-dot" style="background:${dot};"></span><span>${msg}</span></p>`;
}

function renderChart() {
  const venueCfg = VENUE_CONFIG[STATE.currentVenue];
  const roleCfg = ROLE_CONFIG[CURRENT_ROLE] || ROLE_CONFIG.admin;
  const lines = ['revenue', ...(roleCfg.chart_lines || venueCfg.chart_lines)];
  const rows = STATE.histories[STATE.currentVenue] || [];
  let trailing, chartTitle;
  if (STATE.currentTimeframe === 'day') {
    trailing = rows.slice(-30);
    chartTitle = '30-Day Trailing';
  } else {
    let anchorDay = STATE.currentDay;
    if (!anchorDay && rows.length) anchorDay = rows[rows.length - 1].date;
    trailing = rowsForTimeframe(rows, STATE.currentTimeframe, anchorDay);
    if (trailing.length < 2) trailing = rows.slice(-30);
    chartTitle = 'Selected Period — Daily';
  }
  const titleEl = document.getElementById('trend-title');
  if (titleEl) titleEl.textContent = chartTitle;
  const ytEl = document.getElementById('year-toggles');
  if (ytEl && !(CURRENT_ROLE === 'admin' && STATE.focusMetric)) ytEl.style.display = 'none';
  const tickColor = IS_DARK ? '#B7B1A4' : '#5A544C';
  const gridColor = IS_DARK ? 'rgba(120,110,90,0.2)' : 'rgba(232,223,207,0.4)';
  const ctx = document.getElementById('trend-chart').getContext('2d');
  if (CHART_INSTANCE) CHART_INSTANCE.destroy();
  if (CURRENT_ROLE === 'admin') {
    const endIso = trailing.length ? trailing[trailing.length - 1].date : isoDate(sydneyToday());
    const dfr = deliveryFeesPct(STATE.currentVenue, endIso);
    const ohA = overheadsDailyRate(STATE.currentVenue, endIso);
    const ohDaily = ohA ? ohA.rate : (OVERHEADS_WEEKLY[STATE.currentVenue] || 0) / 7;
    const cpDaily = corpPayrollDaily(STATE.currentVenue, endIso);
    const dailyBins = trailing.length <= 45;
    const bins = {};
    for (const r of trailing) {
      const key = dailyBins ? r.date : isoDate(weekStart(new Date(r.date)));
      const b = bins[key] = bins[key] || { rev: 0, cogs: 0, wages: 0, days: 0, wageDays: 0, drv: 0 };
      b.rev += toNum(r.revenue_ex_gst); b.cogs += toNum(r.cogs_dollars);
      if (hasVal(r.wages_dollars)) { b.wages += toNum(r.wages_dollars); b.wageDays += 1; }
      b.drv += toNum(r.delivery_dollars);
      b.days += 1;
    }
    const keys = Object.keys(bins).sort();

    if (STATE.focusMetric) {
      const sh = dowShares(STATE.currentVenue);
      // Overhead per day = fixed OH + payroll tax/WC (both lumpy, DOW-weighted).
      const ohcDaily = ohDaily + wageOncostDaily(STATE.currentVenue, endIso);
      const ohOf = (k, B = bins) => dailyBins && sh ? ohcDaily * 7 * sh[new Date(k).getDay()] : ohcDaily * B[k].days;
      const t = STATE.baselines[STATE.currentVenue] || {};
      const a = actualCogs(STATE.currentVenue, endIso);
      const FOCUS = {
        revenue:       { label: 'Revenue $', unit: '$', target: null,
                         fn: (k, B = bins) => B[k].rev },
        wages_hero:    { label: 'Wages %', unit: '%', target: WAGE_TARGET_PCT,
                         fn: (k, B = bins) => B[k].wageDays && B[k].rev ? B[k].wages / B[k].rev * 100 : null },
        cogs_merged:   { label: 'Expected COGS % (Lightspeed)', unit: '%', target: COGS_TARGET_PCT,
                         fn: (k, B = bins) => B[k].rev ? B[k].cogs / B[k].rev * 100 : null },
        delivery_fees: { label: 'Delivery %', unit: '%', target: t.delivery && t.delivery.target,
                         fn: (k, B = bins) => B[k].rev ? (B[k].drv + (dfr ? B[k].rev * dfr.pct / 100 : 0)) / B[k].rev * 100 : null },
        overheads:     { label: 'Overheads %', unit: '%', target: null,
                         fn: (k, B = bins) => B[k].rev ? ohOf(k, B) / B[k].rev * 100 : null },
        profit:        { label: 'Expected profit $', unit: '$', target: null,
                         fn: (k, B = bins) => {
                           if (!B[k].wageDays) return null;
                           const cogsPct = a ? a.pct : (B[k].rev ? B[k].cogs / B[k].rev * 100 : 0);
                           return B[k].rev - B[k].rev * cogsPct / 100 - B[k].wages - ohOf(k, B)
                                  - (dfr ? B[k].rev * dfr.pct / 100 : 0) - (dailyBins && sh ? cpDaily * 7 * sh[new Date(k).getDay()] : cpDaily * B[k].days);
                         } },
      };
      const def = FOCUS[STATE.focusMetric];
      if (def) {
        const series = keys.map(k => def.fn(k));
        const vals = series.filter(x => x !== null && isFinite(x));
        const avg = vals.length ? vals.reduce((x, y) => x + y, 0) / vals.length : null;
        const fmtTick = def.unit === '$' ? (v => '$' + (Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : v)) : (v => v + '%');
        const sets = [
          { label: def.label, data: series.map(x => x === null ? null : Math.round(x * 10) / 10), borderColor: '#378ADD', backgroundColor: '#378ADD20', borderWidth: 2.5, pointRadius: keys.length > 45 ? 0 : 3, pointBackgroundColor: '#378ADD', tension: 0.25, spanGaps: true },
        ];
        if (avg !== null) sets.push({ label: 'Period average', data: keys.map(() => Math.round(avg * 10) / 10), borderColor: tickColor, borderDash: [6, 5], borderWidth: 1.5, pointRadius: 0 });
        if (def.target != null) sets.push({ label: 'Target', data: keys.map(() => def.target), borderColor: '#1D9E75', borderDash: [2, 4], borderWidth: 2, pointRadius: 0 });
        if (STATE.focusMetric === 'revenue') {
          const bp = breakevenParts(STATE.currentVenue, endIso);
          if (bp) {
            const beOf = k => (dailyBins && sh ? bp.fixedDaily * 7 * sh[new Date(k).getDay()] : bp.fixedDaily * bins[k].days) / (1 - bp.varPct / 100);
            sets.push({ label: 'Breakeven', data: keys.map(k => Math.round(beOf(k))), borderColor: '#E24B4A', borderDash: [3, 4], borderWidth: 1.8, pointRadius: 0 });
          }
        }
        // ---- prior-year overlays: same window shifted -364n days (weekday-
        // aligned), binned onto the SAME axis keys. Rate inputs (Xero fees,
        // OH, corp payroll) stay at today's rates — like-for-like at the
        // current cost structure.
        const ytEl2 = document.getElementById('year-toggles');
        const histMin = rows.length ? rows[0].date : '9999-12-31';
        const endYear = new Date(endIso).getFullYear();
        const avail = [];
        for (let n = 1; n <= 4; n++) {
          if (isoDate(addDays(new Date(endIso), -364 * n)) >= histMin) avail.push(n);
        }
        STATE.focusYears = STATE.focusYears.filter(n => avail.includes(n));
        if (ytEl2) {
          ytEl2.style.display = avail.length ? 'flex' : 'none';
          ytEl2.innerHTML = '<span class="yt-label">Compare:</span>' + avail.map(n =>
            `<button class="yt-btn${STATE.focusYears.includes(n) ? ' active' : ''}" onclick="toggleYear(${n})">${endYear - n}</button>`).join('');
        }
        const YCOLORS = { 1: '#E8A82B', 2: '#D4537E', 3: '#5DCAA5', 4: '#B4B2A9' };
        for (const n of STATE.focusYears) {
          const s2 = isoDate(addDays(new Date(keys[0]), -364 * n));
          const e2 = isoDate(addDays(new Date(endIso), -364 * n));
          const B2 = {};
          for (const r of rows) {
            if (r.date < s2 || r.date > e2) continue;
            const mapped = dailyBins ? isoDate(addDays(new Date(r.date), 364 * n)) : isoDate(addDays(weekStart(new Date(r.date)), 364 * n));
            const b = B2[mapped] = B2[mapped] || { rev: 0, cogs: 0, wages: 0, days: 0, wageDays: 0, drv: 0 };
            b.rev += toNum(r.revenue_ex_gst); b.cogs += toNum(r.cogs_dollars);
            if (hasVal(r.wages_dollars)) { b.wages += toNum(r.wages_dollars); b.wageDays += 1; }
            b.drv += toNum(r.delivery_dollars); b.days += 1;
          }
          const data2 = keys.map(k => B2[k] ? def.fn(k, B2) : null);
          sets.push({ label: String(endYear - n), data: data2.map(x => x === null ? null : Math.round(x * 10) / 10),
            borderColor: YCOLORS[n], backgroundColor: YCOLORS[n] + '20', borderWidth: 1.8, pointRadius: 0, tension: 0.25, spanGaps: true });
        }
        if (titleEl) titleEl.textContent = def.label + ' \u2014 ' + (dailyBins ? 'daily' : 'weekly') + ' \u00b7 click the card again to return';
        CHART_INSTANCE = new Chart(ctx, {
          type: 'line',
          data: { labels: keys.map(k => k.slice(5)), datasets: sets },
          options: {
            responsive: true, maintainAspectRatio: false,
            scales: {
              y: { ticks: { callback: fmtTick, font: { family: 'Inter', size: 11 }, color: tickColor }, grid: { color: gridColor } },
              x: { ticks: { font: { family: 'Inter', size: 11 }, color: tickColor, maxTicksLimit: 16 }, grid: { display: false } }
            },
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 14, boxHeight: 3, usePointStyle: true, pointStyle: 'line', font: { family: 'Space Grotesk', size: 12, weight: '500' }, color: tickColor, padding: 16 } } }
          }
        });
        return;
      }
    }
    const shc = dowShares(STATE.currentVenue);
    const cogsArr = keys.map(k => Math.round(bins[k].cogs));
    const wageArr = keys.map(k => Math.round(bins[k].wages));
    const delvArr = keys.map(k => Math.round(dfr ? bins[k].rev * dfr.pct / 100 : 0));
    const cpArr = keys.map(k => Math.round(dailyBins && shc ? cpDaily * 7 * shc[new Date(k).getDay()] : cpDaily * bins[k].days));
    const ohArr = keys.map(k => Math.round(dailyBins && shc ? ohDaily * 7 * shc[new Date(k).getDay()] : ohDaily * bins[k].days));
    const revArr = keys.map(k => Math.round(bins[k].rev));
    const profArr = keys.map((k, i) => bins[k].wageDays ? revArr[i] - cogsArr[i] - wageArr[i] - delvArr[i] - cpArr[i] - ohArr[i] : null);
    const mk = (label, color, data) => ({ type: 'bar', label, stack: 'costs', backgroundColor: color, borderWidth: 0, data });
    const stackSets = [
      mk('COGS', '#D85A30', cogsArr),
      mk('Wages', '#7F77DD', wageArr),
      mk('Delivery', '#EF9F27', delvArr),
      mk('Corp payroll', '#D4537E', cpArr),
      mk('Overheads', '#888780', ohArr),
      { type: 'line', label: 'Revenue', data: revArr, borderColor: '#378ADD', backgroundColor: '#378ADD20', borderWidth: 2.5, pointRadius: keys.length > 45 ? 0 : 3, pointBackgroundColor: '#378ADD', tension: 0.25 },
    ];
    if (titleEl) titleEl.textContent = (dailyBins ? 'Daily' : 'Weekly') + ' costs stacked vs revenue \u2014 the gap is profit';
    CHART_INSTANCE = new Chart(ctx, {
      data: { labels: keys.map(k => k.slice(5)), datasets: stackSets },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          y: { stacked: true, ticks: { callback: v => '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v), font: { family: 'Inter', size: 11 }, color: tickColor }, grid: { color: gridColor } },
          x: { stacked: true, ticks: { font: { family: 'Inter', size: 11 }, color: tickColor, maxTicksLimit: 16 }, grid: { display: false } }
        },
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 14, boxHeight: 8, font: { family: 'Space Grotesk', size: 12, weight: '500' }, color: tickColor, padding: 16 } } }
      }
    });
    return;
  }
  const datasets = lines.map(line => {
    const def = CHART_LINE_DEFS[line];
    return { label: def.label, yAxisID: def.axis || 'y', data: trailing.map(r => hasVal(r[def.key]) ? toNum(r[def.key]) : null), borderColor: def.color, backgroundColor: def.color + '20', tension: 0.3, borderWidth: 2.5, pointRadius: trailing.length > 45 ? 0 : 3, pointBackgroundColor: def.color, spanGaps: true };
  });
  CHART_INSTANCE = new Chart(ctx, {
    type: 'line',
    data: { labels: trailing.map(r => r.date.slice(5)), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { ticks: { callback: v => v + '%', font: { family: 'Inter', size: 11 }, color: tickColor }, grid: { color: gridColor } },
        y2: { position: 'right', ticks: { callback: v => '$' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v), font: { family: 'Inter', size: 11 }, color: '#3A7BD5' }, grid: { display: false } },
        x: { ticks: { font: { family: 'Inter', size: 11 }, color: tickColor, maxTicksLimit: 16 }, grid: { display: false } }
      },
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 14, boxHeight: 3, usePointStyle: true, pointStyle: 'line', font: { family: 'Space Grotesk', size: 12, weight: '500' }, color: tickColor, padding: 20 } } }
    }
  });
}

async function renderHourly(venue, date, showLabour) {
  const sec = document.getElementById('hourly-section');
  const noteEl = document.getElementById('hourly-note');
  if (HOURLY_CHART) { HOURLY_CHART.destroy(); HOURLY_CHART = null; }
  STATE._hourlyInsight = null;
  // Build the by-hour revenue + labour maps. For the group, sum every venue that
  // has an hourly feed (Stow + Mari today; HG once its own hourly pull lands) and
  // remember which venues had none, so the note can stay honest.
  const HVLABEL = { stow: 'Stowaway', hg: 'Harry Gatos', mari: "Marilyna's" };
  let rev = {}, labByHour = {}, hourlyMissing = [];
  if (venue === 'group') {
    let any = false;
    for (const v of REAL_VENUES) {
      const rv = await loadHourlyRevenue(v, date);
      if (!rv) { hourlyMissing.push(HVLABEL[v] || v); continue; }
      any = true;
      for (const [h, val] of Object.entries(rv)) rev[h] = (rev[h] || 0) + val;
      if (showLabour) {
        const lb = await loadHourlyLabour(v, date);
        if (lb && lb.byHour) for (const [h, val] of Object.entries(lb.byHour)) labByHour[h] = (labByHour[h] || 0) + val;
      }
    }
    if (!any) { sec.style.display = 'none'; return; }
  } else {
    const rv = await loadHourlyRevenue(venue, date);
    if (!rv) { sec.style.display = 'none'; return; }
    rev = rv;
    const lab = showLabour ? await loadHourlyLabour(venue, date) : null;
    labByHour = (lab && lab.byHour) || {};
    if (lab && lab.avgRate) STATE._avgLabourRate = lab.avgRate;
  }

  // Trading-day window for the axis: drop pre-dawn (0–5am) hours — they only
  // appear because a close shift crosses midnight and wraps to hour 0. Fold that
  // post-midnight labour into the last trading hour so it's still counted, and
  // clamp any stray labour to the window edges.
  const dayHours = [...Object.keys(rev), ...Object.keys(labByHour)].map(Number).filter(h => h >= 6);
  if (!dayHours.length) { sec.style.display = 'none'; return; }
  const lo = Math.min(...dayHours), hi = Math.max(...dayHours);
  const hours = [];
  for (let h = lo; h <= hi; h++) hours.push(h);
  const labFold = {};
  for (const [hs, v] of Object.entries(labByHour)) {
    let h = Number(hs);
    if (h < 6) h = hi; else if (h < lo) h = lo; else if (h > hi) h = hi;
    labFold[h] = (labFold[h] || 0) + v;
  }

  const revArr = hours.map(h => Math.round(rev[h] || 0));
  const labArr = hours.map(h => Math.round(labFold[h] || 0));
  const barColors = hours.map((h) => hourStrengthColor(rev[h] || 0, labFold[h] || 0));

  // Insight: peak-trade hour + the most labour-heavy hour that still did real
  // trade. Stashed for the verbal wrap to reuse (avoids a second fetch).
  let peakH = null, peakRev = -1, weakH = null, weakShare = -1, strongH = null, strongRatio = -1;
  for (const h of hours) {
    const rv = rev[h] || 0, lb = labFold[h] || 0;
    if (rv > peakRev) { peakRev = rv; peakH = h; }
    if (rv >= 150 && lb > 0) {
      const share = lb / rv;
      if (share > weakShare) { weakShare = share; weakH = h; }
      const ratio = rv / lb;
      if (ratio > strongRatio) { strongRatio = ratio; strongH = h; }
    }
  }
  STATE._hourlyInsight = {
    peakH, peakRev,
    weakH, weakShare, weakLabour: weakH != null ? (labFold[weakH] || 0) : 0, weakRev: weakH != null ? (rev[weakH] || 0) : 0,
    strongH, strongRev: strongH != null ? (rev[strongH] || 0) : 0, strongLabour: strongH != null ? (labFold[strongH] || 0) : 0,
    hasLabour: showLabour && Object.keys(labByHour).length > 0,
  };

  // Plain-language line under the title.
  const bits = [];
  if (peakH != null) bits.push(`Peak trade ${hourLabel(peakH)} on ${fmtDollars(peakRev)}`);
  if (STATE._hourlyInsight.hasLabour && weakH != null && weakShare >= 0.6)
    bits.push(`the ${hourLabel(weakH)} block ran labour-heavy (${fmtDollars(STATE._hourlyInsight.weakLabour)} wages on ${fmtDollars(STATE._hourlyInsight.weakRev)})`);
  else if (STATE._hourlyInsight.hasLabour && strongH != null)
    bits.push(`the ${hourLabel(strongH)} hour did the most per labour dollar`);
  if (hourlyMissing.length) bits.push(`${hourlyMissing.join(' & ')} not in this chart yet (no hourly feed)`);
  noteEl.textContent = bits.length ? bits.join('; ') + '.' : '';
  noteEl.style.display = bits.length ? '' : 'none';

  const tickColor = IS_DARK ? '#B7B1A4' : '#5A544C';
  const gridColor = IS_DARK ? 'rgba(120,110,90,0.2)' : 'rgba(232,223,207,0.4)';
  const datasets = [
    { type: 'bar', label: 'Revenue (ex-GST)', data: revArr, backgroundColor: barColors,
      borderWidth: 0, yAxisID: 'y', order: 2 },
  ];
  if (STATE._hourlyInsight.hasLabour) {
    datasets.push({ type: 'line', label: 'Labour $', data: labArr, borderColor: '#7F77DD',
      backgroundColor: '#7F77DD20', borderWidth: 2.5, pointRadius: 3, pointBackgroundColor: '#7F77DD',
      tension: 0.25, yAxisID: 'y2', order: 1 });
  }
  const ctx = document.getElementById('hourly-chart').getContext('2d');
  HOURLY_CHART = new Chart(ctx, {
    data: { labels: hours.map(hourLabel), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, ticks: { callback: v => '$' + (v >= 1000 ? (v / 1000).toFixed(1) + 'k' : v), font: { family: 'Inter', size: 11 }, color: tickColor }, grid: { color: gridColor } },
        y2: { position: 'right', beginAtZero: true, display: STATE._hourlyInsight.hasLabour, ticks: { callback: v => '$' + v, font: { family: 'Inter', size: 11 }, color: '#7F77DD' }, grid: { display: false } },
        x: { ticks: { font: { family: 'Inter', size: 11 }, color: tickColor }, grid: { display: false } }
      },
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 14, boxHeight: 8, font: { family: 'Space Grotesk', size: 12, weight: '500' }, color: tickColor, padding: 16 } },
        tooltip: { callbacks: { label: c => c.dataset.label + ': ' + fmtDollars(c.parsed.y) } }
      }
    }
  });
  sec.style.display = '';
}

function renderDailyWrap(day) {
  const el = document.getElementById('daily-wrap');
  if (!el) return;
  el.innerHTML = '';
  if (CURRENT_ROLE !== 'admin' || !day) return;
  const w = pnlWindow(day);
  if (!w) return;

  const eat = toNum(day.eatclub_giveaway_ex_gst);
  const gross = w.rev + eat;
  if (!gross) return;
  const pct = x => x / gross * 100;
  const profit = w.profit;
  const margin = pct(profit);
  const venueLabel = (VENUE_CONFIG[STATE.currentVenue] || {}).label || 'The venue';
  const cogsD = w.rev * w.cogsPct / 100;
  const cogsPctG = pct(cogsD);
  const wagesPctG = pct(w.wages);
  const wTgt = wageTargetFor(day);
  const fmtP = x => (x < 0 ? '−$' : '$') + Math.round(Math.abs(x)).toLocaleString();
  const seed = parseInt(String(day.date).replace(/\D/g, '').slice(-4) || '0', 10);

  // --- Sentence 1: did it make money? ---
  let s1;
  if (profit >= 0) {
    const opener = pickPhrase([
      `${venueLabel} banked`, `${venueLabel} kept`, `${venueLabel} put`, `${venueLabel} walked away with`,
    ], seed);
    s1 = `${opener} <span class="dw-pos">${fmtP(profit)}</span> on the day — a <strong>${fmtPct(margin)}</strong> margin on ${fmtDollars(gross)} through the till.`;
  } else {
    const opener = pickPhrase([
      `${venueLabel} went backwards`, `${venueLabel} dropped`, `${venueLabel} bled`, `${venueLabel} came up short`,
    ], seed);
    s1 = `${opener} <span class="dw-neg">${fmtP(profit)}</span> — a <strong>${fmtPct(margin)}</strong> margin on ${fmtDollars(gross)} through the till.`;
  }

  // --- Sentence 2: revenue vs typical / LY ---
  const rows = STATE.histories[STATE.currentVenue] || [];
  let anchorDay = STATE.currentDay;
  if (!anchorDay && rows.length) anchorDay = rows[rows.length - 1].date;
  const yy = yoyRevenueDelta(rows, STATE.currentTimeframe, anchorDay);
  const dow = /^\d{4}-\d{2}-\d{2}$/.test(String(day.date)) ? new Date(day.date).getDay() : null;
  const typical = dow != null ? dowForecast(STATE.currentVenue, dow) : null;
  let s2 = '';
  if (typical && w.rev) {
    const d = (w.rev - typical) / typical * 100;
    const wd = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][dow];
    if (d >= 8) s2 = `Trade ran <span class="dw-pos">${Math.round(d)}% above</span> a typical ${wd} (${fmtDollars(typical)})`;
    else if (d <= -8) s2 = `Trade sat <span class="dw-neg">${Math.round(Math.abs(d))}% under</span> a typical ${wd} (${fmtDollars(typical)})`;
    else s2 = `Trade landed about where a ${wd} usually does (${fmtDollars(typical)})`;
    if (yy !== null) s2 += `, and ${yy >= 0 ? `<span class="dw-pos">${yy.toFixed(0)}% up</span>` : `<span class="dw-neg">${Math.abs(yy).toFixed(0)}% down</span>`} on the same week last year.`;
    else s2 += '.';
  } else if (yy !== null) {
    s2 = `Revenue was ${yy >= 0 ? `<span class="dw-pos">${yy.toFixed(0)}% up</span>` : `<span class="dw-neg">${Math.abs(yy).toFixed(0)}% down</span>`} on the same week last year.`;
  }

  // --- Sentence 3: the single biggest lever ---
  const avgRate = STATE._avgLabourRate && STATE._avgLabourRate > 0 ? STATE._avgLabourRate : 45;
  const wageOver = wTgt != null ? (wagesPctG - wTgt) / 100 * gross : 0;
  const cogsOver = (cogsPctG - 22) / 100 * gross;
  let s3 = '';
  if (wageOver > 40 && wageOver >= cogsOver) {
    const hrs = wageOver / avgRate;
    const insight = STATE._hourlyInsight;
    let trim = '';
    if (insight && insight.hasLabour && insight.weakH != null && insight.weakShare >= 0.6)
      trim = ` — the ${hourLabel(insight.weakH)} block was the soft spot (${fmtDollars(insight.weakLabour)} wages on ${fmtDollars(insight.weakRev)})`;
    s3 = `The lever was labour: wages ran <span class="dw-neg">${fmtP(wageOver)}</span> (~${hrs.toFixed(hrs < 10 ? 1 : 0)} hours) over the ${fmtPct(wTgt)} target for the day${trim}. Pull that back and the day hits its number.`;
  } else if (cogsOver > 40 && cogsOver > wageOver) {
    s3 = `Cost of goods was the drag — <span class="dw-neg">${fmtP(cogsOver)}</span> over the 22% line at ${fmtPct(cogsPctG)}. That's the difference between this result and a clean day.`;
  } else if (profit >= 0) {
    // Profitable: praise the strongest thing.
    const insight = STATE._hourlyInsight;
    if (cogsPctG <= 20)
      s3 = `COGS behaved beautifully at <span class="dw-pos">${fmtPct(cogsPctG)}</span>, well under the 22% target — that's where the margin came from.`;
    else if (wTgt != null && wagesPctG <= wTgt - 2)
      s3 = `Wages came in tight at <span class="dw-pos">${fmtPct(wagesPctG)}</span> against a ${fmtPct(wTgt)} target — the roster earned its keep.`;
    else if (insight && insight.hasLabour && insight.strongH != null)
      s3 = `The ${hourLabel(insight.strongH)} hour did ${fmtDollars(insight.strongRev)} on ${fmtDollars(insight.strongLabour)} of labour — lean into that window.`;
    else
      s3 = `Nothing ran hot: COGS at ${fmtPct(cogsPctG)} and wages at ${fmtPct(wagesPctG)} both sat close to target.`;
  } else {
    // Loss but no single big lever — name the nearest one.
    if (wageOver >= cogsOver && wTgt != null)
      s3 = `Costs were close to line — wages at ${fmtPct(wagesPctG)} vs ${fmtPct(wTgt)} the main watch item; it was mostly a light-trade day.`;
    else
      s3 = `Costs held near target — this was a revenue problem more than a cost one.`;
  }

  el.innerHTML = `<div class="daywrap"><p>${[s1, s2, s3].filter(Boolean).join(' ')}</p></div>`;
}

async function renderDaySingle() {
  const sec = document.getElementById('hourly-section');
  const wrapEl = document.getElementById('daily-wrap');
  const isDay = STATE.currentTimeframe === 'day' && STATE.currentDay;
  const venue = STATE.currentVenue;
  const roleCfg = ROLE_CONFIG[CURRENT_ROLE] || ROLE_CONFIG.admin;
  // Clear both features for anything that isn't a single day.
  if (!isDay) {
    if (HOURLY_CHART) { HOURLY_CHART.destroy(); HOURLY_CHART = null; }
    if (sec) sec.style.display = 'none';
    if (wrapEl) wrapEl.innerHTML = '';
    return;
  }
  const token = (STATE._daySingleToken = (STATE._daySingleToken || 0) + 1);
  const date = STATE.currentDay;
  STATE._avgLabourRate = null;   // per-day/venue; wrap falls back to ~$45/h if unset

  // Feature 1 — hourly chart: per venue, or the group (sums every venue with an
  // hourly feed). Needs hourly data for the day, else the section hides itself.
  if (REAL_VENUES.includes(venue) || venue === 'group') {
    await renderHourly(venue, date, !!roleCfg.show_dollar_amounts);
    if (token !== STATE._daySingleToken) return;   // stale — a newer render started
  } else if (sec) {
    if (HOURLY_CHART) { HOURLY_CHART.destroy(); HOURLY_CHART = null; }
    sec.style.display = 'none';
  }

  // Verbal wrap removed at Zak's request (2026-07-21) — keep the slot empty.
  if (wrapEl) wrapEl.innerHTML = '';
}

function render() {
  renderVenueTabs();
  renderVenueStrip();
  renderTimeframeToggle();
  renderDayPicker();
  renderSnapshot();
  renderChart();
  renderDaySingle();
  fetchLastUpdate();
}

function focusMetric(key) {
  STATE.focusMetric = STATE.focusMetric === key ? null : key;
  STATE.focusYears = [];
  render();
}

function toggleYear(n) {
  const i = STATE.focusYears.indexOf(n);
  if (i >= 0) STATE.focusYears.splice(i, 1); else STATE.focusYears.push(n);
  render();
}

function switchVenue(venue) {
  STATE.currentVenue = venue;
  STATE.focusMetric = null;
  const rows = STATE.histories[venue] || [];
  STATE.currentDay = rows.length ? rows[rows.length - 1].date : null;
  render();
}

function toggleAdmin() {
  STATE.includeAdmin = !STATE.includeAdmin;
  STATE.histories.group = synthesizeGroupHistory(STATE.histories);
  render();
}

function toggleLeave() {
  STATE.includeLeave = !STATE.includeLeave;
  STATE.histories.group = synthesizeGroupHistory(STATE.histories);
  render();
}

function switchTimeframe(tf) {
  STATE.currentTimeframe = tf;
  if (tf !== 'day') {
    const rows = STATE.histories[STATE.currentVenue] || [];
    if (rows.length) STATE.currentDay = rows[rows.length - 1].date;
  }
  if (tf === 'range' && !STATE.rangeStart) {
    const rows = STATE.histories[STATE.currentVenue] || [];
    if (rows.length) {
      STATE.rangeStart = rows[Math.max(0, rows.length - 28)].date;
      STATE.rangeEnd = rows[rows.length - 1].date;
    }
  }
  render();
}

function switchDay(iso) {
  STATE.currentTimeframe = 'day';
  STATE.currentDay = iso;
  render();
}

async function bootstrap() {
  const roleCfg = ROLE_CONFIG[CURRENT_ROLE] || ROLE_CONFIG.admin;
  STATE.histories = await loadAllHistories(CURRENT_ROLE);
  // Roster forecast is OPERATIONAL — every role gets the week-ahead view.
  try {
    const r = await fetch('/data/roster_week.json?t=' + Date.now());
    if (r.ok) STATE.roster = await r.json();
  } catch (e) {}
  // Economic feeds are ADMIN-ONLY (Zak, 2026-07-15: only admin sees overheads
  // and profit) — manager roles never download Xero/fee data at all.
  if (CURRENT_ROLE === 'admin') {
    try {
      const r = await fetch('/data/xero_cogs_weekly.csv?t=' + Date.now());
      if (r.ok) STATE.xeroCogs = parseCsv(await r.text());
    } catch (e) {}
    try {
      const r = await fetch('/data/xero_overheads_monthly.csv?t=' + Date.now());
      if (r.ok) STATE.xeroOH = parseCsv(await r.text());
    } catch (e) {}
    try {
      // Wage on-costs: owner salary (-> corp payroll) + payroll-tax/WC rate
      // (-> overheads, as a % of wages). Built by compute_wage_oncosts.py.
      const r = await fetch('/data/wage_oncosts.json?t=' + Date.now());
      if (r.ok) {
        const o = await r.json();
        STATE.oncost = { rate: toNum(o.oncost_rate), ownerWeekly: toNum(o.owner_weekly_inc_super) };
      }
    } catch (e) {}
    try {
      // Actual daily Uber Eats fees, split commission vs marketing, per shop.
      // Pulled from the merchant portal by the uber-eats-daily-fees scheduled task.
      const r = await fetch('/data/uber_daily.csv?t=' + Date.now());
      if (r.ok) STATE.uberDaily = parseCsv(await r.text());
    } catch (e) {}
    try {
      const r = await fetch('/data/uber_marketing_weekly.csv?t=' + Date.now());
      if (r.ok) STATE.uberAds = parseCsv(await r.text());
    } catch (e) {}
    try {
      // Actual daily Uber Direct fees (Mari's own online orders). Emailed daily
      // invoice -> Pipedream -> this CSV (see pipedream/uber_direct_ingest.js).
      const r = await fetch('/data/uber_direct_daily.csv?t=' + Date.now());
      if (r.ok) STATE.uberDirect = parseCsv(await r.text());
    } catch (e) {}
    try {
      const r = await fetch('/data/uber_fees_weekly.csv?t=' + Date.now());
      if (r.ok) STATE.uberFees = parseCsv(await r.text());
    } catch (e) {}
    for (const [v, f] of [['mari', 'mari_baseline.json'], ['stow', 'stow_baseline.json'], ['hg', 'hg_baseline.json']]) {
      try {
        const r = await fetch('/baselines/' + f + '?t=' + Date.now());
        if (r.ok) STATE.baselines[v] = (await r.json()).targets_and_alerts || {};
      } catch (e) {}
    }
  }
  const defaultVenue = roleCfg.defaultVenue;
  const withData = roleCfg.venues.find(v => (STATE.histories[v] || []).length > 0);
  STATE.currentVenue = (STATE.histories[defaultVenue]?.length ? defaultVenue : withData) || defaultVenue;
  const rows = STATE.histories[STATE.currentVenue] || [];
  STATE.currentDay = rows.length ? rows[rows.length - 1].date : isoDate(addDays(sydneyToday(), -1));
  // The timeframe toggle (week chips + buttons + More select) is rebuilt on
  // every render by renderTimeframeToggle() with inline handlers, so there is
  // nothing to wire here beyond the custom-range Apply button.
  document.getElementById('range-apply').onclick = applyRange;
  render();
}
