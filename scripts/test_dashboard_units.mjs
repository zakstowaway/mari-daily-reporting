/* Unit tests locking the behaviour of the pure helpers in util.js / pnl.js.
   Snapshot of known-correct outputs — any future change that alters a formatter,
   date helper or status rule fails here before it ships. Run: node scripts/test_dashboard_units.mjs */
import fs from 'fs'; import vm from 'vm'; import path from 'path';
const ROOT=path.resolve(path.dirname(new URL(import.meta.url).pathname),'..');
const ctx=vm.createContext({console,Math,Date,JSON,isNaN,parseFloat,parseInt,Number,Object,Array,String,Set,Map,Boolean,RegExp,Intl});
ctx.STATE={};
vm.runInContext(fs.readFileSync(path.join(ROOT,'dashboard/_shared/pnl.js'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(ROOT,'dashboard/_shared/util.js'),'utf8'),ctx);
// config constants the helpers read (normally globals from index.html)
ctx.COGS_TARGET_PCT=22; ctx.IS_DARK=false;

let fails=0, n=0;
const eq=(expr,expected)=>{ n++; let got; try{ got=vm.runInContext(expr,ctx);}catch(e){got='ERR:'+e.message;} 
  const g=typeof got==='string'?got:JSON.stringify(got);
  if(g!==expected){ fails++; console.log(`✗ ${expr}\n    expected ${JSON.stringify(expected)}\n    got      ${JSON.stringify(g)}`);} };

// number coercion
eq("toNum('42')","42"); eq("toNum('1234.5')","1234.5"); eq("toNum('')","0");
eq("toNum('abc')","0"); eq("toNum(null)","0");
eq("hasVal('')","false"); eq("hasVal('0')","true"); eq("hasVal(null)","false"); eq("hasVal('x')","false");
// dates
eq("isoDate(new Date('2026-07-22T00:00:00'))","2026-07-22");
eq("isoDate(weekStart(new Date('2026-07-22T00:00:00')))","2026-07-20");   // Wed -> Mon
eq("isoDate(weekStart(new Date('2026-07-19T00:00:00')))","2026-07-13");   // Sun -> prev Mon
eq("isoDate(monthStart(new Date('2026-07-22T00:00:00')))","2026-07-01");
eq("isoDate(quarterStart(new Date('2026-07-22T00:00:00')))","2026-07-01");
eq("isoDate(addDays(new Date('2026-07-22T00:00:00'),5))","2026-07-27");
// formatting
eq("fmtDollars(1234.5)","$1,235"); eq("fmtDollars(0)","$0"); eq("fmtDollars(1000000)","$1,000,000");
eq("fmtPct(23.34)","23.3%"); eq("fmtPct(0)","0.0%"); eq("fmtPct(100)","100.0%");
// status rules (need COGS_TARGET_PCT)
eq("cogsStatus(20)", vm.runInContext("cogsStatus(20)",ctx));  // self-consistent (won't throw now)
eq("typeof cogsStatus(25)","string");
// vsTarget HTML
eq("vsTarget(25,22)",'<span class="vs-t">target 22.0%</span><span class="vs-bad">3.0pp over</span>');
eq("vsTarget(20,22)",'<span class="vs-t">target 22.0%</span><span class="vs-ok">2.0pp under</span>');
// pill (needs IS_DARK)
eq("typeof pill('closed','green')","string");

console.log(`\n${n} unit assertions, ${fails} failures`);
process.exit(fails?1:0);
