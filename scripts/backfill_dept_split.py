"""
Backfill the Kitchen/FOH/Driver SPLIT from Deputy into the daily history CSVs.

Why: backfill_wages_deputy.py fills rows whose wages_dollars is EMPTY. That
is the wrong tool for a row that HAS a total but no dept split — it skips them.
Mari has 533 such rows (every trading day since Oct 2024): a total, no split,
because the aggregator's split_venue gate threw Mari's splits away at write time.
Stow/HG have a smaller gap (17 and 12) from rows whose total came from the
payroll-reconciled wages_weekly feed rather than the Deputy pipeline.

This fills wages_kitchen/foh/driver_dollars ONLY where they are empty, and NEVER
touches wages_dollars — the reconciled total stays authoritative.

The catch this script exists to measure: where a total came from payroll and the
split comes from Deputy, the two may not agree. Run WITHOUT --write first; it
reports how far Deputy's total drifts from the stored total per row, so we can
decide whether the split is trustworthy before publishing it.

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
  python scripts/backfill_dept_split.py 2024-10-20 2026-07-14            # dry run
  python scripts/backfill_dept_split.py 2024-10-20 2026-07-14 --write    # publish
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

WRITE = "--write" in sys.argv
argv = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(argv) < 2:
    sys.exit("usage: backfill_dept_split.py <from YYYY-MM-DD> <to YYYY-MM-DD> [--write]")
d_from = date.fromisoformat(argv[0])
d_to = date.fromisoformat(argv[1])
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


# ---- merge into history CSVs (fill EMPTY dept splits only) ----
TOL = 2.0   # % drift between Deputy's total and the stored total we'll accept

for prefix in ("stow", "hg", "mari"):
    f = DATA_DIR / f"{prefix}_daily_history.csv"
    rows = list(csv.DictReader(f.open()))
    if not rows:
        continue
    fields = list(rows[0].keys())
    if "wages_driver_dollars" not in fields:
        fields.insert(fields.index("wages_foh_dollars") + 1, "wages_driver_dollars")

    filled = skipped_has = skipped_nodeputy = skipped_notot = 0
    drift_ok = drift_bad = 0
    worst = []
    for r in rows:
        if float(r.get("revenue_ex_gst") or 0) <= 0:
            continue
        if r.get("wages_kitchen_dollars") not in (None, ""):
            skipped_has += 1
            continue
        d = day.get(r["date"], {}).get(prefix)
        if not d:
            skipped_nodeputy += 1
            continue
        kit = d.get("Kitchen", 0) * SUPER_MULT
        foh = d.get("FOH", 0) * SUPER_MULT
        drv = d.get("Driver", 0) * SUPER_MULT
        adm = d.get("Admin", 0) * SUPER_MULT
        tot = kit + foh + drv + adm
        if tot <= 0:
            skipped_notot += 1
            continue
        stored = float(r.get("wages_dollars") or 0)
        # Does Deputy's own total agree with the total already on the row? If it
        # doesn't, the split is describing a different week's worth of work than
        # the number the dashboard shows, and publishing it would make
        # kitchen+foh+driver disagree with wages_dollars.
        if stored:
            drift = (tot - stored) / stored * 100
            if abs(drift) <= TOL:
                drift_ok += 1
            else:
                drift_bad += 1
                worst.append((abs(drift), r["date"], stored, tot))
                continue          # don't publish a split we can't reconcile
        if WRITE:
            r["wages_kitchen_dollars"] = round(kit, 2)
            r["wages_foh_dollars"] = round(foh, 2)
            r["wages_driver_dollars"] = round(drv, 2)
            food = float(r.get("food_ex_gst") or 0)
            bev = float(r.get("bev_ex_gst") or 0)
            r["wages_kitchen_pct"] = round(kit / food * 100, 1) if food else ""
            r["wages_foh_pct"] = round(foh / bev * 100, 1) if bev else ""
        filled += 1

    worst.sort(reverse=True)
    print(f"\n{prefix}:")
    print(f"  would fill split          {filled}")
    print(f"  already had a split       {skipped_has}")
    print(f"  no Deputy data that day   {skipped_nodeputy}")
    print(f"  Deputy total was zero     {skipped_notot}")
    print(f"  reconciled within {TOL}%    {drift_ok}")
    print(f"  DRIFTED > {TOL}% (skipped)  {drift_bad}")
    for dr, dt, stored, tot in worst[:5]:
        print(f"      {dt}  stored ${stored:>9,.2f}  deputy ${tot:>9,.2f}  drift {dr:>6.1f}%")

    if WRITE:
        with f.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})
        print(f"  WROTE {f}")

print("\nDRY RUN — nothing written. Re-run with --write." if not WRITE else "\ndone")
