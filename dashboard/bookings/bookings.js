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
function renderCaps(remaining) {
  $('caps').innerHTML = Object.entries(remaining).map(([t, sizes]) =>
    `<div class="cap"><b>${t} — next party fits?</b>` +
    Object.entries(sizes).map(([s, ok]) =>
      `${s}p <span class="${ok ? 'yes' : 'no'}">${ok ? '✓' : '✗'}</span>`).join(' · ') +
    '</div>').join('');
}

function renderDay(d) {
  const rows = d.bookings.map(b => {
    const covers = b.adults + b.kids;
    const pills =
      (b.dogs ? `<span class="pill dog">${b.dogs} DOG</span>` : '') +
      (b.kids ? `<span class="pill kid">${b.kids} KID</span>` : '') +
      (b.babies ? `<span class="pill kid">BABY</span>` : '');
    const cls = b.status === 'cancelled' ? 'cancelled'
              : b.status === 'pending_deposit' ? 'pending' : '';
    const actions = b.status !== 'cancelled'
      ? `<button class="ghost" data-edit="${b.id}">edit</button>
         <button class="warn" data-cancel="${b.id}">cancel</button>` : '';
    return `<tr class="${cls}">
      <td>${b.time}</td><td><b>${b.suggested_table || '—'}</b></td>
      <td>${b.name}</td><td>${covers}</td><td>${pills}</td>
      <td>${b.phone || ''}</td><td>${(b.notes || '').slice(0, 60)}</td>
      <td style="white-space:nowrap">${fmtBooked(b.created_at)}</td>
      <td>${b.status}</td>
      <td style="white-space:nowrap">${actions}</td></tr>`;
  }).join('');
  $('daywrap').innerHTML = `<table><tr><th>Sitting</th><th>Table</th><th>Name</th>
    <th>Pax</th><th>Flags</th><th>Phone</th><th>Notes</th><th>Booked</th><th>Status</th><th></th></tr>${rows}</table>`;
  $('daywrap').querySelectorAll('[data-edit]').forEach(el =>
    el.addEventListener('click', () => openEdit(el.dataset.edit)));
  $('daywrap').querySelectorAll('[data-cancel]').forEach(el =>
    el.addEventListener('click', () => cancelBooking(el.dataset.cancel)));
}

// ---------------------------------------------------------------- actions
async function loadDay() {
  $('status').textContent = 'loading…';
  try {
    DAY = await (await call('/api/admin/day/' + $('date').value)).json();
    $('status').textContent = DAY.event +
      (DAY.solvable ? ' · day solves ✓' : ' · ⚠ DAY DOES NOT SOLVE');
    renderCaps(DAY.remaining);
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

async function downloadRunsheet() {
  const r = await call(`/api/admin/day/${$('date').value}/runsheet`);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = `Stowaway_Runsheet_${$('date').value}.pdf`;
  a.click();
}

async function saveEvent() {
  await call('/api/admin/events', {
    method: 'POST',
    body: JSON.stringify({
      date: $('ev_date').value, name: $('ev_name').value,
      sittings: $('ev_sittings').value.split(',').map(s => s.trim()),
      open: $('ev_open').value === '1',
    }),
  });
  $('status').textContent = 'event saved';
  init();
}

// ---------------------------------------------------------------- boot
function showToken() {
  $('tokenbox').style.display = 'block';
  $('main').style.display = 'none';
}

async function init() {
  if (!svcToken()) { showToken(); return; }
  $('tokenbox').style.display = 'none';
  $('main').style.display = 'block';
  // Event-style: one live date at a time — auto-detect and load it.
  try {
    const dates = await (await fetch(API + '/api/dates')).json();
    if (dates.length) {
      $('date').value = dates[0].date;
      $('eventline').textContent =
        `Live event: ${dates[0].name} · ${dates[0].date} · sittings ${dates[0].sittings.join(' & ')}`;
    } else {
      $('eventline').textContent = 'No event open for bookings right now.';
    }
  } catch (e) {
    $('eventline').textContent = 'Booking engine unreachable: ' + e.message;
  }
  if ($('date').value) loadDay();
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
    $('loadbtn').addEventListener('click', loadDay);
    $('runsheetbtn').addEventListener('click', downloadRunsheet);
    $('saveeditbtn').addEventListener('click', saveEdit);
    $('closeeditbtn').addEventListener('click', () => { $('editbox').style.display = 'none'; });
    $('saveeventbtn').addEventListener('click', saveEvent);
    init();
  },
});
