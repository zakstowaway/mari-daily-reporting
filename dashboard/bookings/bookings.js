/**
 * Bookings module — all logic for /bookings/ (the HTML is a shell).
 *
 * Two layers of auth, deliberately:
 *   1. Supabase gate (shared with every tool) decides who may open the page.
 *      Roles: admin only for now — bookings expose guest phone numbers.
 *   2. The booking engine's bearer token is the REAL auth, verified by the
 *      service on every call ("auth is at the endpoint"). Entered once per
 *      device, kept in localStorage. Never committed — this repo is public.
 *
 * The engine is a live service (stowaway-bookings on Render): availability is
 * a seating-solver run over the whole day, so accepts/refusals/edits must be
 * answered by the server that holds the current state — that's why this page
 * talks to it directly instead of reading committed data/ files.
 */
import { Auth } from '/_shared/auth.js';

const API = 'https://stowaway-bookings.onrender.com';
const TOKEN_KEY = 'stowaway_booking_token';

const $ = (id) => document.getElementById(id);
let DAY = null;
let EDITING = null;
let SEL = null;   // the selected event {date, name, sittings}

// ---------------------------------------------------------------- service io
const svcToken = () => localStorage.getItem(TOKEN_KEY) || '';
const hdrs = () => ({ 'Authorization': 'Bearer ' + svcToken(),
                      'Content-Type': 'application/json' });

async function call(path, opts = {}) {
  const r = await fetch(API + path, { ...opts, headers: { ...hdrs(), ...(opts.headers || {}) } });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || ('HTTP ' + r.status));
  return r;
}

function fmtBooked(iso) {
  if (!iso) return '';
  return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString('en-AU',
    { timeZone: 'Australia/Sydney', day: '2-digit', month: 'short',
      hour: 'numeric', minute: '2-digit' });
}

// ---------------------------------------------------------------- rendering
const T12 = (t) => t.replace('12:00', '12pm').replace('14:00', '2pm').replace('19:00', '7pm');

function rowHtml(b) {
  const covers = b.adults + b.kids;
  const flags = [
    b.dogs ? `<span class="pill dog">${b.dogs > 1 ? b.dogs + ' dogs' : 'dog'}</span>` : '',
    b.kids ? `<span class="pill kid">${b.kids} kid${b.kids > 1 ? 's' : ''}</span>` : '',
    b.babies ? `<span class="pill kid">high chair</span>` : '',
    b.status === 'pending_deposit' ? `<span class="pill">deposit pending</span>` : '',
  ].join('');
  const note = b.notes ? `<span class="bnote">${b.notes}</span> · ` : '';
  return `<div class="brow${b.status === 'pending_deposit' ? ' pending' : ''}">
    <button class="btable${b.pinned_table ? ' pinnedchip' : ''}" data-pick="${b.id}"
      title="${b.pinned_table ? 'pinned — click to change' : 'click to choose a table'}">${b.suggested_table || '—'}</button>
    <div class="bmain">
      <div class="bname">${b.name} ${flags}</div>
      <div class="bsub">${note}${b.phone || ''} · booked ${fmtBooked(b.created_at)}</div>
    </div>
    <div class="bpax">${covers}<small>pax</small></div>
    <div class="bacts">
      <button class="mini" data-edit="${b.id}">edit</button>
      <button class="mini danger" data-cancel="${b.id}">cancel</button>
    </div>
  </div>`;
}

