"""Derive Mari's food/bev split across history. No Deputy call needed — a pizza
shop's revenue IS food revenue and its COGS IS food COGS, so these columns were
always computable from data already in the CSV.

Fills empties only, never overwrites. Crucially: only writes the COGS/GP columns
where cogs_dollars actually exists. 34 trading days (mostly Oct-Nov 2024) have
revenue but no COGS — deriving those would have written cogs 0.0% / GP 100.0%,
inventing a perfect-margin pizza shop. Revenue is known on every trading day, so
food_ex_gst is always safe; the margin columns are left blank where the source is
blank, which is what "awaiting data" is actually for."""
import csv, sys
from pathlib import Path

f = Path("data/mari_daily_history.csv")
rows = list(csv.DictReader(f.open()))
fields = list(rows[0].keys())
if "wages_driver_dollars" not in fields:
    fields.insert(fields.index("wages_foh_dollars") + 1, "wages_driver_dollars")

rev_only = full = 0
for r in rows:
    rev = float(r.get("revenue_ex_gst") or 0)
    if rev <= 0 or r.get("food_ex_gst"):
        continue
    r["food_ex_gst"] = round(rev, 2)
    r["bev_ex_gst"] = 0.0
    cogs_raw = r.get("cogs_dollars")
    if cogs_raw and float(cogs_raw) > 0:
        cogs = float(cogs_raw)
        r["food_cogs"] = round(cogs, 2)
        r["bev_cogs"] = 0.0
        r["food_cogs_pct"] = round(cogs / rev * 100, 1)
        r["food_gp_pct"] = round((rev - cogs) / rev * 100, 1)
        full += 1
    else:
        rev_only += 1          # revenue known, margin genuinely unknown

if "--write" in sys.argv:
    with f.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"WROTE  {full} rows with full split, {rev_only} revenue-only (no COGS at source)")
else:
    print(f"DRY RUN  {full} rows would get the full split, {rev_only} revenue-only (COGS left blank)")
    for d in ('2024-10-23', '2025-05-14', '2026-05-10', '2026-07-14'):
        r = next((x for x in rows if x['date'] == d), None)
        if r: print(f"    {d} | rev {r['revenue_ex_gst']:>8} -> food {str(r['food_ex_gst']):>8} | cogs% {str(r['food_cogs_pct']) or '(blank)':>7} | gp% {str(r['food_gp_pct']) or '(blank)':>7}")
