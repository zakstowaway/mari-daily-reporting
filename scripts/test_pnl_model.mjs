/* Node test for the extracted P&L model. Runs pnl.js in a vm context exactly as
   the browser loads it (classic script -> globals), rebuilds STATE from the real
   data/ files, and asserts: (1) internal conservation of every pnlWindow, and
   (2) group revenue == sum of venue revenue for each timeframe. Run:
     node dashboard/_shared/pnl.test.mjs   (from repo root) */
import fs from 'fs';
import vm from 'vm';
import path from 'path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const D = p => fs.existsSync(path.join(ROOT, p)) ? fs.readFileSync(path.join(ROOT, p), 'utf8') : '';

const ctx = vm.createContext({ console, Math, Date, JSON, isNaN, parseFloat, parseInt,
  Number, Object, Array, String, Set, Map, Boolean, RegExp });
vm.runInContext(D('dashboard/_shared/pnl.js'), ctx);
// pull synthesizeGroupHistory (a loader that stays in index.html) into the ctx.
// Uses acorn if present; otherwise the group=Σvenues check is skipped (real-venue
// conservation still runs), so the test needs no npm install to be useful.
let HAVE_GROUP = false;
try {
  const acorn = await import('acorn');
  const html = D('dashboard/sales/index.html');
  const re = /<script(?![^>]*\bsrc=)(?![^>]*type="module")[^>]*>([\s\S]*?)<\/script>/g;
  let m, big=''; while ((m=re.exec(html))) if (m[1].length>big.length) big=m[1];
  const a = acorn.parse(big, {ecmaVersion:2022});
  for (const n of a.body) if (n.type==='FunctionDeclaration' && n.id.name==='synthesizeGroupHistory')
    vm.runInContext(big.slice(n.start,n.end), ctx);
  HAVE_GROUP = true;
} catch (e) { console.log('(acorn unavailable — skipping group=Σvenues, running real-venue conservation only)'); }

// Rebuild STATE from the data files (mirrors index.html's loaders)
const S = { histories:{}, xeroCogs:[], xeroOH:[], baselines:{}, roster:{days:{}},
  uberFees:[], uberDaily:[], uberAds:[], uberDirect:[], oncost:{rate:0,ownerWeekly:0},
  currentVenue:'group', currentTimeframe:'lastweek', currentDay:null, includeAdmin:false, includeLeave:false };
ctx.STATE = S;
const pc = t => {
  if (!t) return [];
  const lines = t.replace(/\r/g,'').split('\n').filter(x=>x.length);
  const cols = lines[0].split(',');
  return lines.slice(1).map(ln => { const cells = ln.split(','); const o={}; cols.forEach((c,i)=>o[c]=cells[i]); return o; });
};
for (const [k,f] of [['stow','stow'],['hg','hg'],['mari','mari']])
  S.histories[k] = pc(D(`data/${f}_daily_history.csv`));
S.xeroCogs = pc(D('data/xero_cogs_weekly.csv'));
S.xeroOH   = pc(D('data/xero_overheads_monthly.csv'));
S.uberDaily= pc(D('data/uber_daily.csv'));
S.uberAds  = pc(D('data/uber_marketing_weekly.csv'));
S.uberDirect=pc(D('data/uber_direct_daily.csv'));
S.uberFees = pc(D('data/uber_fees_weekly.csv'));
try { S.roster = JSON.parse(D('data/roster_week.json')||'{"days":{}}'); } catch{}
try { const o = JSON.parse(D('data/wage_oncosts.json')||'{}'); S.oncost = { rate: ctx.toNum(o.oncost_rate), ownerWeekly: ctx.toNum(o.owner_weekly_inc_super) }; } catch{}
if (HAVE_GROUP) S.histories.group = ctx.synthesizeGroupHistory(S.histories);

let fails = 0, checks = 0;
const approx = (a,b,e=0.02) => Math.abs(a-b) <= e + Math.abs(b)*1e-6;
const TFS = ['day','week','lastweek','month','lastmonth','quarter','thisfy','all'];
const VENUES = HAVE_GROUP ? ['stow','hg','mari','group'] : ['stow','hg','mari'];

for (const tf of TFS) {
  const revByVenue = {};
  for (const v of VENUES) {
    S.currentVenue = v; S.currentTimeframe = tf;
    const rows = S.histories[v] || [];
    const anchor = rows.length ? rows[rows.length-1].date : null;
    S.currentDay = tf==='day' ? anchor : null;
    const day = ctx.rollup(ctx.rowsForTimeframe(rows, tf, anchor));
    const w = day ? ctx.pnlWindow(day, v) : null;
    if (!w) { revByVenue[v]=0; continue; }
    revByVenue[v] = w.rev;
    // (1) conservation
    const expOp = w.rev - w.rev*w.cogsPct/100 - w.wages - w.oh - w.df - w.cp;
    checks++; if (!approx(w.opProfit, expOp, 0.5)) { fails++; console.log(`✗ ${v}/${tf} opProfit ${w.opProfit.toFixed(2)} != ${expOp.toFixed(2)}`); }
    checks++; if (!approx(w.profit, w.opProfit - w.fin, 0.5)) { fails++; console.log(`✗ ${v}/${tf} profit != opProfit-fin`); }
    checks++; if (!isFinite(w.profit)) { fails++; console.log(`✗ ${v}/${tf} profit not finite`); }
  }
  // (2) group revenue == sum of venues (strong cross-module invariant)
  if (HAVE_GROUP) {
    const sum = revByVenue.stow + revByVenue.hg + revByVenue.mari;
    checks++;
    if (!approx(revByVenue.group, sum, 1.0)) { fails++; console.log(`✗ ${tf}: group rev ${revByVenue.group.toFixed(2)} != Σvenues ${sum.toFixed(2)}`); }
    else console.log(`✓ ${tf.padEnd(9)} group $${Math.round(revByVenue.group).toLocaleString()} == Σvenues; conservation holds`);
  } else console.log(`✓ ${tf.padEnd(9)} real-venue conservation holds`);
}
console.log(`\n${checks} checks, ${fails} failures`);
process.exit(fails ? 1 : 0);
