/* dashboard/_shared/util.js — pure helpers, formatting & data transforms (no DOM, no I/O)
   Classic <script src> loaded BEFORE the inline bootstrap, so every declaration
   here is a window global exactly as when it lived in index.html. Extracted from
   sales/index.html verbatim (byte-identical, proven by the arch guard). */

async function sha256Hex(s) {
  const buf = new TextEncoder().encode(s);
  const digest = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
}

function pill(text, kind) {
  const m = { red: ['#FCEBEB', '#791F1F', '#501313', '#F7C1C1'],
              green: ['#E1F5EE', '#085041', '#04342C', '#9FE1CB'],
              warn: ['#FAEEDA', '#633806', '#412402', '#FAC775'] }[kind];
  const bg = IS_DARK ? m[2] : m[0], fg = IS_DARK ? m[3] : m[1];
  return `<span style="font-size:12px;font-weight:500;padding:2px 8px;border-radius:8px;background:${bg};color:${fg};vertical-align:2px;white-space:nowrap;">${text}</span>`;
}

function financeDaily(venue, endIso) {
  const feed = STATE.xeroOH;
  if (!feed.length) return 0;
  const endMonth = String(endIso || '').slice(0, 7);
  const months = feed.filter(r => r.month && r.month < endMonth && hasVal(r.group_finance))
                     .sort((a, b) => a.month.localeCompare(b.month)).slice(-3);
  if (!months.length) return 0;
  let fin = 0, days = 0;
  for (const r of months) {
    fin += toNum(r.group_finance);
    const [y, m] = r.month.split('-').map(Number);
    days += new Date(y, m, 0).getDate();
  }
  return days ? fin * (PLAN_WAGE_SHARE[venue] || 0) / days : 0;
}

function dowDeptForecast(venue, dow) {
  STATE._dfcst = STATE._dfcst || {};
  if (!STATE._dfcst[venue]) {
    const rows = STATE.histories[venue] || [];
    const f = [[], [], [], [], [], [], []], b = [[], [], [], [], [], [], []];
    for (let i = rows.length - 1; i >= 0; i--) {
      const r = rows[i];
      if (!hasVal(r.food_ex_gst)) continue;
      const d = new Date(r.date).getDay();
      if (f[d].length < 8) { f[d].push(toNum(r.food_ex_gst)); b[d].push(toNum(r.bev_ex_gst)); }
      if (f.every(a2 => a2.length >= 8)) break;
    }
    const med = a2 => {
      if (!a2.length) return 0;
      const s2 = [...a2].sort((x, y) => x - y);
      return s2.length % 2 ? s2[(s2.length - 1) / 2] : (s2[s2.length / 2 - 1] + s2[s2.length / 2]) / 2;
    };
    STATE._dfcst[venue] = { food: f.map(med), bev: b.map(med) };
  }
  return { food: STATE._dfcst[venue].food[dow], bev: STATE._dfcst[venue].bev[dow] };
}

function periodBounds(tf, anchorIso) {
  if (!anchorIso) return null;
  const a = new Date(anchorIso);
  const today = sydneyToday();
  if (tf === 'week') { const st = weekStart(a); return { s: isoDate(st), e: isoDate(addDays(st, 6)) }; }
  if (tf === 'month') return { s: isoDate(monthStart(a)), e: isoDate(new Date(a.getFullYear(), a.getMonth() + 1, 0)) };
  if (tf === 'quarter') { const q = quarterStart(a); return { s: isoDate(q), e: isoDate(new Date(q.getFullYear(), q.getMonth() + 3, 0)) }; }
  if (tf === 'thisfy') { const fy = today.getMonth() >= 6 ? today.getFullYear() : today.getFullYear() - 1; return { s: isoDate(new Date(fy, 6, 1)), e: isoDate(new Date(fy + 1, 5, 30)) }; }
  return null;
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const headers = lines[0].split(',').map(h => h.trim());
  return lines.slice(1).map(line => {
    const values = line.split(',').map(v => v.trim());
    return Object.fromEntries(headers.map((h, i) => [h, values[i]]));
  });
}

