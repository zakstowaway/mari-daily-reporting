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
// synthesizeGroupHistory now lives in util.js — load it into the same ctx so the
// group=Σvenues invariant can be checked. util.js is pure declarations, so running
// it just defines functions (only synthesizeGroupHistory is exercised here).
let HAVE_GROUP = false;
try { vm.runInContext(D('dashboard/_shared/util.js'), ctx); HAVE_GROUP = typeof ctx.synthesizeGroupHistory === 'function'; }
catch (e) { console.log('(util.js load failed — running real-venue conservation only):', e.message); }

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

// Common anchor so the group==Σvenues check compares the SAME day/window across
// venues. Using each venue's own latest date breaks when a partial daily pull
// leaves their latest dates out of sync (group's latest day vs a venue's older
// latest day is not a real conservation break). Group's history spans the max
// date range, so its latest is the common reference.
const groupRows = S.histories.group || [];
const commonDay = groupRows.length ? groupRows[groupRows.length-1].date : null;
for (const tf of TFS) {
  const revByVenue = {};
  for (const v of VENUES) {
    S.currentVenue = v; S.currentTimeframe = tf;
    S.currentDay = tf==='day' ? commonDay : null;
    const rows = S.histories[v] || [];
    const day = ctx.rollup(ctx.rowsForTimeframe(rows, tf, commonDay));
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
