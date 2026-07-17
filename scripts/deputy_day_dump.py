"""Diagnostic: dump Deputy's raw timesheets for one day, exactly as the daily
pull and the backfill both query them (identical filters), so we can see WHY a
stored wages_dollars disagrees with what Deputy reports now.

Prints per shift: employee, OU, hours, Cost, whether we'd synthesize a salaried
cost, and the approval/timestamp fields that would reveal a late approval.

    python scripts/deputy_day_dump.py 2026-06-25
"""
import json, os, sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))   # repo root -> core/
from core import venues as V, urllib.request

TOKEN = os.environ.get("DEPUTY_TOKEN")
if not TOKEN: sys.exit("DEPUTY_TOKEN not set")
HOST = "https://831d4015123255.au.deputy.com"
OFFSET_H = 10
SUPER_MULT = 1.0 + V.SUPER_RATE
cfg = json.loads((Path(__file__).parent / "salaried_employees.json").read_text())
SAL = {str(k): v["annual"] / cfg.get("_weeks_per_year", 52) / cfg.get("_hours_per_week", 40)
       for k, v in cfg["employees"].items()}

target = date.fromisoformat(sys.argv[1])
t0 = int(datetime(target.year, target.month, target.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
t1 = t0 + 86400

req = urllib.request.Request(HOST + "/api/v1/resource/Timesheet/QUERY",
    data=json.dumps({"search": {
        "s1": {"field": "StartTime", "type": "ge", "data": t0},
        "s2": {"field": "StartTime", "type": "lt", "data": t1},
        "s3": {"field": "IsInProgress", "type": "eq", "data": 0},
        "s4": {"field": "Discarded", "type": "eq", "data": 0}},
        "join": ["OperationalUnitObject"], "max": 500}).encode(),
    headers={"Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"})
rows = json.loads(urllib.request.urlopen(req).read())
print(f"=== {target} — Deputy returned {len(rows)} timesheets ===\n")
print("ALL FIELDS on first record (so we can spot approval/timestamp fields):")
if rows: print("  " + ", ".join(sorted(rows[0].keys())))
print()
tot = {}
for ts in sorted(rows, key=lambda r: r.get("StartTime", 0)):
    ou = (ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {}) or {}).get("OperationalUnitName", "")
    dept = None
    for vkey, pfx in (("stowaway","stow"),("harry","hg"),("marilynas","mari")):
        d = V.dept_for_ou(vkey, ou)
        if d: dept = f"{pfx}/{d}"; break
    if ou == V.ADMIN_OU_NAME: dept = "admin(90/10)"
    hours = ts.get("TotalTime") or 0
    cost = ts.get("Cost") or 0
    rate = SAL.get(str(ts.get("Employee")))
    synth = bool(rate and not cost)
    eff = (hours * rate) if synth else cost
    if dept and dept.startswith("mari"):
        tot[dept] = tot.get(dept, 0) + eff
    print(f"  emp={str(ts.get('Employee')):>5} ou={ou!r:22} dept={str(dept):16} hrs={hours:>6} Cost={cost:>9} synth={str(synth):5} eff={eff:>9.2f}")
    for k in ("Approved","ApprovedBy","TimeApproved","Modified","Created","OnCost","Mealbreak","PayRule"):
        if k in ts and ts[k] not in (None, "", 0):
            print(f"        {k}={ts[k]!r}")
print("\nMARI totals (ex-super / inc-super):")
for k, v in tot.items():
    print(f"  {k:16} ${v:>9.2f}  ->  ${v*SUPER_MULT:>9.2f}")
print(f"  {'TOTAL':16} ${sum(tot.values()):>9.2f}  ->  ${sum(tot.values())*SUPER_MULT:>9.2f}")