function vsTarget(pct, tgt, band = 2, invert = false) {
  if (pct === null || tgt === null || !isFinite(pct) || !isFinite(tgt)) return '';
  const d = pct - tgt;
  const miss = invert ? d < 0 : d > 0;
  const cls = Math.abs(d) <= band ? 'vs-ok' : miss ? 'vs-bad' : 'vs-good';
  const word = Math.abs(d) < 0.05 ? 'on target'
             : `${Math.abs(d).toFixed(1)}pp ${d > 0 ? 'over' : 'under'}`;
  return `<span class="vs-t">target ${fmtPct(tgt)}</span><span class="${cls}">${word}</span>`;
}

function fmtDollars(n) { return '$' + Math.round(n).toLocaleString(); }

function fmtPct(n) { return (Math.round(n * 10) / 10).toFixed(1) + '%'; }

function stripOverhead(rows) {
  for (const r of rows) {
    const adm = toNum(r.wages_admin_dollars);
    if (!adm || !hasVal(r.wages_dollars)) continue;
    const w = toNum(r.wages_dollars) - adm;
    const rev = toNum(r.revenue_ex_gst);
    r.wages_dollars = w;
    r.wages_pct = rev ? w / rev * 100 : '';
  }
  return rows;
}

function applyAssumed(rows) {
  for (const r of rows) {
    const n = toNum(r.wages_assumed_shifts);
    const a = toNum(r.wages_assumed_dollars);
    if (!n || !a || !hasVal(r.wages_dollars)) continue;
    const rev = toNum(r.revenue_ex_gst);
    r.wages_actual_dollars = toNum(r.wages_dollars);   // keep the truth reachable
    r.wages_assumed_n = n;
    r.wages_dollars = a;
    r.wages_pct = rev ? a / rev * 100 : '';
  }
  return rows;
}

