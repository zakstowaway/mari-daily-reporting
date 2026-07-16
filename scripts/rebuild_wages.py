#!/usr/bin/env python3
"""Rebuild wages from Deputy for whole PAYROLL WEEKS, using the canonical model.

Replaces the per-day synthesis. Two reasons it must work a week at a time:

1. A salaried employee costs annual/52 per WEEK (verified against Xero payroll).
   You cannot know Monday's share of that until you know how much of the week
   they logged — so a per-day pull structurally cannot cost them correctly.
2. Deputy's Cost lands when a timesheet is APPROVED. The 6am pull reads whatever
   is approved at 6am; shifts on 2026-06-25 were approved on 2026-06-30 and were
   never re-read. Rebuilding the week each day picks approvals up for free.

Canon (same as daily_deputy_pull.py / venues.py):
  - OU -> venue/dept routing, incl Harry's Bar, Driver, Admin 90/10 stow/hg
  - Monday reallocation: Stow Kitchen -> HarryGatos Kitchen
  - 12% super gross-up on everything
  - Leave: hourly leave keeps Deputy's cost (+17.5% AL loading on LeaveRule 1)
    and lands in stow's leave_dollars. SALARIED leave is already inside the
    annual/52 (Xero: Kris = 38.5h worked + 1.5h leave = 40 units), so it is
    allocated out of the weekly salary, never added on top.

Writes wages_dollars, wages_kitchen/foh/driver_dollars and their pcts. Only
touches days Deputy actually returned shifts for — a day with no Deputy data
keeps whatever it had, so this can't silently blank pre-Deputy history.

    python scripts/rebuild_wages.py 2024-10-21 2026-07-12            # dry run
    python scripts/rebuild_wages.py 2024-10-21 2026-07-12 --write
"""
import csv, json, os, sys, urllib.request, urllib.error
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import venues as V
from wage_model import allocate_week

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
DEPUTY_HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")
SUPER_MULT = 1.0 + V.SUPER_RATE
AL_LOADING = 1.175
OFFSET_H = 10

WRITE = "--write" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) < 2:
    sys.exit("usage: rebuild_wages.py <from YYYY-MM-DD> <to YYYY-MM-DD> [--write]")
if not TOKEN:
    sys.exit("DEPUTY_TOKEN not set")

cfg = json.loads((Path(__file__).parent / "salaried_employees.json").read_text())
SAL = {k: v["annual"] for k, v in cfg["employees"].items()}
WPY = cfg["_weeks_per_year"]

# Xero is truth for any week payroll has posted. The salaried model is only an
# ESTIMATE standing in for it — good for the open week, unnecessary for a closed
# one. Using the real figure also fixes what no config could:
#   * staff who moved between hourly and salaried mid-tenure (Gabriel Choi ran
#     hourly for 5 weeks, then $75k salaried for 11, then part weeks — a flat
#     annual would have overstated him ~15%);
#   * 13 departed staff (5,232 hours) whose cost was missing entirely because
#     Xero's Employees endpoint only lists ACTIVE people;
#   * part weeks, leave, loading and allowances, for free.
XERO_PAY = {}
EMP_MAP = {}
_xp = DATA_DIR / "xero_pay_weekly.json"
_em = DATA_DIR / "employee_map.json"
if _xp.exists() and _em.exists():
    XERO_PAY = json.loads(_xp.read_text())
    EMP_MAP = json.loads(_em.read_text())
    print(f"Xero pay: {len(XERO_PAY)} employees, map: {len(EMP_MAP)} Deputy ids")
else:
    print("WARNING: no Xero pay data — falling back to the salaried estimate everywhere")

d_from = date.fromisoformat(args[0]); d_to = date.fromisoformat(args[1])
d_from -= timedelta(days=d_from.weekday())          # back to Monday
d_to += timedelta(days=6 - d_to.weekday())          # out to Sunday
print(f"payroll weeks {d_from} .. {d_to}  ({(d_to - d_from).days + 1} days)")


