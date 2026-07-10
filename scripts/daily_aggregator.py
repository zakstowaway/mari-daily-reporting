"""
Mari daily aggregator — runs each morning after Insights CSV lands.

Inputs:
  - data/insights_<yyyy-mm-dd>.csv   (Lightspeed Insights daily sales summary)
  - data/deputy_<yyyy-mm-dd>.json    (Deputy API daily wages)

Output:
  - data/mari_daily_<yyyy-mm-dd>.json  (per-day 4-lane rollup with alerts)
  - data/mari_daily_history.csv        (append-only 30-day trailing)

Runs claude-less on GitHub Actions cron.
"""
import csv, json, os, sys
from pathlib import Path
from datetime import date, timedelta, datetime

REPO_ROOT = Path(os.environ.get("REPO_ROOT", "/sessions/sweet-adoring-albattani/mnt/Sales Reports/Daily Reporting"))
DATA_DIR = REPO_ROOT / "data"
BASELINE = REPO_ROOT / "baselines" / "mari_baseline.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ==============================================================
# 1. Determine target date (yesterday by default, or CLI arg)
# ==============================================================
if len(sys.argv) > 1:
    target = date.fromisoformat(sys.argv[1])
else:
    target = date.today() - timedelta(days=1)

print(f"Aggregating for: {target.isoformat()}")

# ==============================================================
# 2. Load Lightspeed Insights CSV
# ==============================================================
insights_file = DATA_DIR / f"insights_{target.isoformat()}.csv"
if not insights_file.exists():
    print(f"⚠️  Insights CSV not found: {insights_file}")
    print("    Will emit alert-only record with 'data_missing' flag")
    lightspeed_data = None
else:
    rows = []
    with insights_file.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    revenue_inc = sum(float(r.get("Revenue_inc_gst") or r.get("Sales") or 0) for r in rows)
    revenue_net = sum(float(r.get("Revenue_net") or r.get("NetRevenue") or 0) for r in rows) or revenue_inc / 1.1
    cogs = sum(float(r.get("COGS") or r.get("Cost") or 0) for r in rows)
    gp = revenue_net - cogs
    category_breakdown = {}
    for r in rows:
        cat = r.get("Category", "Uncategorised")
        category_breakdown.setdefault(cat, {"rev": 0, "cogs": 0, "qty": 0})
        category_breakdown[cat]["rev"] += float(r.get("Revenue_inc_gst") or r.get("Sales") or 0)
        category_breakdown[cat]["cogs"] += float(r.get("COGS") or r.get("Cost") or 0)
        category_breakdown[cat]["qty"] += float(r.get("Qty") or 0)
    uber_eats_rev = 0
    for r in rows:
        pay_type = (r.get("PaymentType") or "").lower()
        if "uber" in pay_type:
            uber_eats_rev += float(r.get("Revenue_inc_gst") or r.get("Sales") or 0)
    lightspeed_data = {"revenue_inc": revenue_inc,"revenue_ex": revenue_net,"cogs": cogs,"gp": gp,"gp_pct": gp/revenue_net*100 if revenue_net else 0,"cogs_pct": cogs/revenue_net*100 if revenue_net else 0,"uber_eats_rev": uber_eats_rev,"category_breakdown": category_breakdown}

deputy_file = DATA_DIR / f"deputy_{target.isoformat()}.json"
if not deputy_file.exists():
    deputy_data = None
else:
    with deputy_file.open() as f:
        d = json.load(f)
    kitchen_cost = sum(t["cost"] for t in d if t.get("dept") == "Kitchen")
    driver_cost = sum(t["cost"] for t in d if t.get("dept") == "Driver")
    total_wages = kitchen_cost + driver_cost
    deputy_data = {"kitchen_wages": kitchen_cost,"driver_wages": driver_cost,"total_wages": total_wages,"kitchen_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Kitchen"),"driver_hours": sum(t.get("hours", 0) for t in d if t.get("dept") == "Driver")}

if lightspeed_data and lightspeed_data.get("uber_eats_rev"):
    uber_commission = lightspeed_data["uber_eats_rev"] / 1.1 * 0.30
else:
    uber_commission = 0

