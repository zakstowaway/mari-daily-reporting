"""
Daily Deputy pull — pulls yesterday's timesheets for a single venue.

Auth: Deputy permanent API token (stored in env DEPUTY_TOKEN).
Endpoint: https://831d4015123255.au.deputy.com/api/v1/resource/Timesheet

Venue config (OU allow-list, dept mapping, file prefix) lives in
scripts/venues.py — edit there to add new venues.

2026-07-12 — aligned with the weekly-report wages pipeline
(build_wages_from_deputy.py + salaried_employees.json):
  - SALARIED SYNTHESIS: Deputy returns Cost=0 for salaried staff (Min,
    Nicola, Kris, ...). Their per-shift cost is now synthesized as
    hours × (annual / 52 / 40) from scripts/salaried_employees.json —
    same model the weekly report uses. (The weekly 40h cap / leave residual
    can't apply on a single day; the Tuesday report remains the payroll
    reconciliation point.)
  - MONDAY REALLOCATION: Stow Kitchen shifts on Mondays are HarryGatos
    Kitchen labour (Stow kitchen closed Mondays, HG rings through that POS).
    The stowaway pull drops them; the harry pull picks them up.
  - ADMIN SPLIT: worked Admin-OU time splits 90% Stowaway / 10% HarryGatos
    (dept 'Admin'). Marilynas gets no admin share.
  - Leave shifts (IsLeave) are skipped — leave is a group-level overhead in
    the weekly canon, never a venue cost.

KNOWN LIMITS:
  - Unapproved timesheets carry Cost=0 in Deputy until the morning
    approval run — the 6am pull races it, so same-morning wages are
    understated for HOURLY staff. Re-running the aggregator later
    refreshes them. (Salaried costs are synthesized, so they're right
    regardless of approval state.)

Output: data/deputy_<prefix>_<yyyy-mm-dd>.json
        + data/deputy_<yyyy-mm-dd>.json (only when venue=marilynas, kept
          for backward compat with the existing daily_pull workflow)

CLI:
  python daily_deputy_pull.py                              # yesterday, Mari
  python daily_deputy_pull.py 2026-07-10                   # specific date, Mari
  python daily_deputy_pull.py --venue stowaway             # yesterday, Stow
  python daily_deputy_pull.py --venue harry 2026-07-10     # specific date, HG
  python daily_deputy_pull.py discover                     # dump every OU
  python daily_deputy_pull.py 2026-07-10 all               # dump every timesheet that day
"""
import os, sys, json, urllib.request, urllib.parse, urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

# scripts/ dir is on sys.path when invoked as `python scripts/daily_deputy_pull.py`
sys.path.insert(0, str(Path(__file__).parent))
import venues as V

# On GitHub Actions runner, CWD is the repo checkout root.
REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEPUTY_HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")

SALARIED_FILE = Path(__file__).parent / "salaried_employees.json"


def load_salaried() -> dict:
    """employee_id (str) -> hourly base rate (annual / 52 / 40)."""
    if not SALARIED_FILE.exists():
        print(f"WARNING: {SALARIED_FILE} missing — salaried shifts will cost $0")
        return {}
    with SALARIED_FILE.open() as f:
        cfg = json.load(f)
    hpw = cfg.get("_hours_per_week", 40)
    wpy = cfg.get("_weeks_per_year", 52)
    return {
        str(eid): {"name": e["name"], "hourly": e["annual"] / wpy / hpw}
        for eid, e in cfg.get("employees", {}).items()
    }


def _do_request(req):
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from {req.full_url}", file=sys.stderr)
        print(f"Response body: {body[:2000]}", file=sys.stderr)
        raise


def api_get(path, params=None):
    url = DEPUTY_HOST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"OAuth {TOKEN}",
        "Content-Type": "application/json",
    })
    return _do_request(req)


def api_post(path, body):
    url = DEPUTY_HOST + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"OAuth {TOKEN}",
        "Content-Type": "application/json",
    })
    return _do_request(req)


def discover_ous():
    """Dump every OU in the account so we can spot new ones."""
    ous = api_get("/api/v1/resource/OperationalUnit")
    print(f"Deputy has {len(ous)} operational units total:")
    for ou in ous:
        print(f"  [{ou.get('Id')}] {ou.get('OperationalUnitName')} "
              f"(Company={ou.get('Company')}, Active={ou.get('Active')})")
    return ous


