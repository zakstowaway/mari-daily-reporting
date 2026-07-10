"""
End-to-end pipeline test using existing CSV data (no APIs needed).

Simulates what happens each morning:
1. Reads yesterday's data from the existing Marilynas_daily_*.csv
2. Fakes an Insights CSV for that date
3. Fakes Deputy wages by pro-rating the weekly wages
4. Runs the aggregator
5. Confirms output looks sensible

Usage: python scripts/test_pipeline.py [YYYY-MM-DD]
"""
import csv, json, os, sys, subprocess
from pathlib import Path
from datetime import date, timedelta

REPO_ROOT = Path("/sessions/sweet-adoring-albattani/mnt/Sales Reports/Daily Reporting")
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

target = date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2026-07-05")

source_daily = Path("/sessions/sweet-adoring-albattani/mnt/Daily Sales/.drive-staging/Marilynas_daily_2026-H2.csv")
row_found = None
with source_daily.open() as f:
    for row in csv.DictReader(f):
        if row["Date"] == target.isoformat():
            row_found = row
            break

if not row_found:
    print(f"No sales data found for {target}")
    sys.exit(1)

insights_file = DATA_DIR / f"insights_{target.isoformat()}.csv"
with insights_file.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date","Category","Revenue_inc_gst","Discounts","Revenue_net","COGS","GP_dollars","GP_pct","Qty","PaymentType"])
    sales_inc = float(row_found["Sales"])
    sales_ex = sales_inc / 1.1
    cost = float(row_found["Cost"])
    qty = float(row_found["Qty"] or 0)
    for cat, pct, pay in [("Pizza", 0.55, "Card"), ("Pizza", 0.30, "Uber Eats"), ("Sides+Drinks", 0.15, "Card")]:
        cat_inc = sales_inc * pct
        cat_ex = sales_ex * pct
        cat_cogs = cost * pct
        cat_gp = cat_ex - cat_cogs
        w.writerow([target.isoformat(), cat, round(cat_inc,2),0,round(cat_ex,2),round(cat_cogs,2),round(cat_gp,2),round(cat_gp/cat_ex*100,1) if cat_ex else 0,round(qty*pct,0),pay])
print(f"Wrote {insights_file}")

wages_file = Path("/sessions/sweet-adoring-albattani/mnt/Daily Sales/.drive-staging/wages_weekly.csv")
week_kitchen = 0
week_driver = 0
with wages_file.open() as f:
    for row in csv.DictReader(f):
        if row.get("Venue") != "Marilynas":
            continue
        try:
            wk_end = date.fromisoformat(row["WeekEnding"])
        except:
            continue
        wk_start = wk_end - timedelta(days=6)
        if wk_start <= target <= wk_end:
            wages = float(row["TotalWagesIncSuper"] or 0)
            if row["Department"] == "Kitchen":
                week_kitchen = wages
            elif row["Department"] == "Driver":
                week_driver = wages

daily_kitchen = week_kitchen / 7
daily_driver = week_driver / 7
deputy_records = [{"timesheet_id": 1, "employee_name": "Test Kitchen", "dept": "Kitchen", "hours": 8, "cost": daily_kitchen},{"timesheet_id": 2, "employee_name": "Test Driver", "dept": "Driver", "hours": 4, "cost": daily_driver}]
deputy_file = DATA_DIR / f"deputy_{target.isoformat()}.json"
with deputy_file.open("w") as f:
    json.dump(deputy_records, f, indent=2)
print(f"Wrote {deputy_file}")
print(f"  Kitchen daily (weekly / 7): ${daily_kitchen:,.2f}")
print(f"  Driver daily (weekly / 7):  ${daily_driver:,.2f}")

print(f"\n--- Running aggregator for {target} ---")
result = subprocess.run(["python3", str(REPO_ROOT / "scripts" / "daily_aggregator.py"), target.isoformat()],capture_output=True, text=True)
print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    sys.exit(1)

out_file = DATA_DIR / f"mari_daily_{target.isoformat()}.json"
if out_file.exists():
    with out_file.open() as f:
        rec = json.load(f)
    print("\n=== Generated record ===")
    print(json.dumps(rec, indent=2))
else:
    print(f"Output file not found: {out_file}")
    sys.exit(1)