if lightspeed_data:
    rev_ex = lightspeed_data["revenue_ex"]
    cogs_dollars = lightspeed_data["cogs"]
    cogs_pct = cogs_dollars / rev_ex * 100 if rev_ex else 0
    if deputy_data:
        wages_dollars = deputy_data["total_wages"]
        wages_pct = wages_dollars / rev_ex * 100 if rev_ex else 0
        driver_dollars = deputy_data["driver_wages"]
    else:
        wages_dollars = wages_pct = None
        driver_dollars = 0
    delivery_dollars = (driver_dollars or 0) + uber_commission
    delivery_pct = delivery_dollars / rev_ex * 100 if rev_ex else 0
else:
    rev_ex = cogs_dollars = cogs_pct = None
    wages_dollars = wages_pct = None
    delivery_dollars = delivery_pct = None

with BASELINE.open() as f:
    baseline = json.load(f)
targets = baseline["targets_and_alerts"]

def status(v, c):
    if v is None: return "unknown"
    if v >= c["red"]: return "red"
    if v >= c["amber"]: return "amber"
    if v <= c["target"]: return "green"
    return "yellow"

cogs_status = status(cogs_pct, targets["cogs"])
wages_status = status(wages_pct, targets["wages"])
delivery_status = status(delivery_pct, targets["delivery"])

record = {"date": target.isoformat(),"generated_at": datetime.utcnow().isoformat() + "Z","venue": "Marilynas","data_status": {"lightspeed": "ok" if lightspeed_data else "missing","deputy": "ok" if deputy_data else "missing"},"sales": {"revenue_inc_gst": round(lightspeed_data["revenue_inc"], 2) if lightspeed_data else None,"revenue_ex_gst": round(rev_ex, 2) if rev_ex else None,"cogs_dollars": round(cogs_dollars, 2) if cogs_dollars is not None else None,"cogs_pct": round(cogs_pct, 1) if cogs_pct is not None else None,"gp_dollars": round(lightspeed_data["gp"],2) if lightspeed_data else None,"gp_pct": round(lightspeed_data["gp_pct"], 1) if lightspeed_data else None,"uber_eats_revenue": round(lightspeed_data.get("uber_eats_rev", 0), 2) if lightspeed_data else 0},"wages": {"kitchen_dollars": round(deputy_data["kitchen_wages"], 2) if deputy_data else None,"driver_dollars": round(deputy_data["driver_wages"], 2) if deputy_data else None,"total_dollars": round(wages_dollars, 2) if wages_dollars is not None else None,"wages_pct": round(wages_pct, 1) if wages_pct is not None else None},"delivery": {"uber_eats_commission_dollars": round(uber_commission, 2),"own_driver_dollars": round(driver_dollars, 2) if deputy_data else 0,"total_dollars": round(delivery_dollars,2) if delivery_dollars is not None else None,"delivery_pct": round(delivery_pct,1) if delivery_pct is not None else None},"alerts": {"cogs": cogs_status,"wages": wages_status,"delivery": delivery_status},"targets": targets}

out_file = DATA_DIR / f"mari_daily_{target.isoformat()}.json"
with out_file.open("w") as f:
    json.dump(record, f, indent=2)
print(f"Saved {out_file}")

history_file = DATA_DIR / "mari_daily_history.csv"
history_rows = []
if history_file.exists():
    with history_file.open() as f:
        history_rows = list(csv.DictReader(f))
history_rows = [r for r in history_rows if r["date"] != target.isoformat()]
nr = {"date": target.isoformat(),"revenue_ex_gst": record["sales"]["revenue_ex_gst"],"cogs_dollars": record["sales"]["cogs_dollars"],"cogs_pct": record["sales"]["cogs_pct"],"wages_dollars": record["wages"]["total_dollars"],"wages_pct": record["wages"]["wages_pct"],"delivery_dollars": record["delivery"]["total_dollars"],"delivery_pct": record["delivery"]["delivery_pct"],"gp_dollars": record["sales"]["gp_dollars"],"gp_pct": record["sales"]["gp_pct"],"cogs_alert": cogs_status,"wages_alert": wages_status,"delivery_alert": delivery_status}
history_rows.append(nr)
cutoff = target - timedelta(days=60)
history_rows = [r for r in history_rows if date.fromisoformat(r["date"]) > cutoff]
history_rows.sort(key=lambda r: r["date"])
with history_file.open("w", newline="") as f:
    if history_rows:
        w = csv.DictWriter(f, fieldnames=list(nr.keys()))
        w.writeheader()
        w.writerows(history_rows)
print(f"History: {len(history_rows)} rows")
