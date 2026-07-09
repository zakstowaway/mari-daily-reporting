"""
Compute Mari's 8-week baseline from existing CSV data.

Sources:
- /Daily Sales/.drive-staging/Marilynas_daily_2026-H2.csv  → daily revenue + COGS $ + GP%
- /Daily Sales/.drive-staging/wages_weekly.csv             → weekly wages by department

Output:
- /Sales Reports/Daily Reporting/baselines/mari_baseline.json
- /Sales Reports/Daily Reporting/baselines/mari_baseline.md  (human-readable)
"""
import csv, json, os
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

DAILY_FILES = [
    Path("/sessions/sweet-adoring-albattani/mnt/Daily Sales/.drive-staging/Marilynas_daily_2026-H1.csv"),
    Path("/sessions/sweet-adoring-albattani/mnt/Daily Sales/.drive-staging/Marilynas_daily_2026-H2.csv"),
]
WAGES = Path("/sessions/sweet-adoring-albattani/mnt/Daily Sales/.drive-staging/wages_weekly.csv")
OUT_DIR = Path("/sessions/sweet-adoring-albattani/mnt/Sales Reports/Daily Reporting/baselines")
OUT_DIR.mkdir(parents=True, exist_ok=True)

today = date(2026, 7, 9)
window_start = today - timedelta(days=56)

daily_rows = []
for daily_file in DAILY_FILES:
    if not daily_file.exists():
        continue
    with daily_file.open() as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["Date"])
            except Exception:
                continue
            if d < window_start or d >= today:
                continue
            sales = float(row["Sales"] or 0)
            cost = float(row["Cost"] or 0)
            gp_pct = float(row["GP_pct"] or 0)
            daily_rows.append({"date": d, "sales": sales, "cost": cost, "gp_pct": gp_pct})

print(f"Loaded {len(daily_rows)} daily records")

total_rev_inc = sum(r["sales"] for r in daily_rows)
total_cogs = sum(r["cost"] for r in daily_rows)
total_rev_ex = total_rev_inc / 1.1
cogs_pct_ex = total_cogs / total_rev_ex * 100

# Wages
wages_rows = []
with WAGES.open() as f:
    for row in csv.DictReader(f):
        if row.get("Venue") != "Marilynas":
            continue
        try:
            wk_end = date.fromisoformat(row["WeekEnding"])
        except Exception:
            continue
        if wk_end < window_start or wk_end >= today:
            continue
        wages_rows.append({
            "dept": row.get("Department", ""),
            "takings_ex": float(row.get("TakingsExGST") or 0) if row.get("TakingsExGST") else 0,
            "wages": float(row.get("TotalWagesIncSuper") or 0) if row.get("TotalWagesIncSuper") else 0,
        })

venue_total = [r for r in wages_rows if r["dept"] == "Venue Total"]
kitchen = [r for r in wages_rows if r["dept"] == "Kitchen"]
driver = [r for r in wages_rows if r["dept"] == "Driver"]

total_wages_venue = sum(r["wages"] for r in venue_total)
total_wages_kitchen = sum(r["wages"] for r in kitchen)
total_wages_driver = sum(r["wages"] for r in driver)
total_takings_ex = sum(r["takings_ex"] for r in venue_total)

wages_pct_venue = total_wages_venue / total_takings_ex * 100 if total_takings_ex else 0
wages_pct_kitchen = total_wages_kitchen / total_takings_ex * 100 if total_takings_ex else 0
wages_pct_driver = total_wages_driver / total_takings_ex * 100 if total_takings_ex else 0

cogs_target = cogs_pct_ex - 3
cogs_amber = cogs_pct_ex
cogs_red = cogs_pct_ex + 2
wages_target = 30.0
delivery_target = 6.0
oh_target = 25.0

baseline = {
    "venue": "Marilynas",
    "computed_on": today.isoformat(),
    "window": {"start": window_start.isoformat(), "end": (today - timedelta(days=1)).isoformat(), "days": 56},
    "sales_8w": {"revenue_inc_gst": round(total_rev_inc ,2), "revenue_ex_gst": round(total_rev_ex,2), "cogs_dollars": round(total_cogs,2), "cogs_pct_of_ex_gst": round(cogs_pct_ex,2)},
    "wages_8w": {"venue_total": round(total_wages_venue,2), "kitchen": round(total_wages_kitchen,2), "driver": round(total_wages_driver,2), "wages_pct_venue": round(wages_pct_venue,2)},
    "targets_and_alerts": {
        "cogs": {"target": round(cogs_target,1), "amber": round(cogs_amber,1), "red": round(cogs_red,1)},
        "wages": {"target": wages_target, "amber": 32.0, "red": 34.0},
        "delivery": {"target": delivery_target, "amber": 8.0, "red": 10.0},
        "overheads": {"target": oh_target, "amber": 28.0, "red": 30.0},
    },
}

with (OUT_DIR / "mari_baseline.json").open("w") as f:
    json.dump(baseline, f, indent=2)

print(f"✓ Saved {OUT_DIR}/mari_baseline.json")
