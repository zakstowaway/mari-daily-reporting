"""
Daily Deputy pull — pulls yesterday's timesheets for a single venue.

Auth: Deputy permanent API token (stored in env DEPUTY_TOKEN).
Endpoint: https://831d4015123255.au.deputy.com/api/v1/resource/Timesheet

Venue config (OU allow-list, dept mapping, file prefix) lives in
scripts/venues.py — edit there to add new venues.

KNOWN LIMITS (2026-07-12):
  - Unapproved timesheets carry Cost=0 in Deputy until the morning
    approval run — the 6am pull races it, so same-morning wages are
    understated. Re-running the aggregator later refreshes them.
  - Salaried staff (Min, Nicola, etc.) always cost $0 on timesheets;
    the weekly report loads salaried wages from salaried_employees.json.
    The daily dashboard does NOT yet — daily wages exclude salaried.

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
print(f"Venue: {venue_key} ({cfg['display_name']})")
print(f"Target date: {target.isoformat()} (Sydney)")
print(f"Mode: {'ALL timesheets (venue filter off)' if capture_all else 'venue only'}")
print(f"Venue OUs: {V.all_ous(venue_key)}")

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
for ts in results:
    ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
    ou_name = ou_info.get("OperationalUnitName", "")
    company = ou_info.get("CompanyName", "") or ""

    if not capture_all and ou_name not in venue_ous:
        continue

    dept = V.dept_for_ou(venue_key, ou_name) or "Kitchen"  # fallback if OU somehow not classified

    emp_info = ts.get("_DPMetaData", {}).get("EmployeeInfo", {})
    records.append({
        "timesheet_id": ts.get("Id"),
        "employee_id": ts.get("Employee"),
        "employee_name": emp_info.get("DisplayName", ""),
        "ou_name": ou_name,
        "company": company,
        "dept": dept,
        "start_time": ts.get("StartTime"),
        "end_time": ts.get("EndTime"),
        # Deputy's Timesheet.TotalTime is DECIMAL HOURS (e.g. 11.5), not
        # seconds — verified against real shifts 2026-07-11.
        "hours": ts.get("TotalTime") or 0,
        "cost": ts.get("Cost") or 0,
    })

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
    print(f"  {dept}: ${dept_costs[dept]:,.2f}  ({dept_hours[dept]:.1f} hours)")