function synthesizeGroupHistory(histories) {
  // Any day on/after this week's Monday is the open (in-progress) payroll week.
  // Its "leave" is provisional (unapproved salaried timesheets over-book it), so
  // it must never fold into wages/profit — only closed-week leave is real.
  const openWeekStart = isoDate(weekStart(sydneyToday()));
  const allDates = new Set();
  for (const v of REAL_VENUES) for (const r of histories[v] || []) allDates.add(r.date);
  const rows = [];
  for (const date of Array.from(allDates).sort()) {
    const sum = { revenue_ex_gst: 0, cogs_dollars: 0, wages_dollars: 0, delivery_dollars: 0, gp_dollars: 0,
                  food_ex_gst: 0, bev_ex_gst: 0, food_cogs: 0, bev_cogs: 0,
                  wages_kitchen_dollars: 0, wages_foh_dollars: 0,
                  eatclub_giveaway_ex_gst: 0, eatclub_covers: 0 };
    let venueCount = 0;
    let wagePresent = false;
    let splitPresent = false, kwPresent = false;
    for (const v of REAL_VENUES) {
      const row = (histories[v] || []).find(r => r.date === date);
      if (!row || !row.revenue_ex_gst) continue;
      // Group-level food/kitchen slice (Big Chef): Mari is an all-kitchen
      // venue — its revenue is food, its COGS food COGS, its wages minus
      // driver = kitchen labour. Stow/HG contribute their split fields.
      // Presence flags come from Stow/HG only, so pre-split history stays
      // honestly "awaiting split data" instead of showing Mari-only numbers.
      if (v === 'mari') {
        // Mari now emits its split properly (all revenue = food, Kitchen OU =
        // kitchen labour, Driver in its own column). Prefer those real columns;
        // fall back to deriving for history not yet backfilled. The fallback
        // subtracts delivery_dollars, which ALSO carries Uber commission and
        // Uber Direct fees — it understates kitchen whenever those are non-zero,
        // which is exactly why the real column is worth having.
        sum.food_ex_gst += hasVal(row.food_ex_gst) ? toNum(row.food_ex_gst) : toNum(row.revenue_ex_gst);
        sum.food_cogs += hasVal(row.food_cogs) ? toNum(row.food_cogs) : toNum(row.cogs_dollars);
        if (hasVal(row.wages_kitchen_dollars)) sum.wages_kitchen_dollars += toNum(row.wages_kitchen_dollars);
        else if (hasVal(row.wages_dollars)) sum.wages_kitchen_dollars += Math.max(0, toNum(row.wages_dollars) - toNum(row.delivery_dollars));
      } else if (hasVal(row.food_ex_gst)) {
        sum.food_ex_gst += toNum(row.food_ex_gst);
        sum.bev_ex_gst += toNum(row.bev_ex_gst);
        sum.food_cogs += toNum(row.food_cogs);
        sum.bev_cogs += toNum(row.bev_cogs);
        splitPresent = true;
        if (hasVal(row.wages_kitchen_dollars)) {
          sum.wages_kitchen_dollars += toNum(row.wages_kitchen_dollars);
          sum.wages_foh_dollars += toNum(row.wages_foh_dollars);
          kwPresent = true;
        }
      }
      sum.revenue_ex_gst   += toNum(row.revenue_ex_gst);
      sum.cogs_dollars     += toNum(row.cogs_dollars);
      if (hasVal(row.wages_dollars)) { sum.wages_dollars += toNum(row.wages_dollars); wagePresent = true; }
      // Venue rows arrive operational-only (stripOverhead took admin off at
      // load, and leave was never in them). Group can put both back — that's
      // the toggle, and it's the ONLY place the two bases differ, so group and
      // venues reconcile exactly when it's off. Default off (Zak, 2026-07-17):
      // the overhead answers "what does payroll cost?", not "how did we trade?".
      if (STATE.includeAdmin) sum.wages_dollars += toNum(row.wages_admin_dollars);
      // Leave IS a real cost — booked leave (IsLeave timesheets, in the person's
      // hours) plus the estimated salaried-shortfall leave. Include it when toggled.
      // Open-week leave is an estimate that firms up as timesheets are approved.
      if (STATE.includeLeave) sum.wages_dollars += toNum(row.leave_dollars);
      sum.delivery_dollars += toNum(row.delivery_dollars);
      sum.gp_dollars       += toNum(row.gp_dollars);
      sum.eatclub_giveaway_ex_gst += toNum(row.eatclub_giveaway_ex_gst);
      sum.eatclub_covers          += toNum(row.eatclub_covers);
      venueCount++;
    }
    if (venueCount === 0) continue;
    const rev = sum.revenue_ex_gst;
    rows.push({
      date, revenue_ex_gst: sum.revenue_ex_gst,
      cogs_dollars: sum.cogs_dollars, cogs_pct: rev ? sum.cogs_dollars / rev * 100 : 0,
      wages_dollars: wagePresent ? sum.wages_dollars : '',
      wages_pct: wagePresent && rev ? sum.wages_dollars / rev * 100 : '',
      delivery_dollars: sum.delivery_dollars, delivery_pct: rev ? sum.delivery_dollars / rev * 100 : 0,
      gp_dollars: sum.gp_dollars, gp_pct: rev ? sum.gp_dollars / rev * 100 : 0,
      eatclub_giveaway_ex_gst: sum.eatclub_giveaway_ex_gst, eatclub_covers: sum.eatclub_covers,
      contributing_venues: venueCount,
      cogs_alert: 'unknown', wages_alert: 'unknown', delivery_alert: 'unknown', gp_alert: 'unknown',
      ...(splitPresent ? {
        food_ex_gst: sum.food_ex_gst, bev_ex_gst: sum.bev_ex_gst,
        food_cogs: sum.food_cogs, bev_cogs: sum.bev_cogs,
        food_cogs_pct: sum.food_ex_gst ? sum.food_cogs / sum.food_ex_gst * 100 : '',
        bev_cogs_pct: sum.bev_ex_gst ? sum.bev_cogs / sum.bev_ex_gst * 100 : '',
        food_gp_pct: sum.food_ex_gst ? (sum.food_ex_gst - sum.food_cogs) / sum.food_ex_gst * 100 : '',
        bev_gp_pct: sum.bev_ex_gst ? (sum.bev_ex_gst - sum.bev_cogs) / sum.bev_ex_gst * 100 : '',
      } : {}),
      ...(kwPresent ? {
        wages_kitchen_dollars: sum.wages_kitchen_dollars,
        wages_foh_dollars: sum.wages_foh_dollars,
        wages_kitchen_pct: rev ? sum.wages_kitchen_dollars / rev * 100 : '',
        wages_foh_pct: rev ? sum.wages_foh_dollars / rev * 100 : '',
      } : {}),
    });
  }
  return rows;
}

function currentWeekDays() {
  const mon = weekStart(sydneyToday());
  const out = [];
  for (let i = 0; i < 7; i++) out.push(addDays(mon, i));
  return out;
}