def api_post(path, body):
    r = urllib.request.Request(DEPUTY_HOST + path, data=json.dumps(body).encode(),
        headers={"Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


def fetch(start_d, end_d):
    t0 = int(datetime(start_d.year, start_d.month, start_d.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    t1 = int(datetime(end_d.year, end_d.month, end_d.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    out, off = [], 0
    while True:
        b = api_post("/api/v1/resource/Timesheet/QUERY", {"search": {
            "s1": {"field": "StartTime", "type": "ge", "data": t0},
            "s2": {"field": "StartTime", "type": "lt", "data": t1},
            "s3": {"field": "IsInProgress", "type": "eq", "data": 0},
            "s4": {"field": "Discarded", "type": "eq", "data": 0}},
            "join": ["OperationalUnitObject"], "start": off, "max": 500})
        out.extend(b)
        if len(b) < 500:
            return out
        off += 500


def fetch_roster(start_d, end_d):
    """Rostered (planned) shifts — same shape as fetch(), different endpoint."""
    t0 = int(datetime(start_d.year, start_d.month, start_d.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    t1 = int(datetime(end_d.year, end_d.month, end_d.day, tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
    out, off = [], 0
    while True:
        b = api_post("/api/v1/resource/Roster/QUERY", {"search": {
            "s1": {"field": "StartTime", "type": "ge", "data": t0},
            "s2": {"field": "StartTime", "type": "lt", "data": t1}},
            "join": ["OperationalUnitObject"], "start": off, "max": 500})
        out.extend(b)
        if len(b) < 500:
            return out
        off += 500


def local_date(e):
    return datetime.fromtimestamp(e, tz=timezone(timedelta(hours=OFFSET_H))).date().isoformat()


def bucket_for(ou, dstr):
    """OU -> "<venue>|<dept>" / "admin". None = not ours (PH Not Worked etc.)."""
    if ou == V.ADMIN_OU_NAME:
        return "admin"
    if ou == V.MONDAY_REALLOCATED_OU and date.fromisoformat(dstr).weekday() == 0:
        return "hg|Kitchen"
    for vkey, pfx in (("stowaway", "stow"), ("harry", "hg"), ("marilynas", "mari")):
        dept = V.dept_for_ou(vkey, ou)
        if dept:
            return f"{pfx}|{dept}"
    return None


# Sydney's today, not the runner's. Actions runs in UTC, where "today" is
# yesterday for most of our trading day — a UTC date here would treat the live
# week as closed every morning and skip the roster stand-in entirely.
today = datetime.now(timezone(timedelta(hours=OFFSET_H))).date()

day = defaultdict(lambda: defaultdict(float))      # day[date][f"{venue}|{dept}"] = ex-super cost
warnings, weeks = [], 0
xero_weeks = est_weeks = 0
cur = d_from
while cur <= d_to:
    wk_end = cur + timedelta(days=6)
    shifts = []
    for ts in fetch(cur, wk_end + timedelta(days=1)):
        dstr = local_date(ts["StartTime"])
        emp = str(ts.get("Employee"))
        hours = ts.get("TotalTime") or 0
        cost = ts.get("Cost") or 0
        if ts.get("IsLeave"):
            # Salaried leave is inside annual/52 -> let the model allocate it.
            # Hourly leave is a real extra cost at Deputy's rate (+AL loading).
            if emp not in SAL and ts.get("LeaveRule") == 1:
                cost *= AL_LOADING
            shifts.append({"employee_id": emp, "hours": hours, "cost": cost,
                           "date": dstr, "bucket": "leave"})
            continue
        ou = (ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {}) or {}).get("OperationalUnitName", "")
        if ou == V.ADMIN_OU_NAME:
            bucket = "admin"
        elif ou == V.MONDAY_REALLOCATED_OU and date.fromisoformat(dstr).weekday() == 0:
            bucket = "hg|Kitchen"
        else:
            bucket = None
            for vkey, pfx in (("stowaway", "stow"), ("harry", "hg"), ("marilynas", "mari")):
                dept = V.dept_for_ou(vkey, ou)
                if dept:
                    bucket = f"{pfx}|{dept}"
                    break
            if not bucket:
                continue                            # unknown OU (PH Not Worked etc.)
        shifts.append({"employee_id": emp, "hours": hours, "cost": cost,
                       "date": dstr, "bucket": bucket})

    # ---- open week: fill the rest of the week from the ROSTER (2026-07-17) ----
    # A salaried person costs annual/52 for the WHOLE week, and allocate_week
    # spreads that across the shifts we hand it. Hand it only the shifts logged
    # SO FAR and the whole week's salary lands on them:
    #
    #   Mon 13 Jul — Steph Kunde logged one 6.25h shift. Stow took $1,578 that
    #   day. Her entire $1,568 week was booked against it. Read ~99% wages.
    #   Wed 15 Jul — Renan's only logged shift was 8h at Mari; $1,615 of his
    #   $1,442 week landed there. Mari read 185.5%.
    #
    # Neither was overspend — the week just hadn't happened yet. It self-corrects
    # as shifts land, and closed weeks are re-costed from Xero anyway, so the
    # artefact never reaches history: it lives only in the live view, Mon->Sat.
    # That's why it went unnoticed. It is not cosmetic — the day feeds the week
    # strip, where elapsed days read as actuals and future days read as roster,
    # so a salaried person was ALSO counted again in the roster half.
    #
    # Fix: for any day of this week we have no timesheets for, take the ROSTER as
    # a stand-in so the denominator is the whole week. The rostered days are only
    # there to size the shares — they are dropped before anything is written
    # (`_roster`), so we never book cost against a day that hasn't happened.
    # Elapsed days then carry their true share from the first night, and the
    # actual/roster seam sums to exactly annual/52 instead of overlapping.
    #
    # Closed weeks are untouched: Xero pays them, and every day has timesheets so
    # there is nothing for the roster to stand in for.
    roster_shifts = []
    logged_dates = {s["date"] for s in shifts}
    if wk_end >= today:
        for rs in fetch_roster(cur, wk_end + timedelta(days=1)):
            hours = rs.get("TotalTime") or 0
            if not hours:
                continue
            emp = str(rs.get("Employee"))
            if emp not in SAL:
                continue          # hourly roster is not cost we can claim yet
            dstr = local_date(rs["StartTime"])
            if dstr in logged_dates:
                continue          # the day happened — its timesheets are truth
            ou = (rs.get("_DPMetaData", {}).get("OperationalUnitInfo", {}) or {}).get("OperationalUnitName", "")
            b = bucket_for(ou, dstr)
            if not b:
                continue
            roster_shifts.append({"employee_id": emp, "hours": hours, "cost": 0,
                                  "date": dstr, "bucket": b, "_roster": True})
        if roster_shifts:
            rd = sorted({s["date"] for s in roster_shifts})
            print(f"  open week {cur}..{wk_end}: {len(roster_shifts)} rostered salaried shifts "
                  f"stand in for {len(rd)} unworked day(s) ({rd[0]}..{rd[-1]}) to size the split")

    # Prefer what payroll actually paid for this week over any estimate.
    # Allocated pro-rata across the shifts the person logged, so hours still
    # decide WHERE the money lands — Xero decides how much.
    wk_key = wk_end.isoformat()
    paid_this_week = {}
    if XERO_PAY:
        for s in shifts:
            eid = str(s["employee_id"])
            xn = EMP_MAP.get(eid)
            if xn and wk_key in XERO_PAY.get(xn, {}):
                paid_this_week[eid] = XERO_PAY[xn][wk_key]
    if paid_this_week:
        by_emp = defaultdict(list)
        for s in shifts:
            by_emp[str(s["employee_id"])].append(s)
        costed, warn = [], []
        rest = []
        for eid, group in by_emp.items():
            if eid not in paid_this_week:
                rest.extend(group)
                continue
            th = sum((g.get("hours") or 0) for g in group)
            if th <= 0:
                continue                     # paid, but clocked nothing to attribute
            for g in group:
                costed.append({**g, "cost_final": paid_this_week[eid] * (g.get("hours") or 0) / th})
        # Roster stand-ins only help the ESTIMATE. Anyone Xero has paid is costed
        # from the payslip across the shifts they actually logged — a planned
        # shift must never absorb a share of real money.
        rest.extend(r for r in roster_shifts if r["employee_id"] not in paid_this_week)
        c2, warn = allocate_week(rest, SAL, WPY)
        costed.extend(c2)
        xero_weeks += len(paid_this_week)
    else:
        costed, warn = allocate_week(shifts + roster_shifts, SAL, WPY)
    est_weeks += len({str(s["employee_id"]) for s in shifts}) - len(paid_this_week)
    warnings.extend(warn)
    for s in costed:
        if s.get("_roster"):
            continue          # sized the split; the day hasn't happened. Never booked.
        c, b, d = s["cost_final"], s["bucket"], s["date"]
        if b == "leave":
            day[d]["stow|Leave"] += c
        elif b == "admin":
            day[d]["stow|Admin"] += c * V.ADMIN_SHARES["stowaway"]
            day[d]["hg|Admin"] += c * V.ADMIN_SHARES["harry"]
        else:
            day[d][b] += c
    weeks += 1
    cur = wk_end + timedelta(days=1)

print(f"fetched {weeks} weeks, {len(day)} days with shifts")
print(f"employee-weeks costed from XERO actuals: {xero_weeks} | from the estimate: {est_weeks}")

# ---- write ----
for pfx in ("stow", "hg", "mari"):
    f = DATA_DIR / f"{pfx}_daily_history.csv"
    rows = list(csv.DictReader(f.open()))
    if not rows:
        continue
    fields = list(rows[0].keys())
    if "wages_driver_dollars" not in fields:
        fields.insert(fields.index("wages_foh_dollars") + 1, "wages_driver_dollars")
    touched = 0
    delta = 0.0
    for r in rows:
        d = r["date"]
        if d not in day or not (args[0] <= d <= args[1]):
            continue
        b = day[d]
        kit = b.get(f"{pfx}|Kitchen", 0) * SUPER_MULT
        foh = b.get(f"{pfx}|FOH", 0) * SUPER_MULT
        drv = b.get(f"{pfx}|Driver", 0) * SUPER_MULT
        adm = b.get(f"{pfx}|Admin", 0) * SUPER_MULT
        tot = kit + foh + drv + adm
        if tot <= 0:
            continue
        before = float(r.get("wages_dollars") or 0)
        delta += tot - before
        rev = float(r.get("revenue_ex_gst") or 0)
        food = float(r.get("food_ex_gst") or 0)
        bev = float(r.get("bev_ex_gst") or 0)
        r["wages_dollars"] = round(tot, 2)
        r["wages_pct"] = round(tot / rev * 100, 1) if rev else ""
        r["wages_kitchen_dollars"] = round(kit, 2)
        r["wages_foh_dollars"] = round(foh, 2)
        r["wages_driver_dollars"] = round(drv, 2)
        r["wages_kitchen_pct"] = round(kit / food * 100, 1) if food else ""
        r["wages_foh_pct"] = round(foh / bev * 100, 1) if bev else ""
        if pfx == "mari":
            r["delivery_dollars"] = round(drv, 2)
            r["delivery_pct"] = round(drv / rev * 100, 1) if rev else ""
        if pfx == "stow":
            lv = b.get("stow|Leave", 0) * SUPER_MULT
            r["leave_dollars"] = round(lv, 2) if lv else ""
        touched += 1
    print(f"  {pfx}: {touched} days, wages {delta:+,.0f}")
    if WRITE:
        with f.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

zc = [w for w in warnings if w["type"] == "zero_cost_shift"]
nh = [w for w in warnings if w["type"] == "salaried_no_hours"]
if zc:
    agg = defaultdict(lambda: [0.0, 0])
    for w in zc:
        agg[w["employee_id"]][0] += w["hours"] or 0
        agg[w["employee_id"]][1] += 1
    print(f"\n  {len(zc)} zero-cost shifts (real hours, no rate, not salaried) — labour booked at $0:")
    for e, (h, n) in sorted(agg.items(), key=lambda kv: -kv[1][0])[:10]:
        print(f"    employee {e}: {n} shifts, {h:.1f}h")
if nh:
    print(f"\n  {len(nh)} salaried-weeks with no logged hours (paid, unattributable):")
    for w in nh[:10]:
        print(f"    employee {w['employee_id']}: ${w['week_cost']:,.2f}")
print("\nDRY RUN — nothing written. Re-run with --write." if not WRITE else "\nwritten")
