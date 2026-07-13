"""
Backfill historical wages from Deputy into the daily history CSVs.

Why: the wages_weekly feed (and therefore wage history on the dashboard)
only starts 23 Nov 2025. Deputy holds full timesheet history, so this pulls
it week-by-week and fills any history row whose wages_dollars is EMPTY —
it never overwrites wages that came from wages_weekly (payroll-reconciled)
or the live daily pipeline.

Canon applied per shift (same as daily_deputy_pull.py):
  - OU -> venue/dept routing via venues.py (+ Harry's Bar, Driver, Admin 90/10)
  - Monday reallocation: Stow Kitchen -> HarryGatos Kitchen
  - Salaried synthesis: hours x (annual/52/40) when Deputy Cost=0
    (NOTE: uses the CURRENT salaried roster/salaries — historical staff not
    in the roster fall back to Deputy's own Cost, historical salary changes
    are approximated at today's rates)
  - Leave: captured once, on the stow rows' leave_dollars, AL loading 17.5%
    for salaried LeaveRule 1
  - 12% super gross-up on everything

Runs on GitHub Actions (DEPUTY_TOKEN secret). CLI:
  python scripts/backfill_wages_deputy.py 2024-10-20 2025-11-22
"""
import json, os, sys, csv, urllib.request, urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import venues as V

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
DEPUTY_HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")
SALARIED_FILE = Path(__file__).parent / "salaried_employees.json"
SUPER_MULT = 1.0 + V.SUPER_RATE
AL_LOADING = 1.175

if len(sys.argv) < 3:
    sys.exit("usage: backfill_wages_deputy.py <from YYYY-MM-DD> <to YYYY-MM-DD>")
d_from = date.fromisoformat(sys.argv[1])
d_to = date.fromisoformat(sys.argv[2])
if not TOKEN:
    sys.exit("DEPUTY_TOKEN not set")

cfg = json.loads(SALARIED_FILE.read_text())
SAL = {str(k): v["annual"] / cfg.get("_weeks_per_year", 52) / cfg.get("_hours_per_week", 40)
       for k, v in cfg["employees"].items()}

OFFSET_H = 10  # AEST; DST drift of an hour at day boundaries is acceptable
               # for whole-week pulls (shifts start well inside the day)


def api_post(path, body):
    req = urllib.request.Request(DEPUTY_HOST + path, data=json.dumps(body).encode(),
        headers={"Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def fetch_range(start_d, end_d):
    """All timesheets with StartTime in [start_d, end_d)."""
    t0 = int(datetime(start_d.year, start_d.month, start_d.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    t1 = int(datetime(end_d.year, end_d.month, end_d.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    out, offset = [], 0
    while True:
        batch = api_post("/api/v1/resource/Timesheet/QUERY", {
            "search": {
                "s1": {"field": "StartTime", "type": "ge", "data": t0},
                "s2": {"field": "StartTime", "type": "lt", "data": t1},
                "s3": {"field": "IsInProgress", "type": "eq", "data": 0},
                "s4": {"field": "Discarded", "type": "eq", "data": 0},
            },
            "join": ["OperationalUnitObject"],
            "start": offset, "max": 500,
        })
        out.extend(batch)
        if len(batch) < 500:
            return out
        offset += 500


def local_date(epoch):
    return (datetime.fromtimestamp(epoch, tz=timezone(timedelta(hours=OFFSET_H)))).date().isoformat()


# accumulate: day[date][venue][dept] = cost (ex-super)
day = {}
def add(dstr, ven, dept, cost):
    day.setdefault(dstr, {}).setdefault(ven, {}).setdefault(dept, 0.0)
    day[dstr][ven][dept] += cost

cur = d_from
while cur <= d_to:
    wk_end = min(cur + timedelta(days=6), d_to)
    rows = fetch_range(cur, wk_end + timedelta(days=1))
    for ts in rows:
        if ts.get("IsLeave"):
            hours = ts.get("TotalTime") or 0
            cost = ts.get("Cost") or 0
            rate = SAL.get(str(ts.get("Employee")))
            if rate and not cost:
                cost = hours * rate
                if ts.get("LeaveRule") == 1:
                    cost *= AL_LOADING
            add(local_date(ts["StartTime"]), "stow", "Leave", cost)
            continue
        ou = (ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {}) or {}).get("OperationalUnitName", "")
        dstr = local_date(ts["StartTime"])
        hours = ts.get("TotalTime") or 0
        cost = ts.get("Cost") or 0
        rate = SAL.get(str(ts.get("Employee")))
        if rate and not cost:
            cost = hours * rate
        is_monday = date.fromisoformat(dstr).weekday() == 0
        if ou == V.ADMIN_OU_NAME:
            add(dstr, "stow", "Admin", cost * V.ADMIN_SHARES["stowaway"])
            add(dstr, "hg", "Admin", cost * V.ADMIN_SHARES["harry"])
            continue
        if ou == V.MONDAY_REALLOCATED_OU and is_monday:
            add(dstr, "hg", "Kitchen", cost)
            continue
        placed = False
        for vkey, prefix in (("stowaway", "stow"), ("harry", "hg"), ("marilynas", "mari")):
            dept = V.dept_for_ou(vkey, ou)
            if dept:
                add(dstr, prefix, dept, cost)
                placed = True
                break
        # unknown OUs (PH Not Worked etc.) are skipped
    print(f"{cur} .. {wk_end}: {len(rows)} timesheets")
    cur = wk_end + timedelta(days=1)

# ---- merge into history CSVs (fill EMPTY wages only) ----
for prefix in ("stow", "hg", "mari"):
    f = DATA_DIR / f"{prefix}_daily_history.csv"
    rows = list(csv.DictReader(f.open()))
    fields = rows[0].keys() if rows else []
    filled = 0
    for r in rows:
        if r.get("wages_dollars"):
            continue
        d = day.get(r["date"], {}).get(prefix)
        if not d:
            continue
        kit = d.get("Kitchen", 0) * SUPER_MULT
        foh = d.get("FOH", 0) * SUPER_MULT
        drv = d.get("Driver", 0) * SUPER_MULT
        adm = d.get("Admin", 0) * SUPER_MULT
        tot = kit + foh + drv + adm
        if tot <= 0:
            continue
        rev = float(r.get("revenue_ex_gst") or 0)
        r["wages_dollars"] = round(tot, 2)
        r["wages_pct"] = round(tot / rev * 100, 1) if rev else ""
        r["wages_kitchen_dollars"] = round(kit, 2) if prefix != "mari" else ""
        r["wages_foh_dollars"] = round(foh, 2) if prefix != "mari" else ""
        food = float(r.get("food_ex_gst") or 0)
        bev = float(r.get("bev_ex_gst") or 0)
        if prefix != "mari":
            r["wages_kitchen_pct"] = round(kit / food * 100, 1) if food else ""
            r["wages_foh_pct"] = round(foh / bev * 100, 1) if bev else ""
        if prefix == "mari":
            r["delivery_dollars"] = round(drv, 2)
            r["delivery_pct"] = round(drv / rev * 100, 1) if rev else ""
        if prefix == "stow" and "leave_dollars" in r:
            lv = d.get("Leave", 0) * SUPER_MULT
            if lv:
                r["leave_dollars"] = round(lv, 2)
        filled += 1
    with f.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"{prefix}: filled wages on {filled} days")
print("done")
