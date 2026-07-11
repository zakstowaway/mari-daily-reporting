"""
Daily Deputy pull — pulls yesterday's approved timesheets for Marilynas.

Auth: Deputy permanent API token (stored in env DEPUTY_TOKEN).
Endpoint: https://831d4015123255.au.deputy.com/api/v1/resource/Timesheet

Output: data/deputy_<yyyy-mm-dd>.json
"""
import os, sys, json, urllib.request, urllib.parse, urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

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
    """First-time setup — find Marilyna's Kitchen + Driver OU IDs."""
    ous = api_get("/api/v1/resource/OperationalUnit")
    for ou in ous:
        name = ou.get("OperationalUnitName", "")
        if "marilyna" in name.lower() or "mari" in name.lower():
            print(f"OU {ou['Id']}: {name}")
    return ous

if len(sys.argv) > 1 and sys.argv[1] == "discover":
    discover_ous()
    sys.exit(0)

if len(sys.argv) > 1:
    target = date.fromisoformat(sys.argv[1])
else:
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

# Sanity test: hit the /me endpoint first to confirm auth works
try:
    me = api_get("/api/v1/me")
    print(f"Auth OK — signed in as {me.get('DisplayName') or me.get('Employee') or 'unknown'}")
except Exception as e:
    print(f"Auth test failed: {e}", file=sys.stderr)
    raise

# Deputy Timesheet fields (canonical): Id, Employee, StartTime, EndTime, TotalTime,
# Cost, IsInProgress (0 = clock-off done), IsLeave, Discarded, PayRuleApproval
# (0 = draft, 1 = approved by supervisor, etc.). We want completed + non-discarded
# timesheets in the target date range; then compute Marilynas-only from OU name.
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

print(f"Query body: {json.dumps(query_body)}")

results = api_post("/api/v1/resource/Timesheet/QUERY", query_body)
print(f"Deputy returned {len(results)} timesheets total")

records = []
for ts in results:
    ou_info = ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {})
    ou_name = ou_info.get("OperationalUnitName", "")
    company = ou_info.get("CompanyName", "") or ""

    # Marilyna's timesheet — match on OU or Company name
    if not any("mari" in str(x).lower() or "marilyna" in str(x).lower()
               for x in (ou_name, company)):
        continue

    dept = "Kitchen"
    haystack = f"{ou_name} {company}".lower()
    if "driver" in haystack or "delivery" in haystack:
        dept = "Driver"

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
print(f"Saved {len(records)} Marilynas timesheets to {out_file}")
print(f"  Kitchen: ${kitchen_cost:,.2f}")
print(f"  Driver:  ${driver_cost:,.2f}")
