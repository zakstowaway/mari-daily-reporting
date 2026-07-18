"""How much of the ROSTER actually gets worked? Measure it from Deputy.

THE PROBLEM (found 2026-07-18)
------------------------------
The in-progress week's number leans on the roster for days that haven't happened
yet (today, and the rest of the week). But a roster is a PLAN, and plans run hot:
shifts get trimmed, people leave when it's quiet, a rostered 8h becomes a worked
6.5h. This week's Mon-Fri roster forecast ran 24.5% ABOVE what was actually
worked — every single day over. So a raw roster forecast makes the live week's
wage estimate too high, worst on a big all-roster day.

WHAT THIS DOES
--------------
For the last N complete payroll weeks, pull BOTH Deputy resources over the same
window:
  * Roster    -> PLANNED hours (what was scheduled)
  * Timesheet -> WORKED hours  (what actually happened, approved)
and compute realization = worked / planned, overall and per day-of-week (a quiet
Tuesday realises differently from a Saturday). Salaried staff are excluded — they
cost annual/52 regardless of hours, so their "realization" is meaningless and
would pollute the ratio.

Writes data/roster_realization.json. roster_pull reads it and discounts the
forecast, so the live-week estimate reflects what usually gets WORKED, not what
was optimistically rostered. Same self-improving pattern as the wage calibration:
measured from actuals, refreshed weekly, never a hardcoded guess.

    DEPUTY_TOKEN=... python scripts/measure_roster_realization.py [--weeks 8]
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("REPO_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")
OFFSET_H = 10
if not TOKEN:
    sys.exit("DEPUTY_TOKEN not set")

WEEKS = int(sys.argv[sys.argv.index("--weeks") + 1]) if "--weeks" in sys.argv else 8

cfg = json.loads((Path(__file__).parent / "salaried_employees.json").read_text())
SAL = {str(k) for k in cfg["employees"]}

# Complete payroll weeks only: this Monday is the first day NOT included.
today = datetime.now(timezone(timedelta(hours=OFFSET_H))).date()
this_mon = today - timedelta(days=today.weekday())
end = this_mon                          # exclusive
start = this_mon - timedelta(weeks=WEEKS)


def api(path, t0, t1):
    out, offset = [], 0
    while True:
        req = urllib.request.Request(HOST + path, headers={
            "Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"},
            data=json.dumps({"search": {
                "s1": {"field": "StartTime", "type": "ge", "data": t0},
                "s2": {"field": "StartTime", "type": "lt", "data": t1}},
                "start": offset, "max": 500}).encode())
        batch = json.loads(urllib.request.urlopen(req).read())
        out.extend(batch)
        if len(batch) < 500:
            return out
        offset += 500


t0 = int(datetime(start.year, start.month, start.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
t1 = int(datetime(end.year, end.month, end.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())

roster = api("/api/v1/resource/Roster/QUERY", t0, t1)
sheets = api("/api/v1/resource/Timesheet/QUERY", t0, t1)
print(f"pulled {len(roster)} rostered shifts, {len(sheets)} timesheets  {start} .. {end - timedelta(days=1)}")

planned = defaultdict(float)   # dow -> planned hours (hourly staff only)
worked = defaultdict(float)
p_tot = w_tot = 0.0
for s in roster:
    emp = str(s.get("Employee"))
    if emp in SAL:
        continue                       # salaried: hours don't drive cost
    h = s.get("TotalTime") or 0
    if h <= 0:
        continue
    dow = datetime.fromtimestamp(s["StartTime"], tz=timezone(timedelta(hours=OFFSET_H))).weekday()
    planned[dow] += h
    p_tot += h
for s in sheets:
    emp = str(s.get("Employee"))
    if emp in SAL:
        continue
    h = s.get("TotalTime") or 0
    if h <= 0:
        continue
    dow = datetime.fromtimestamp(s["StartTime"], tz=timezone(timedelta(hours=OFFSET_H))).weekday()
    worked[dow] += h
    w_tot += h

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
overall = (w_tot / p_tot) if p_tot else 1.0
print(f"\n  overall realization (worked/planned, hourly): {overall:.3f}   "
      f"({w_tot:,.0f}h worked / {p_tot:,.0f}h planned)")
print(f"\n  {'dow':4} {'planned':>9} {'worked':>9} {'realize':>8}")
by_dow = {}
for d in range(7):
    p, w = planned[d], worked[d]
    r = (w / p) if p > 30 else overall     # thin days fall back to overall
    by_dow[DOW[d]] = round(r, 3)
    print(f"  {DOW[d]:4} {p:>9,.0f} {w:>9,.0f} {r:>8.3f}")

out = {
    "_comment": "Roster realization = worked hours / planned hours (hourly staff, "
                "salaried excluded). roster_pull multiplies the forecast by this so "
                "the live-week wage estimate reflects what gets WORKED, not what was "
                "rostered. Built by measure_roster_realization.py; refreshed weekly.",
    "_generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "_window": f"{start} .. {end - timedelta(days=1)}",
    "_sample_hours": {"planned": round(p_tot, 1), "worked": round(w_tot, 1)},
    "overall": round(overall, 3),
    "by_dow": by_dow,
}
# Guard: a realization outside a sane band means thin data or a broken pull, not
# a real signal. Clamp and flag rather than ship a wild factor into the P&L.
if not (0.5 <= overall <= 1.05):
    out["_warning"] = (f"overall realization {overall:.3f} is outside 0.50-1.05 — "
                       f"likely too little data or a pull problem. roster_pull will "
                       f"treat a missing/implausible file as 1.0 (no discount).")
    print(f"\n!! {out['_warning']}")

(DATA / "roster_realization.json").write_text(json.dumps(out, indent=1))
print(f"\nwrote {DATA / 'roster_realization.json'}")