# --------------------------------------------------------------
# CLI arg parsing — simple positional + --venue flag
# --------------------------------------------------------------
venue_key = "marilynas"
target = None
capture_all = False

args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a == "--venue":
        venue_key = args[i + 1]
        i += 2
        continue
    if a == "discover":
        if not TOKEN:
            print("DEPUTY_TOKEN not set"); sys.exit(2)
        discover_ous()
        sys.exit(0)
    if a == "all":
        capture_all = True
        i += 1
        continue
    try:
        target = date.fromisoformat(a)
    except ValueError:
        pass
    i += 1

if target is None:
    target = date.today() - timedelta(days=1)

if not TOKEN:
    print("DEPUTY_TOKEN env var not set — cannot pull.")
    sys.exit(2)

cfg = V.get(venue_key)
is_monday = target.weekday() == 0
admin_share = V.ADMIN_SHARES.get(venue_key, 0.0)
salaried = load_salaried()
print(f"Venue: {venue_key} ({cfg['display_name']})")
print(f"Target date: {target.isoformat()} (Sydney){' — MONDAY (Stow Kitchen -> HG Kitchen realloc active)' if is_monday else ''}")
print(f"Mode: {'ALL timesheets (venue filter off)' if capture_all else 'venue only'}")
print(f"Venue OUs: {V.all_ous(venue_key)} | admin share: {admin_share} | salaried roster: {len(salaried)}")

# Sydney-day boundaries → UTC epoch. July = winter = UTC+10 (AEST).
day_start_dt = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=timezone(timedelta(hours=10)))
day_end_dt = day_start_dt + timedelta(days=1)
day_start = int(day_start_dt.timestamp())
day_end = int(day_end_dt.timestamp())

# Sanity test: hit /me first to confirm auth works
try:
    me = api_get("/api/v1/me")
    print(f"Auth OK — signed in as {me.get('DisplayName') or me.get('Employee') or 'unknown'}")
except Exception as e:
    print(f"Auth test failed: {e}", file=sys.stderr)
    raise

query_body = {
    "search": {
        "s1": {"field": "StartTime", "type": "ge", "data": day_start},
        "s2": {"field": "StartTime", "type": "lt", "data": day_end},
        "s3": {"field": "IsInProgress", "type": "eq", "data": 0},
        "s4": {"field": "Discarded", "type": "eq", "data": 0},
    },
    "join": ["EmployeeObject", "OperationalUnitObject"],
    "max": 500,
}

results = api_post("/api/v1/resource/Timesheet/QUERY", query_body)
print(f"Deputy returned {len(results)} timesheets total for the day")

# Log the unique OU/Company distribution — useful for spotting new OUs.
ou_counter = Counter()
for ts in results:
    ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
    ou_name = ou_info.get("OperationalUnitName", "")
    company = ou_info.get("CompanyName", "") or ""
    ou_counter[(ou_name, company)] += 1

venue_ous = V.all_ous(venue_key)
if ou_counter:
    print("Unique (OU, Company) distribution for this day:")
    for (ou, co), n in sorted(ou_counter.items(), key=lambda kv: -kv[1]):
        marker = f"  <- {venue_key}" if ou in venue_ous else ""
        print(f"  {n:3d}  OU='{ou}'  Company='{co}'{marker}")