function renderDay(d) {
  // One block per sitting (covers + capacity in its header), rows inside
  // ordered by when they booked. Cancelled collapse at the bottom — they're
  // history, not service.
  const wrap = $('daywrap');
  wrap.innerHTML = '';
  const active = d.bookings.filter(b => b.status !== 'cancelled');
  const cancelled = d.bookings.filter(b => b.status === 'cancelled');

  d.sittings.forEach(t => {
    const rows = active.filter(b => b.time === t)
      .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
    const covers = rows.reduce((n, b) => n + b.adults + b.kids, 0);
    // remaining[t][size] is true/false/null — null means the engine is still
    // proving that size in the background (day view reads cache only). Never
    // show FULL until every size is actually known to not fit.
    const sizes = d.remaining[t] || {};
    const vals = Object.entries(sizes);
    const fitting = vals.filter(([, ok]) => ok === true).map(([s]) => +s);
    const unknown = vals.some(([, ok]) => ok === null || ok === undefined);
    const fit = fitting.length && vals.every(([, ok]) => ok === true)
        ? '<span class="yes">any party fits</span>'
      : fitting.length ? `<span class="yes">room up to ${Math.max(...fitting)}p</span>`
      : unknown ? '<span class="sit-meta">checking capacity…</span>'
      : '<span class="no">FULL</span>';
    const block = document.createElement('div');
    block.className = 'sitting-block';
    block.innerHTML = `<div class="sitting-head">
        <span class="sit-time">${T12(t)}</span>
        <span class="sit-meta">${rows.length} bookings · ${covers} covers</span>
        <span class="sit-fit">${fit}</span>
      </div>` + (rows.map(rowHtml).join('') ||
        '<div class="bsub" style="padding:14px 18px">No bookings yet.</div>');
    wrap.appendChild(block);
  });

  if (cancelled.length) {
    const det = document.createElement('details');
    det.className = 'cancelled-list';
    det.innerHTML = `<summary>${cancelled.length} cancelled</summary>` +
      cancelled.map(b =>
        `<div class="crow">${T12(b.time)} · ${b.name} ×${b.adults + b.kids} · ${b.phone || ''} · ${fmtBooked(b.created_at)}</div>`).join('');
    wrap.appendChild(det);
  }

  wrap.querySelectorAll('[data-edit]').forEach(el =>
    el.addEventListener('click', () => openEdit(el.dataset.edit)));
  wrap.querySelectorAll('[data-cancel]').forEach(el =>
    el.addEventListener('click', () => cancelBooking(el.dataset.cancel)));
  wrap.querySelectorAll('[data-pick]').forEach(el =>
    el.addEventListener('click', () => pickTable(el.dataset.pick, el)));
}

