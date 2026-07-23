/* Behavioural test for the render layer (render.js) — the piece that had no
   coverage. Loads all four dashboard modules + the config from index.html into a
   vm with a lightweight DOM stub, rebuilds STATE from the real data files, then
   drives the real render functions across every venue x timeframe. Asserts:
     - no render call throws
     - the profit card and snapshot title actually get populated
   Catches a broken render (missing global, bad reference, template error) before
   it reaches the browser. Run: node scripts/test_dashboard_render.mjs */
import fs from 'fs';
import vm from 'vm';
import path from 'path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const R = p => fs.readFileSync(path.join(ROOT, p), 'utf8');

// ---- lightweight DOM stub (caches elements by id so we can inspect them) ----
const els = new Map();
function makeEl(id) {
  const e = {
    id, innerHTML: '', textContent: '', value: '', _cls: new Set(),
    style: new Proxy({}, { get: () => '', set: () => true }),
    classList: { add(c){e._cls.add(c);}, remove(c){e._cls.delete(c);}, toggle(c){e._cls.has(c)?e._cls.delete(c):e._cls.add(c);}, contains(c){return e._cls.has(c);} },
    setAttribute(){}, removeAttribute(){}, remove(){}, append(){}, appendChild(){}, prepend(){},
    addEventListener(){}, removeEventListener(){}, focus(){}, blur(){}, click(){},
    querySelector(){return null;}, querySelectorAll(){return [];},
    getContext(){return { canvas:{}, clearRect(){}, save(){}, restore(){}, beginPath(){}, closePath(){}, fill(){}, stroke(){}, arc(){}, moveTo(){}, lineTo(){}, fillRect(){}, fillText(){}, measureText(){return {width:0};}, setLineDash(){}, translate(){}, scale(){} };},
    closest(){return null;}, get children(){return [];}, get parentNode(){return makeEl(id+'_p');},
    get firstChild(){return null;}, get offsetWidth(){return 600;}, get clientWidth(){return 600;},
  };
  return e;
}
function getEl(id){ if(!els.has(id)) els.set(id, makeEl(id)); return els.get(id); }
const documentStub = {
  getElementById: id => getEl(id),
  querySelector: () => null, querySelectorAll: () => [],
  createElement: tag => makeEl('new_'+tag), createElementNS: () => makeEl('svg'),
  addEventListener(){}, body: makeEl('body'), documentElement: makeEl('html'),
  head: makeEl('head'), cookie: '',
};
const ctx = vm.createContext({
  console, Math, Date, JSON, isNaN, parseFloat, parseInt, Number, Object, Array,
  String, Set, Map, Boolean, RegExp, Intl, encodeURIComponent, decodeURIComponent,
  document: documentStub,
  window: {}, localStorage: { getItem:()=>null, setItem(){}, removeItem(){} },
  matchMedia: () => ({ matches:false, addEventListener(){}, addListener(){} }),
  Chart: function(){ return { destroy(){}, update(){}, resize(){} }; },
  fetch: () => Promise.reject(new Error('no fetch in render test')),
  setTimeout: (f)=>{ try{ typeof f==='function'&&f(); }catch{} return 0; }, clearTimeout(){},
  requestAnimationFrame: (f)=>{ return 0; }, cancelAnimationFrame(){},
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
});
ctx.window = ctx; ctx.globalThis = ctx; ctx.Chart.defaults = { font:{}, plugins:{} };

// load the four modules (define all logic globals)
for (const m of ['pnl','util','data','render']) vm.runInContext(R(`dashboard/_shared/${m}.js`), ctx, {filename:m+'.js'});

// eval ONLY the config declarations from index.html's inline script (objects/consts
// the render layer reads: VENUE_CONFIG, ROLE_CONFIG, CARD_DEFS, ...). We skip the
// bootstrap execution (it fetches). STATE is set below from data.
{
  const html = R('dashboard/sales/index.html');
  const re = /<script(?![^>]*\bsrc=)(?![^>]*type="module")[^>]*>([\s\S]*?)<\/script>/g;
  let m, big=''; while ((m=re.exec(html))) if (m[1].length>big.length) big=m[1];
  // grab each top-level `var NAME = ...;` config decl (a simple, robust slice:
  // from a line starting with `var ` up to the matching `;\n` at column 0-ish).
  // Simplest: run the whole inline script but neutralise the trailing bootstrap()
  // call and any addEventListener. We wrap it so a throw doesn't kill the test.
  const neutralised = big
    .replace(/\bbootstrap\s*\(\s*\)\s*;?/g, ';')
    .replace(/document\.addEventListener\([^)]*\)\s*;?/g, ';');
  try { vm.runInContext(neutralised, ctx, {filename:'index-config.js'}); }
  catch (e) { console.log('config eval note:', e.message); }
}

// rebuild STATE from data files (same as the model test)
const pc = t => { if(!t) return []; const L=t.replace(/\r/g,'').split('\n').filter(x=>x.length); const c=L[0].split(','); return L.slice(1).map(ln=>{const cs=ln.split(',');const o={};c.forEach((h,i)=>o[h]=cs[i]);return o;}); };
const S = ctx.STATE || {};
S.histories = S.histories || {}; S.baselines = S.baselines || {}; S.roster = {days:{}};
S.xeroCogs = pc(R('data/xero_cogs_weekly.csv')); S.xeroOH = pc(R('data/xero_overheads_monthly.csv'));
S.uberDaily = pc(R('data/uber_daily.csv')); S.uberAds = pc(R('data/uber_marketing_weekly.csv'));
S.uberDirect = pc(R('data/uber_direct_daily.csv')); S.uberFees = pc(R('data/uber_fees_weekly.csv'));
S.oncost = { rate:0, ownerWeekly:0 }; S.includeAdmin=false; S.includeLeave=false;
for (const v of ['stow','hg','mari']) S.histories[v] = pc(R(`data/${v}_daily_history.csv`));
S.histories.group = ctx.synthesizeGroupHistory(S.histories);
ctx.STATE = S; ctx.CURRENT_ROLE = 'admin'; ctx.CURRENT_USER = { role:'admin', name:'Test' };

// drive the real master render() across venue x timeframe (the true entry point)
let fails=0, calls=0;
const TFS=['day','week','lastweek','month','all'];
const VENUES=['group','stow','hg','mari'];
for (const v of VENUES) for (const tf of TFS) {
  S.currentVenue=v; S.currentTimeframe=tf;
  const rows=S.histories[v]||[]; S.currentDay = tf==='day' ? (rows.length?rows[rows.length-1].date:null) : null;
  els.get('profit-card') && (els.get('profit-card').innerHTML='');
  calls++;
  try { ctx.render(); }
  catch(e){ fails++; console.log(`✗ render() ${v}/${tf}: ${e.message}`); continue; }
  const pcEl = els.get('profit-card');
  const titleEl = els.get('snapshot-title');
  if (!pcEl || pcEl.innerHTML.length < 10) { fails++; console.log(`✗ profit-card empty for ${v}/${tf}`); }
  if (!titleEl || titleEl.textContent.length < 3) { fails++; console.log(`✗ snapshot-title empty for ${v}/${tf}`); }
}
console.log(`\n${calls} render() calls across ${VENUES.length}x${TFS.length} combos, ${fails} failures`);
process.exit(fails?1:0);