records = []
synth_count = 0
for ts in results:
    ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
    ou_name = ou_info.get("OperationalUnitName", "")
    company = ou_info.get("CompanyName", "") or ""

    if ts.get("IsLeave"):
        # Group-level overhead (weekly canon: Group/Leave row — never a venue
        # cost). Captured ONCE, by the stowaway pull, to avoid triple-counting
        # across the three venue runs. Salaried leave synthesizes at base rate;
        # LeaveRule 1 (Annual Leave) carries the statutory 17.5% loading.
        # Hourly leave keeps Deputy's Cost (loading applied at pay-rule level).
        if venue_key != "stowaway":
            continue
        lv_hours = ts.get("TotalTime") or 0
        lv_cost = ts.get("Cost") or 0
        lv_sal = salaried.get(str(ts.get("Employee")))
        if lv_sal and not lv_cost:
            lv_cost = lv_hours * lv_sal["hourly"]
            if ts.get("LeaveRule") == 1:
                lv_cost *= 1.175
        emp_info = ts.get("_DPMetaData", {}).get("EmployeeInfo", {})
        records.append({
            "timesheet_id": ts.get("Id"), "employee_id": ts.get("Employee"),
            "employee_name": emp_info.get("DisplayName", ""),
            "ou_name": ou_name, "company": company, "dept": "Leave",
            "start_time": ts.get("StartTime"), "end_time": ts.get("EndTime"),
            "hours": round(lv_hours, 4), "cost": round(lv_cost, 2),
            "leave_rule": ts.get("LeaveRule"),
        })
        continue

    scale = 1.0
    if capture_all:
        dept = V.dept_for_ou(venue_key, ou_name) or ou_name or "Kitchen"
    elif ou_name == V.ADMIN_OU_NAME:
        # Worked admin time splits 90/10 Stowaway/HarryGatos.
        if admin_share <= 0:
            continue
        dept = "Admin"
        scale = admin_share
    elif ou_name == V.MONDAY_REALLOCATED_OU and is_monday:
        # Stow Kitchen on a Monday is HarryGatos Kitchen labour.
        if venue_key == "harry":
            dept = "Kitchen"
        else:
            continue   # stowaway (and everyone else) drops it
    elif ou_name in venue_ous:
        if venue_key == "stowaway" and ou_name == V.MONDAY_REALLOCATED_OU and is_monday:
            continue   # unreachable (handled above) but kept for clarity
        dept = V.dept_for_ou(venue_key, ou_name) or "Kitchen"
    else:
        continue

    emp_info = ts.get("_DPMetaData", {}).get("EmployeeInfo", {})
    emp_id = ts.get("Employee")
    # Deputy's Timesheet.TotalTime is DECIMAL HOURS (e.g. 11.5), not
    # seconds — verified against real shifts 2026-07-11.
    hours = ts.get("TotalTime") or 0
    cost = ts.get("Cost") or 0

    # Salaried synthesis: Deputy costs salaried staff at $0.
    #
    # PROVISIONAL ONLY. hours × (annual/52/40) is NOT what a salaried employee
    # costs — they're paid annual/52 whatever they log (verified against Xero;
    # see wage_model.py). But this job runs one day at a time and the true
    # figure needs the whole payroll week, so a day in isolation cannot get it
    # right. rebuild_wages.py restates the current + previous payroll week every
    # morning at 7:15 and is what the history CSV ends up holding.
    #
    # Kept rather than zeroed so this JSON stays a plausible same-day estimate;
    # treat `cost` on a salaried shift as an estimate, not a fact.
    sal = salaried.get(str(emp_id))
    if sal and not cost:
        cost = hours * sal["hourly"]
        synth_count += 1

    records.append({
        "timesheet_id": ts.get("Id"),
        "employee_id": emp_id,
        "employee_name": emp_info.get("DisplayName", ""),
        "ou_name": ou_name,
        "company": company,
        "dept": dept,
        "start_time": ts.get("StartTime"),
        "end_time": ts.get("EndTime"),
        "hours": round(hours * scale, 4),
        "cost": round(cost * scale, 2),
        "salaried_synth": bool(sal and cost),
    })

if synth_count:
    print(f"Synthesized cost for {synth_count} salaried shift(s) from salaried_employees.json")

# Write the prefixed file — new canonical filename.
prefixed_file = DATA_DIR / f"deputy_{cfg['file_prefix']}_{target.isoformat()}.json"
with prefixed_file.open("w") as f:
    json.dump(records, f, indent=2)

# For marilynas, ALSO write the legacy path so the existing daily_pull
# workflow (which reads data/deputy_<date>.json) keeps working during
# the transition. Remove this after the workflow is updated to use the
# prefixed filename.
if venue_key == "marilynas":
    legacy_file = DATA_DIR / f"deputy_{target.isoformat()}.json"
    with legacy_file.open("w") as f:
        json.dump(records, f, indent=2)

# Summary by dept
dept_costs = Counter()
dept_hours = Counter()
for r in records:
    dept_costs[r["dept"]] += r["cost"]
    dept_hours[r["dept"]] += r["hours"]

print(f"Saved {len(records)} {venue_key} timesheets to {prefixed_file}")
for dept in sorted(dept_costs):
    print(f"  {dept}: ${dept_costs[dept]:,.2f}  ({dept_hours[dept]:.1f} hours)  [ex-super]")