async function pickTable(id, chip) {
  // Swap the chip for a dropdown of every table the engine PROVES this
  // booking could move to (each option = a full day re-solve). Picking one
  // pins it; "auto" hands the choice back to the engine.
  const original = chip.textContent;
  chip.textContent = '…';
  chip.disabled = true;
  let alts;
  try {
    alts = await (await call(`/api/admin/bookings/${id}/alternatives`)).json();
  } catch (e) {
    chip.textContent = original; chip.disabled = false;
    $('status').textContent = 'error: ' + e.message;
    return;
  }
  const sel = document.createElement('select');
  sel.className = 'tblselect';
  const current = alts.pinned || original;
  sel.innerHTML = `<option value="auto">auto — engine picks</option>` +
    alts.options.map(o =>
      `<option value="${o}" ${o === current ? 'selected' : ''}>${o}${o === alts.pinned ? ' (pinned)' : ''}</option>`).join('');
  chip.replaceWith(sel);
  sel.focus();
  let done = false;
  sel.addEventListener('change', async () => {
    done = true;
    try {
      await call(`/api/admin/bookings/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ pinned_table: sel.value }),
      });
    } catch (e) { $('status').textContent = 'move refused: ' + e.message; }
    loadDay();
  });
  sel.addEventListener('blur', () => { if (!done) loadDay(); });
}

// ---------------------------------------------------------------- actions
async function loadDay() {
  $('status').textContent = 'loading…';
  try {
    DAY = await (await call('/api/admin/day/' + SEL.date)).json();
    $('status').textContent = DAY.event +
      (DAY.solvable ? ' · day solves ✓' : ' · ⚠ DAY DOES NOT SOLVE');
    renderDay(DAY);
  } catch (e) {
    if (String(e.message).includes('bad admin token')) {
      localStorage.removeItem(TOKEN_KEY);
      showToken();
    } else {
      $('status').textContent = 'error: ' + e.message;
    }
  }
}

async function cancelBooking(id) {
  if (!confirm('Cancel this booking?')) return;
  await call(`/api/admin/bookings/${id}/cancel`, { method: 'POST' });
  loadDay();
}

function openEdit(id) {
  const b = (DAY?.bookings || []).find(x => x.id === id);
  if (!b) return;
  EDITING = id;
  $('edit_name').textContent = '— ' + b.name;
  $('ed_time').innerHTML = (DAY.sittings || []).map(t =>
    `<option ${t === b.time ? 'selected' : ''}>${t}</option>`).join('');
  $('ed_adults').value = b.adults; $('ed_kids').value = b.kids;
  $('ed_babies').value = b.babies; $('ed_dogs').value = b.dogs;
  $('ed_phone').value = b.phone || ''; $('ed_notes').value = b.notes || '';
  $('editbox').style.display = 'block';
  $('editbox').scrollIntoView({ behavior: 'smooth' });
}

async function saveEdit() {
  try {
    await call(`/api/admin/bookings/${EDITING}`, {
      method: 'PATCH',
      body: JSON.stringify({
        time: $('ed_time').value, adults: +$('ed_adults').value,
        kids: +$('ed_kids').value, babies: +$('ed_babies').value,
        dogs: +$('ed_dogs').value, phone: $('ed_phone').value,
        notes: $('ed_notes').value,
      }),
    });
    $('editbox').style.display = 'none';
    loadDay();
  } catch (e) { $('status').textContent = 'edit refused: ' + e.message; }
}

// New booking (phone/staff entry). Goes to the ADMIN create endpoint: works
// inside the 24h guest cutoff and email is optional — but the seating solver
// still has the final say, so an impossible party is refused with a reason.
function openAdd() {
  $('nb_time').innerHTML = ((DAY && DAY.sittings) || SEL.sittings || []).map(t =>
    `<option value="${t}">${T12(t)}</option>`).join('');
  ['nb_name', 'nb_phone', 'nb_email', 'nb_notes'].forEach(id => { $(id).value = ''; });
  $('nb_adults').value = 2; $('nb_kids').value = 0;
  $('nb_babies').value = 0; $('nb_dogs').value = 0;
  $('editbox').style.display = 'none';
  $('addbox').style.display = 'block';
  $('addbox').scrollIntoView({ behavior: 'smooth' });
  $('nb_name').focus();
}

async function saveNew() {
  if (!$('nb_name').value.trim()) { $('status').textContent = 'name is required'; return; }
  if (($('nb_phone').value || '').replace(/\D/g, '').length < 8) {
    $('status').textContent = 'phone number looks too short'; return;
  }
  try {
    const r = await (await call('/api/admin/bookings', {
      method: 'POST',
      body: JSON.stringify({
        date: SEL.date, time: $('nb_time').value,
        name: $('nb_name').value.trim(),
        phone: $('nb_phone').value.trim(),
        email: $('nb_email').value.trim() || null,
        adults: +$('nb_adults').value, kids: +$('nb_kids').value,
        babies: +$('nb_babies').value, dogs: +$('nb_dogs').value,
        notes: $('nb_notes').value,
      }),
    })).json();
    $('addbox').style.display = 'none';
    $('status').textContent = `booked — ${r.covers} pax at ${T12(r.time)}`;
    loadDay();
  } catch (e) { $('status').textContent = 'booking refused: ' + e.message; }
}

async function downloadRunsheet() {
  const r = await call(`/api/admin/day/${SEL.date}/runsheet`);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = `Stowaway_Runsheet_${SEL.date}.pdf`;
  a.click();
}

// ---------------------------------------------------------------- boot
function showToken() {
  $('tokenbox').style.display = 'block';
  $('main').style.display = 'none';
}

function niceDate(iso) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-AU',
    { weekday: 'long', day: 'numeric', month: 'long' });
}

function selectEvent(ev, card) {
  SEL = ev;
  $('addbox').style.display = 'none';
  $('editbox').style.display = 'none';
  document.querySelectorAll('.event-card').forEach(c => c.classList.remove('sel'));
  if (card) card.classList.add('sel');
  loadDay();
}

async function init() {
  if (!svcToken()) { showToken(); return; }
  $('tokenbox').style.display = 'none';
  $('main').style.display = 'block';
  // No date picker: upcoming open events are cards — pick one.
  try {
    const dates = await (await fetch(API + '/api/dates')).json();
    if (!dates.length) {
      $('eventline').textContent = 'No event open for bookings right now.';
      $('events').innerHTML = '';
      return;
    }
    $('eventline').textContent = 'Pick an event:';
    $('events').innerHTML = '';
    dates.forEach((ev, i) => {
      const card = document.createElement('div');
      card.className = 'event-card' + (i === 0 ? ' sel' : '');
      card.innerHTML = `<h3>${ev.name}</h3>
        <div class="when">${niceDate(ev.date)} · sittings ${ev.sittings.join(' & ')}</div>`;
      card.addEventListener('click', () => selectEvent(ev, card));
      $('events').appendChild(card);
    });
    SEL = dates[0];
    loadDay();
  } catch (e) {
    $('eventline').textContent = 'Booking engine unreachable: ' + e.message;
  }
}

Auth.gate($('gate'), {
  roles: ['admin'],   // guest phone numbers live here — widen deliberately
  onOk: (user) => {
    $('app').style.display = '';
    $('whotop').innerHTML = `<strong>${user.name}</strong>`;
    $('signout').onclick = async (e) => {
      e.preventDefault(); await Auth.logout(); location.href = '/';
    };
    $('savetoken').addEventListener('click', () => {
      localStorage.setItem(TOKEN_KEY, $('svc_token').value.trim());
      init();
    });
    $('addbtn').addEventListener('click', openAdd);
    $('savenewbtn').addEventListener('click', saveNew);
    $('closenewbtn').addEventListener('click', () => { $('addbox').style.display = 'none'; });
    $('runsheetbtn').addEventListener('click', downloadRunsheet);
    $('saveeditbtn').addEventListener('click', saveEdit);
    $('closeeditbtn').addEventListener('click', () => { $('editbox').style.display = 'none'; });
    init();
  },
});