function cardsForCurrentView() {
  const venueCfg = VENUE_CONFIG[STATE.currentVenue];
  const roleCfg = ROLE_CONFIG[CURRENT_ROLE] || ROLE_CONFIG.admin;
  const base = (roleCfg.cards || venueCfg.cards);
  // Admin: revenue / COGS / wages / profit AND the delivery-fee / overhead cost
  // lines now all live in the dedicated #profit-card hero block (renderProfitCard).
  // The plain snapshot grid is therefore empty for admin — the kitchen roles keep
  // their own cards untouched.
  let list = (CURRENT_ROLE === 'admin')
    ? []
    : base.slice();
  // Retired cards: standalone GP (just 100 − COGS) and the EatClub give-away
  // (now folded into the profit breakdown as a cost line, not its own card).
  list = list.filter(k => k !== 'gp' && k !== 'eatclub');
  return list;
}

function sparkline(vals) {
  const v = vals.filter(x => x !== null && isFinite(x));
  if (v.length < 5) return '';
  const min = Math.min(...v), max = Math.max(...v);
  const W = 72, H = 26;
  const pts = vals.map((x, i) => (x === null || !isFinite(x)) ? null :
    `${(i / (vals.length - 1) * W).toFixed(1)},${max === min ? H / 2 : (H - 2 - (x - min) / (max - min) * (H - 4)).toFixed(1)}`)
    .filter(Boolean).join(' ');
  const col = IS_DARK ? '#8B87D9' : '#7F77DD';
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5" opacity=".65"/></svg>`;
}

function cogsStatus(pct) { return pct > COGS_TARGET_PCT + 2 ? 'red' : pct < COGS_TARGET_PCT - 2 ? 'green' : 'amber'; }

function dowWageTarget(venue, dow) {
  STATE._dwt = STATE._dwt || {};
  if (STATE._dwt[venue] === undefined) {
    const rows = (STATE.histories[venue] || []).slice(-84);
    const wSum = [0, 0, 0, 0, 0, 0, 0], rSum = [0, 0, 0, 0, 0, 0, 0];
    for (const r of rows) {
      if (!hasVal(r.wages_dollars) || !toNum(r.revenue_ex_gst)) continue;
      const d = new Date(r.date).getDay();
      wSum[d] += toNum(r.wages_dollars);
      rSum[d] += toNum(r.revenue_ex_gst);
    }
    const wT = wSum.reduce((a, b) => a + b, 0), rT = rSum.reduce((a, b) => a + b, 0);
    STATE._dwt[venue] = wT && rT ? wSum.map((w, i) => rSum[i] ? WAGE_TARGET_PCT * (w / wT) / (rSum[i] / rT) : null) : null;
  }
  const t = STATE._dwt[venue];
  return t ? t[dow] : null;
}

