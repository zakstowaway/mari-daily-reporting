"""
Roster-forward wages — pulls TWO PAYROLL WEEKS of rostered shifts from Deputy
and writes data/roster_week.json for the dashboard's week-ahead view.

Same canon as the timesheet pulls (daily_deputy_pull.py):
  - OU -> venue/dept routing via venues.py
  - Monday reallocation: Stow Kitchen -> HarryGatos Kitchen
  - Admin OU worked time 90/10 Stowaway/HarryGatos
  - Salaried synthesis: Deputy roster Cost=0 for salaried staff -> hours x
    (annual/52/40) from salaried_employees.json
  - 12% super gross-up (all figures inc-super, matching wages_dollars)
  - Leave shifts skipped (group overhead, not venue cost)

Output data/roster_week.json:
  { "generated": iso-ts,
    "days": { "YYYY-MM-DD": { "stow": {"Kitchen": x, "FOH": y, "Admin": z, "total": t},
                              "hg": {...}, "mari": {...} } } }

CLI: python scripts/roster_pull.py            # today .. today+6
     python scripts/roster_pull.py 2026-07-20 # that day .. +6
"""
import json, os, sys, urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))   # repo root -> core/
from core import venues as V
from wage_model import allocate_week

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEPUTY_HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")
SALARIED_FILE = Path(__file__).parent / "salaried_employees.json"
SUPER_MULT = 1.0 + V.SUPER_RATE
OFFSET_H = 10  # AEST

if not TOKEN:
    sys.exit("DEPUTY_TOKEN not set")

cfg = json.loads(SALARIED_FILE.read_text())
# SAL kept only as a membership test (is this person salaried?). The per-hour
# rate it used to hold is gone: hours no longer decide a salaried cost.
SAL = {str(k) for k in cfg["employees"]}
SAL_ANNUAL = {str(k): v["annual"] for k, v in cfg["employees"].items()}
WPY = cfg.get("_weeks_per_year", 52)

# Anchor to the PAYROLL WEEK, not "today" (Zak, 2026-07-15). Wages are
# budgeted and reconciled Mon-Sun, so a rolling next-7-days window straddles
# two payroll weeks and can't be totalled against a weekly target. We pull from
# THIS week's Monday and run 14 days, which covers the current payroll week
# (elapsed days included — the dashboard prefers actuals for those, but having
# the roster lets it show a cell before the actuals land) plus all of next week
# as it gets rostered.
anchor = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else \
    datetime.now(timezone(timedelta(hours=OFFSET_H))).date()
start = anchor - timedelta(days=anchor.weekday())   # Monday of anchor's week
end = start + timedelta(days=14)                    # 2 full payroll weeks


def api_post(path, body):
    req = urllib.request.Request(DEPUTY_HOST + path, data=json.dumps(body).encode(),
        headers={"Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


t0 = int(datetime(start.year, start.month, start.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
t1 = int(datetime(end.year, end.month, end.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())

shifts, offset = [], 0
while True:
    batch = api_post("/api/v1/resource/Roster/QUERY", {
        "search": {
            "s1": {"field": "StartTime", "type": "ge", "data": t0},
            "s2": {"field": "StartTime", "type": "lt", "data": t1},
        },
        "join": ["OperationalUnitObject"],
        "start": offset, "max": 500,
    })
    shifts.extend(batch)
    if len(batch) < 500:
        break
    offset += 500
print(f"Deputy returned {len(shifts)} rostered shifts {start} .. {end - timedelta(days=1)}")

days = {}
def add(dstr, ven, dept, cost):
    d = days.setdefault(dstr, {}).setdefault(ven, {})
    d[dept] = d.get(dept, 0.0) + cost

skipped_ous = {}
# Bucket every rostered shift first, THEN cost it a whole payroll week at a
# time. A salaried manager costs annual/52 that week whatever the roster says
# (verified against Xero payroll — see wage_model.py), so their cost cannot be
# derived from one shift in isolation. Rostering them on fewer shifts doesn't
# make them cheaper; it just concentrates the same salary onto those shifts.
# That matters here more than anywhere: this feed is what managers change.
by_week = {}
for s in shifts:
    hours = s.get("TotalTime") or 0
    if not hours:
        continue
    emp = str(s.get("Employee"))
    cost = s.get("Cost") or 0
    if not cost and emp not in SAL:
        continue  # open/unassigned shift with no costing — nothing to count
    ou = (s.get("_DPMetaData", {}).get("OperationalUnitInfo", {}) or {}).get("OperationalUnitName", "")
    dstr = datetime.fromtimestamp(s["StartTime"], tz=timezone(timedelta(hours=OFFSET_H))).date().isoformat()
    d0 = date.fromisoformat(dstr)
    if ou == V.ADMIN_OU_NAME:
        bucket = "admin"
    elif ou == V.MONDAY_REALLOCATED_OU and d0.weekday() == 0:
        bucket = "hg|Kitchen"
    else:
        bucket = None
        for vkey, prefix in (("stowaway", "stow"), ("harry", "hg"), ("marilynas", "mari")):
            dept = V.dept_for_ou(vkey, ou)
            if dept:
                bucket = f"{prefix}|{dept}"
                break
        if not bucket:
            skipped_ous[ou] = skipped_ous.get(ou, 0) + 1
            continue
    wk = (d0 - timedelta(days=d0.weekday())).isoformat()
    by_week.setdefault(wk, []).append(
        {"employee_id": emp, "hours": hours, "cost": cost, "date": dstr, "bucket": bucket})

for wk, wk_shifts in by_week.items():
    costed, warn = allocate_week(wk_shifts, SAL_ANNUAL, WPY)
    for w in warn:
        print(f"  warn {w}")
    for s in costed:
        c, b, dstr = s["cost_final"], s["bucket"], s["date"]
        if b == "admin":
            add(dstr, "stow", "Admin", c * V.ADMIN_SHARES["stowaway"])
            add(dstr, "hg", "Admin", c * V.ADMIN_SHARES["harry"])
        else:
            ven, dept = b.split("|")
            add(dstr, ven, dept, c)

if skipped_ous:
    print("Skipped OUs:", skipped_ous)

out = {"generated": datetime.now(timezone(timedelta(hours=OFFSET_H))).isoformat(timespec="seconds"), "days": {}}
for dstr in sorted(days):
    out["days"][dstr] = {}
    for ven, depts in days[dstr].items():
        grossed = {k: round(v * SUPER_MULT, 2) for k, v in depts.items()}
        grossed["total"] = round(sum(grossed.values()), 2)
        out["days"][dstr][ven] = grossed

(DATA_DIR / "roster_week.json").write_text(json.dumps(out, indent=1))
for dstr, vens in out["days"].items():
    print(dstr, {v: d["total"] for v, d in vens.items()})
print("wrote data/roster_week.json")
