"""Who is working for $0?

rebuild_wages.py books a shift at Deputy's Cost unless the employee is in
salaried_employees.json. A shift with real hours, Cost=0 and no salary record is
labour that costs the business money and appears in no number we hold.

Two ways it happens:
  * an hourly employee with no pay rate configured in Deputy;
  * a SALARIED employee who isn't in salaried_employees.json — including people
    who have since left, whose cost is missing from history forever.

Reports each by name, hours and OU so they can be triaged.

    python scripts/deputy_zero_cost_audit.py 2024-10-21 2026-07-12
"""
import json, os, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import venues as V

TOKEN = os.environ.get("DEPUTY_TOKEN")
if not TOKEN: sys.exit("DEPUTY_TOKEN not set")
HOST = "https://831d4015123255.au.deputy.com"
OFFSET_H = 10
cfg = json.loads((Path(__file__).parent / "salaried_employees.json").read_text())
SAL = set(cfg["employees"])

d_from = date.fromisoformat(sys.argv[1]); d_to = date.fromisoformat(sys.argv[2])

def api(path, body):
    r = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
        headers={"Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())

def fetch(a, b):
    t0 = int(datetime(a.year,a.month,a.day,tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    t1 = int(datetime(b.year,b.month,b.day,tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    out, off = [], 0
    while True:
        r = api("/api/v1/resource/Timesheet/QUERY", {"search":{
            "s1":{"field":"StartTime","type":"ge","data":t0},
            "s2":{"field":"StartTime","type":"lt","data":t1},
            "s3":{"field":"IsInProgress","type":"eq","data":0},
            "s4":{"field":"Discarded","type":"eq","data":0}},
            "join":["OperationalUnitObject","EmployeeObject"],"start":off,"max":500})
        out.extend(r)
        if len(r) < 500: return out
        off += 500

agg = defaultdict(lambda: {"h":0.0,"n":0,"ous":defaultdict(float),"name":"","first":"9999","last":"0000"})
cur = d_from
while cur <= d_to:
    wk = min(cur + timedelta(days=6), d_to)
    for ts in fetch(cur, wk + timedelta(days=1)):
        if ts.get("IsLeave"): continue
        emp = str(ts.get("Employee"))
        hours = ts.get("TotalTime") or 0
        cost = ts.get("Cost") or 0
        if cost or not hours or emp in SAL: continue
        m = ts.get("_DPMetaData", {})
        nm = (m.get("EmployeeInfo", {}) or {}).get("DisplayName", "") or f"emp {emp}"
        ou = (m.get("OperationalUnitInfo", {}) or {}).get("OperationalUnitName", "?")
        d = datetime.fromtimestamp(ts["StartTime"], tz=timezone(timedelta(hours=OFFSET_H))).date().isoformat()
        a = agg[emp]; a["h"] += hours; a["n"] += 1; a["name"] = nm; a["ous"][ou] += hours
        a["first"] = min(a["first"], d); a["last"] = max(a["last"], d)
    cur = wk + timedelta(days=1)

print(f"ZERO-COST SHIFTS {d_from} .. {d_to} — real hours, no rate, not salaried\n")
print(f"  {'employee':<26}{'id':>5}{'shifts':>7}{'hours':>9}   {'first':<11}{'last':<11} main OU")
tot = 0
for e, a in sorted(agg.items(), key=lambda kv: -kv[1]["h"]):
    ou = max(a["ous"].items(), key=lambda kv: kv[1])[0]
    tot += a["h"]
    print(f"  {a['name'][:25]:<26}{e:>5}{a['n']:>7}{a['h']:>9.1f}   {a['first']:<11}{a['last']:<11} {ou}")
print(f"\n  {len(agg)} people, {tot:,.0f} hours booked at $0")
print(f"  at a nominal $40/h inc super that is ~${tot*40:,.0f} of labour missing from history")