function wageTargetFor(day, venue = STATE.currentVenue) {
  const t = String((day && day.date) || '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return dowWageTarget(venue, new Date(t).getDay());
  return WAGE_TARGET_PCT;
}

function deptWageShares(venue, dow) {
  const rows = (STATE.histories[venue] || []).slice(-84);
  let k = 0, f = 0;
  for (const r of rows) {
    if (dow !== undefined && new Date(r.date).getDay() !== dow) continue;
    k += toNum(r.wages_kitchen_dollars); f += toNum(r.wages_foh_dollars);
  }
  const t = k + f;
  if (dow !== undefined && t < 500) return deptWageShares(venue);
  return t ? { k: k / t, f: f / t } : null;
}

function missingDepts(v, dIso, dow, deptOnly) {
  const vens = (STATE.roster.days || {})[dIso] || {};
  const out = [];
  for (const pk of (v === 'group' ? ['stow', 'hg', 'mari'] : [v])) {
    const mix = deptWageShares(pk, dow);
    if (!mix) continue;                                  // no split data (Mari) — can't judge
    if ((dowForecast(pk, dow) || 0) < 300) continue;     // venue shut this weekday
    const dd = vens[pk] || {};
    const kitShut = pk === 'stow' && dowDeptForecast(pk, dow).food < 150;
    const name = v === 'group' ? { stow: 'Stow', hg: 'HG', mari: 'Mari' }[pk] + ' ' : '';
    if ((!deptOnly || deptOnly === 'Kitchen') && mix.k > 0.05 && !kitShut && (dd.Kitchen || 0) < 25) out.push(name + 'kitchen');
    if ((!deptOnly || deptOnly === 'FOH') && mix.f > 0.05 && (dd.FOH || 0) < 25) out.push(name + 'FOH');
  }
  return out;
}

function waDay(v, dIso, deptOnly, todayIso) {
  const dow = new Date(dIso).getDay();
  const mixV = deptWageShares(v, dow);
  const deptShare = deptOnly && mixV ? (deptOnly === 'Kitchen' ? mixV.k : mixV.f) : 1;
  const dwt = dowWageTarget(v, dow);
  const target = dwt !== null ? dwt * deptShare : null;

  const hist = (STATE.histories[v] || []).find(r => r.date === dIso);
  // Actuals land the morning after, so "today" still reads as roster.
  const hasActual = dIso < todayIso && hist && toNum(hist.revenue_ex_gst) > 0 && hasVal(hist.wages_dollars);

  let wages = 0, rev = 0, mode = 'roster', noSplit = false;
  if (hasActual) {
    mode = 'actual';
    rev = toNum(hist.revenue_ex_gst);
    if (deptOnly === 'Kitchen') {
      if (hasVal(hist.wages_kitchen_dollars)) wages = toNum(hist.wages_kitchen_dollars); else noSplit = true;
    } else if (deptOnly === 'FOH') {
      if (hasVal(hist.wages_foh_dollars)) wages = toNum(hist.wages_foh_dollars); else noSplit = true;
    } else if (v === 'group') {
      wages = toNum(hist.wages_dollars);
    } else if (hasVal(hist.wages_kitchen_dollars) && hasVal(hist.wages_foh_dollars)) {
      // Kitchen + FOH excludes admin — matches the roster cells, which strip
      // Admin at venue level. Mixing the two conventions across the seam would
      // make the weekly total meaningless.
      wages = toNum(hist.wages_kitchen_dollars) + toNum(hist.wages_foh_dollars);
    } else {
      wages = toNum(hist.wages_dollars);
    }
  } else {
    rev = venueForecast(v, dow);
    const vens = (STATE.roster.days || {})[dIso] || {};
    if (deptOnly) {
      if (v === 'group') { for (const pk of ['stow', 'hg', 'mari']) wages += ((vens[pk] || {})[deptOnly]) || 0; }
      else wages = ((vens[v] || {})[deptOnly]) || 0;
    } else if (v === 'group') { for (const pk of ['stow', 'hg', 'mari']) wages += (vens[pk] || {}).total || 0; }
    else {
      // Admin wages reflect at GROUP level only (Zak, 2026-07-15) — venue
      // cells show the wages the venue can actually roster against its trade.
      const dv = vens[v] || {};
      wages = (dv.total || 0) - (dv.Admin || 0);
    }
  }

  // Closed trading days (HG shuts Sundays + Tuesdays) carry no budget.
  const closed = v !== 'group' && rev < 300;
  const kitClosed = deptOnly === 'Kitchen' && v === 'stow' && dowDeptForecast(v, dow).food < 150;
  // A ~$0 roster on a day that expects real trade means nobody has rostered it
  // yet — incomplete, not a win. Never true of an actual day.
  const unrostered = mode === 'roster' && !closed && !kitClosed && rev >= 300 && wages < 25;
  // Partly rostered: something IS on the day, but a dept that normally works it
  // isn't. The number is real but incomplete — it must not read as a win, and
  // it must not be totalled.
  const missing = (mode === 'roster' && !closed && !kitClosed && !unrostered) ? missingDepts(v, dIso, dow, deptOnly) : [];
  const budget = target !== null && rev ? target / 100 * rev : null;
  const pct = rev ? wages / rev * 100 : null;
  return { dIso, dow, mode, wages, rev, target, budget, pct, closed, kitClosed, unrostered, noSplit, missing, vens: (STATE.roster.days || {})[dIso] || {} };
}

function renderWaWeek(wkStart, wIdx, v, deptOnly, todayIso) {
  const isos = [];
  for (let i = 0; i < 7; i++) isos.push(isoDate(addDays(wkStart, i)));
  // Next week only appears once Deputy has a roster for it at all — but if the
  // roster exists and THIS dept isn't on it, we still show it, so the gap is
  // visible rather than silent.
  const anyRoster = isos.some(d => {
    const vens = (STATE.roster.days || {})[d];
    if (!vens) return false;
    return (v === 'group' ? ['stow', 'hg', 'mari'] : [v]).some(pk => ((vens[pk] || {}).total || 0) > 0);
  });
  const anyActual = isos.some(d => d < todayIso && (STATE.histories[v] || []).some(r => r.date === d && toNum(r.revenue_ex_gst) > 0));
  if (wIdx > 0 && !anyRoster) return null;
  if (wIdx === 0 && !anyRoster && !anyActual) return null;

  const data = isos.map(d => waDay(v, d, deptOnly, todayIso));
  const cells = data.map(D => waCellHtml(D, v, deptOnly, todayIso)).join('');

  // The total: only days that are actually accounted for. Closed days have no
  // budget; unrostered days aren't a win. Counting either would be a lie.
  let sumW = 0, sumB = 0, sumR = 0, counted = 0, nUnrostered = 0, nPartial = 0, nActual = 0;
  const missingSet = new Set();
  for (const D of data) {
    if (D.closed || D.kitClosed || D.noSplit) continue;
    if (D.unrostered) { nUnrostered++; continue; }
    if (D.missing && D.missing.length) { nPartial++; D.missing.forEach(m => missingSet.add(m)); continue; }
    if (D.budget === null) continue;
    sumW += D.wages; sumB += D.budget; sumR += D.rev; counted++;
    if (D.mode === 'actual') nActual++;
  }
  const label = wIdx === 0 ? 'This week' : 'Next week';
  const range = `${wkStart.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })} – ${addDays(wkStart, 6).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })}`;
  let totHtml;
  const gaps = missingSet.size ? Array.from(missingSet).join(', ') : null;
  // A week total drawn from a minority of its trading days isn't a week total —
  // a big red % off one rostered day would mislead exactly as much as the green
  // $0 did. Below half, report the gap and refuse to publish a number.
  const tradeable = counted + nUnrostered + nPartial;
  const tooThin = counted > 0 && counted * 2 < tradeable;
  if (!counted || tooThin) {
    const why = !counted
      ? (nPartial ? `${gaps} not rostered yet` : `${deptOnly ? deptOnly.toLowerCase() : 'roster'} not rostered yet`)
      : `only ${counted} of ${tradeable} trading days rostered${gaps ? ` — still missing ${gaps}` : ''}`;
    totHtml = `<div class="wa-tot"><span class="wa-tot-pct" style="opacity:.4;">—</span>
      <span class="wa-none">${why} — too little rostered to total the week</span></div>`;
  } else {
    const diff = sumW - sumB;
    const wpct = sumR ? sumW / sumR * 100 : null;
    const tpct = sumR ? sumB / sumR * 100 : null;
    const cls = tpct === null || wpct === null ? '' : wpct > tpct + 2 ? 'bad' : wpct < tpct - 2 ? 'good' : '';
    const verdict = Math.abs(diff) < 25
      ? `<b>on target</b>`
      : diff > 0 ? `<b class="wa-over">${fmtDollars(diff)} over target</b>`
                 : `<b class="wa-under">${fmtDollars(-diff)} under target</b>`;
    const notes = [];
    if (nActual) notes.push(`${nActual} day${nActual > 1 ? 's' : ''} actual, ${counted - nActual} rostered`);
    if (nUnrostered) notes.push(`${nUnrostered} day${nUnrostered > 1 ? 's' : ''} not rostered yet — excluded`);
    if (nPartial) notes.push(`${nPartial} day${nPartial > 1 ? 's' : ''} missing ${gaps} — excluded`);
    totHtml = `<div class="wa-tot ${cls}">
      <span class="wa-tot-pct">${wpct !== null ? fmtPct(wpct) : '—'}</span>
      <span>${fmtDollars(sumW)} of ${fmtDollars(sumB)} budget${tpct !== null ? ` · tgt ${fmtPct(tpct)}` : ''}</span>
      ${verdict}
      ${notes.length ? `<span class="wa-tot-note">${notes.join(' · ')}</span>` : ''}
    </div>`;
  }
  return `<div class="wa-week"><p class="wa-wk">${label} · ${range}</p><div class="wa-row">${cells}</div>${totHtml}</div>`;
}

function waCellHtml(D, v, deptOnly, todayIso) {
  const { dIso, dow, mode, wages, rev, target, budget, pct } = D;
  const vens = D.vens;
  const wd = new Date(dIso).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' });
  const tag = dIso === todayIso ? ' · today' : mode === 'actual' ? ' · actual' : '';
  const head = `<p class="wa-day">${wd}${tag}</p>`;
  if (D.closed) {
    const mis = wages > 100;
    return `<div class="wa-cell ${mis ? 'bad' : ''}">${head}
      <p class="wa-pct" style="opacity:.45;">Closed</p>
      <p class="wa-sub">${mis ? 'but ' + fmtDollars(wages) + ' rostered — check Deputy' : 'no trade expected'}</p>
    </div>`;
  }
  if (D.kitClosed) {
    return `<div class="wa-cell ${wages > 100 ? 'bad' : ''}">${head}
      <p class="wa-pct" style="opacity:.45;">Closed</p>
      <p class="wa-sub">${wages > 100 ? 'but ' + fmtDollars(wages) + ' rostered — check Deputy' : 'kitchen shut'}</p>
    </div>`;
  }
  if (D.noSplit) {
    return `<div class="wa-cell">${head}
      <p class="wa-pct" style="opacity:.4;">—</p>
      <p class="wa-none">awaiting split data</p>
      <p class="wa-sub">took ${fmtDollars(rev)}</p>
    </div>`;
  }
  if (D.unrostered) {
    return `<div class="wa-cell">${head}
      <p class="wa-pct" style="opacity:.4;">—</p>
      <p class="wa-none">${deptOnly ? deptOnly.toLowerCase() : 'roster'} not rostered yet</p>
      <p class="wa-sub">typical rev ${fmtDollars(rev)}</p>
    </div>`;
  }
  // A day missing a dept can still be OVER (that's real — the cut stands), but
  // it can never be "good": the number is only low because the roster is
  // unfinished. Neutralise the green and name the gap.
  const partial = D.missing && D.missing.length > 0;
  // A day cell no longer renders a verdict against target (Zak, 2026-07-17).
  // The colour was the last of the per-day budget: it said "this Wednesday is
  // over" about a number you settle across the payroll week, not on the day.
  // It also lies during the OPEN week — a salaried person's whole weekly cost
  // lands on their first logged shift, so Wed 15 Jul read 185.5% red purely
  // because Renan hadn't worked the rest of his week yet. Nothing was wrong.
  // The week total still carries target vs roster; that's the real decision.
  const cls = '';
  const fcst = rev;
  let dept = '';
  if (!deptOnly && (v === 'stow' || v === 'hg')) {
    // Dept canon (Zak, 2026-07-15): BOTH venues read dept wages against TOTAL
    // venue revenue, and the venue wage target splits across depts by that
    // weekday's own wage mix — dept targets always sum to the venue target, so
    // hitting both = hitting the venue number by construction.
    const hist = (STATE.histories[v] || []).find(r => r.date === dIso);
    let kit, foh;
    if (mode === 'actual' && hist && hasVal(hist.wages_kitchen_dollars)) {
      kit = toNum(hist.wages_kitchen_dollars); foh = toNum(hist.wages_foh_dollars);
    } else if (mode === 'roster') {
      const dd = vens[v] || {};
      kit = dd.Kitchen || 0; foh = dd.FOH || 0;
    }
    if (kit !== undefined) {
      const df2 = dowDeptForecast(v, dow);
      const mix = deptWageShares(v, dow);
      const kT = target !== null && mix ? target * mix.k : null;
      const fT = target !== null && mix ? target * mix.f : null;
      const kp2 = fcst ? kit / fcst * 100 : null;
      const fp2 = fcst ? foh / fcst * 100 : null;
      // Dept lines are informational too — no target verdict (Zak, 2026-07-17).
      const col = () => '';
      const kClosed = v === 'stow' && df2.food < 150;
      const kLine = kClosed
        ? `<b>Kitchen</b> closed${kit > 100 ? ' · but ' + fmtDollars(kit) + ' rostered' : ''}`
        : `<b>Kitchen</b> <span${col(kp2, kT)}>${kp2 !== null ? fmtPct(kp2) : '—'}</span> of total · ${fmtDollars(kit)}`;
      const fLine = `<b>FOH</b> <span${col(fp2, fT)}>${fp2 !== null ? fmtPct(fp2) : '—'}</span> of total · ${fmtDollars(foh)}`;
      dept = `<p class="wa-dept">${kLine}<br>${fLine}</p>`;
    }
  }
  // No per-day budget, target or cut-$X here any more (Zak, 2026-07-16: "the
  // daily wage budget is too noisy"). A day's budget was never a real decision
  // anyway — you don't balance the books on a Tuesday, you balance them over the
  // payroll week, and a quiet Tuesday against a shaped Tuesday target produced a
  // lot of red that meant nothing. The week total carries target vs roster; a
  // cell just shows what's on. The colour went too (2026-07-17) — same reason,
  // and because in the open week it flags salaried allocation artefacts as if
  // they were overspend.
  // The one exception is a MISSING dept — that's not budget noise, it's the
  // roster being unfinished, and it has to stay visible. Likewise a CLOSED day
  // with wages rostered on it stays red: that's a Deputy error, not a verdict.
  const gap = partial ? `<p class="wa-none">${D.missing.join(' + ')} not rostered yet</p>` : '';
  return `<div class="wa-cell ${cls}">${head}
    <p class="wa-pct">${pct !== null ? fmtPct(pct) : '—'}</p>
    <p class="wa-sub">${mode === 'actual' ? 'wages' : 'roster'} ${fmtDollars(wages)}<br>${mode === 'actual' ? 'took' : 'typical rev'} ${rev ? fmtDollars(rev) : '—'}</p>
    ${gap}
    ${dept}
  </div>`;
}

function dhChip(lbl, cls, detail) {
  dhChip._cls.push(cls);
  dhChip._items.push({ lbl, cls, detail });
  return '';
}

function overheadChips() {
  if (STATE.currentVenue !== 'group') return { chips: '', note: '' };
  // Open week = on/after this Monday. Its leave is provisional (salaried
  // timesheets aren't approved yet), so we never offer the leave toggle for a
  // window that touches it — and we flag the provisional leave with a note.
  const openWeekStart = isoDate(weekStart(sydneyToday()));
  let adm = 0, lve = 0, lveOpen = 0, winEndDate = '';
  for (const v of REAL_VENUES) {
    for (const r of rowsForTimeframe(STATE.histories[v] || [], STATE.currentTimeframe, STATE.currentDay)) {
      adm += toNum(r.wages_admin_dollars);
      lve += toNum(r.leave_dollars);
      if (r.date >= openWeekStart) lveOpen += toNum(r.leave_dollars);
      if (r.date > winEndDate) winEndDate = r.date;
    }
  }
  const windowInOpenWeek = winEndDate !== '' && winEndDate >= openWeekStart;
  const chip = (on, label, dollars, fn) =>
    `<button class="oh-chip${on ? ' on' : ''}" onclick="${fn}()">${label} ${fmtDollars(dollars)}</button>`;
  // Always show BOTH toggles on the group view (Zak wants the leave toggle to be
  // dependably present, next to admin — even when the selected period happens to
  // have no leave, in which case it reads $0). Group-only; venue views returned early.
  const parts = [
    chip(STATE.includeAdmin, 'include admin', adm, 'toggleAdmin'),
    chip(STATE.includeLeave, 'include leave', lve, 'toggleLeave'),
  ];
  const notes = [];
  if (parts.length) notes.push("admin and leave aren't rostered against trade");
  if (windowInOpenWeek && lveOpen > 0.005) notes.push('open-week leave is an estimate until timesheets are approved');
  return {
    chips: parts.length ? `<span class="oh-chips">${parts.join('')}</span>` : '',
    note: notes.length ? `<p class="pcard-ohnote">${notes.join(' · ')}</p>` : '',
  };
}

function extractByHour(json, key) {
  const node = key ? (json && json[key]) : json;
  const bh = node && node.by_hour;
  if (!bh) return null;
  const out = {};
  for (const h of Object.keys(bh)) {
    const v = toNum(bh[h].ex_gst);
    if (isFinite(v)) out[parseInt(h, 10)] = v;
  }
  return Object.keys(out).length ? out : null;
}

function hourStrengthColor(rev, labour) {
  if (!(rev > 0)) return '#888780';
  if (!(labour > 0)) return '#1D9E75';
  const share = labour / rev;              // labour as a fraction of that hour's rev
  if (share <= 0.35) return '#1D9E75';     // green — strong
  if (share >= 0.75) return '#E24B4A';     // red — labour-heavy
  return '#E8A82B';                        // amber — middling
}

function hourLabel(h) {
  const hr = ((h % 24) + 24) % 24;
  const ampm = hr < 12 ? 'am' : 'pm';
  const h12 = hr % 12 === 0 ? 12 : hr % 12;
  return h12 + ampm;
}

function pickPhrase(arr, seed) { return arr[Math.abs(seed) % arr.length]; }
