"""
Daily Deputy pull — pulls yesterday's approved timesheets for Marilynas.

Auth: Deputy permanent API token (stored in env DEPUTY_TOKEN).
Endpoint: https://831d4015123255.au.deputy.com/api/v1/resource/Timesheet

Output: data/deputy_<yyyy-mm-dd>.json
"""
import os, sys, json, urllib.request, urllib.parse
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "/sessions/sweet-adoring-albattani/mnt/Sales Reports/Daily Reporting"))
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEPUTY_HOST = "https://831d4015123255.au.deputy.com"
TOKEN = os.environ.get("DEPUTY_TOKEN")

# Marilyna's Operating Unit IDs — populated after first pull (see OU discovery below)
MARI_OU_IDS = {
    "Kitchen": None,
    "Driver": None,
}

def api_get(path, params=None):
    url = DEPUTY_HOST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"OAuth {TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def api_post(path, body):
    url = DEPUTY_HOST + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"OAuth {TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

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
    print("⚠️  DEPUTY_TOKEN env var not set — cannot pull.")
    sys.exit(2)

day_start = int(__import__("time").mktime(target.timetuple()))
day_end = day_start + 86400

query_body = {
    "search": {
        "s1": {"field": "Approved", "type": "eq", "data": 1},
        "s2": {"field": "StartTime", "type": "ge", "data": day_start},
        "s3": {"field": "StartTime", "type": "lt", "data": day_end},
    },
    "join": ["EmployeeObject", "OperationalUnitObject"],
    "max": 500,
}

results = api_post("/api/v1/resource/Timesheet/QUERY", query_body)

records = []
for ts in results:
    ou_name = (ts.get("_DPMetaData", {}).get("OperationalUnitInfo", {}).get("OperationalUnitName")
               or ts.get("OperationalUnit", ""))
    if not ("mari" in str(ou_name).lower() or "marilyna" in str(ou_name).lower()):
        continue

    dept = "Kitchen"
    if "driver" in str(ou_name).lower() or "delivery" in str(ou_name).lower():
        dept = "Driver"
    elif "kitchen" in str(ou_name).lower() or "boh" in str(ou_name).lower():
        dept = "Kitchen"

    emp_info = ts.get("_DPMetaData", {}).get("EmployeeInfo", {})
    records.append({
        "timesheet_id": ts.get("Id"),
        "employee_id": ts.get("Employee"),
        "employee_name": emp_info.get("DisplayName", ""),
        "ou_name": ou_name,
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
print(f"✓ Saved {len(records)} timesheets to {out_file}")
print(f"  Kitchen: ${kitchen_cost:,.2f}")
print(f"  Driver:  ${driver_cost:,.2f}")
