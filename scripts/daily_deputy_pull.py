"""
Daily Deputy pull — pulls yesterday's timesheets for Marilyna's.

In Deputy, Mari is called "Pizza Shop" under Company "Stowaway".
(Confirmed 2026-07-11 via OU discovery.)

Auth: Deputy permanent API token (stored in env DEPUTY_TOKEN).
Endpoint: https://831d4015123255.au.deputy.com/api/v1/resource/Timesheet

Output: data/deputy_<yyyy-mm-dd>.json

CLI:
  python daily_deputy_pull.py                # yesterday, filter for Mari
  python daily_deputy_pull.py 2026-07-10     # specific date
  python daily_deputy_pull.py discover       # dump all OUs to stdout
  python daily_deputy_pull.py 2026-07-10 all # dump all timesheets that day (no Mari filter)
"""
import os, sys, json, urllib.request, urllib.parse, urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

# On GitHub Actions runner, CWD is the repo checkout root.
REPO_ROOT = Path(os.environ.get("REPO_ROOT", "."))
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEPUTY_HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")

# Marilyna's OUs. Exact names as they appear in Deputy for account 831d4015123255.
# Extend this list if new OUs are added (e.g. dedicated Mari driver roster).
MARI_KITCHEN_OUS = {"Pizza Shop"}
MARI_DRIVER_OUS  = set()  # Add here once own-driver OU exists in Deputy

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
    """Dump every OU in the account so we can find Mari's real name."""
    ous = api_get("/api/v1/resource/OperationalUnit")
    print(f"Deputy has {len(ous)} operational units total:")
    for ou in ous:
        print(f"  [{ou.get('Id')}] {ou.get('OperationalUnitName')} "
              f"(Company={ou.get('Company')}, Active={ou.get('Active')})")
    return ous

if len(sys.argv) > 1 and sys.argv[1] == "discover":
    if not TOKEN:
        print("DEPUTY_TOKEN not set")
        sys.exit(2)
    discover_ous()
    sys.exit(0)

# Positional args: [date] [mode]
target = None
capture_all = False
for a in sys.argv[1:]:
    if a == "all":
        capture_all = True
    else:
        try:
            target = date.fromisoformat(a)
        except ValueError:
            pass
if target is None:
    target = date.today() - timedelta(days=1)

if not TOKEN:
    print("DEPUTY_TOKEN env var not set — cannot pull.")
    sys.exit(2)

# Sydney-day boundaries → UTC epoch. July = winter = UTC+10 (AEST).
day_start_dt = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=timezone(timedelta(hours=10)))
day_end_dt = day_start_dt + timedelta(days=1)
day_start = int(day_start_dt.timestamp())
day_end = int(day_end_dt.timestamp())

print(f"Target date: {target.isoformat()} (Sydney)")
print(f"Epoch range: {day_start} to {day_end}")
print(f"Mode: {'ALL timesheets (Mari filter off)' if capture_all else 'Mari only'}")

# Sanity test: hit the /me endpoint first to confirm auth works
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

# Always log the unique OU/Company distribution — useful for spotting new OUs.
ou_counter = Counter()
for ts in results:
    ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
    ou_name = ou_info.get("OperationalUnitName", "")
    company = ou_info.get("CompanyName", "") or ""
    ou_counter[(ou_name, company)] += 1

if ou_counter:
    print("Unique (OU, Company) distribution for this day:")
    for (ou, co), n in sorted(ou_counter.items(), key=lambda kv: -kv[1]):
        marker = "  <- Mari" if ou in MARI_KITCHEN_OUS or ou in MARI_DRIVER_OUS else ""
        print(f"  {n:3d}  OU='{ou}'  Company='{co}'{marker}")

MARI_OUS = MARI_KITCHEN_OUS | MARI_DRIVER_OUS

records = []
for ts in results:
    ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
    ou_name = ou_info.get("OperationalUnitName", "")
    company = ou_info.get("CompanyName", "") or ""

    if not capture_all and ou_name not in MARI_OUS:
        continue

    dept = "Driver" if ou_name in MARI_DRIVER_OUS else "Kitchen"

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
        "hours": (ts.get("TotalTime") or 0) / 3600,
        "cost": ts.get("Cost") or 0,
    })

out_file = DATA_DIR / f"deputy_{target.isoformat()}.json"
with out_file.open("w") as f:
    json.dump(records, f, indent=2)

kitchen_cost = sum(r["cost"] for r in records if r["dept"] == "Kitchen")
driver_cost = sum(r["cost"] for r in records if r["dept"] == "Driver")
print(f"Saved {len(records)} Mari timesheets to {out_file}")
print(f"  Kitchen: ${kitchen_cost:,.2f}")
print(f"  Driver:  ${driver_cost:,.2f}")
