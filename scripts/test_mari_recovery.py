"""
Marilyna's revenue: sourced from the STOW TILL, not from her export.

    python3 scripts/test_mari_recovery.py      # exits 1 on failure

Runs the REAL aggregator against synthetic fixtures via REPO_ROOT, so it never
touches data/. No network, no Deputy, no Xero.

WHY THIS FILE CHANGED SHAPE (2026-07-17)
  Marilyna's has no till. Her Lightspeed export was only ever a FILTER over the
  Stow POS — a saved schedule with a Reporting Group list on it. While her P&L
  was sourced from that filter, her revenue was hostage to a setting nobody
  versions, and it failed in three directions:

    filter DROPS a group  -> Stow strips those rows, her report never gets them,
                             the money reaches NO venue. $612.70 on 14 Jul.
    filter GAINS a group  -> her report bills it, Stow doesn't strip it, both
                             venues keep it. '$60 BANQUET', $54.55 a time.
    filter CHANGES        -> history splits in two. Delivery Cocktails were hers
                             until 16 Jul and Stow's after; no single map can be
                             right for both eras. ~$43/day on 10-11 Jul.

  All three came from ONE mistake: Stow stripped by CLASSIFIER while Mari
  counted by REPORT. Two definitions of one boundary, and every gap between them
  leaked money one way or doubled it the other.

  Now she is the 'm' slice of the Stow till. The till is the whole site, so
  nothing can go missing from it; and Stow strips exactly what Mari receives, so
  the two cannot disagree. One map, one rule, past and future.

  The old RECOVERED / DEDUP UNSOUND / DOUBLE COUNTED guards are gone because
  those failures are now UNREACHABLE — not because they stopped mattering.
  Tests 1-3 exist to prove they're unreachable. If any of them ever fails,
  somebody has pointed Mari back at her export.

FIXTURES MUST USE REAL PRODUCT NAMES. classify_product resolves through
product_dept_map.json, so an invented name silently lands in the 'b' FOH
catch-all and every assertion passes for the wrong reason. Ask the classifier:
    'Large Sanchez', 'Regular Sanchez [Dine-in]', 'Add Pineapple' -> 'm'
    'Unlimited Dumplings'                                          -> 'hgf'
    'Pepsi Max Glass'                                              -> 'b'
"""
import csv, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT = Path(tempfile.mkdtemp(prefix="mari_till_test_")) / "root"
HDR = ["Product Name","Product Quantity","$ Sales","Total Tax","Cost","% of Quantity","% of Sale Amount","Gross Profit %"]

def write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HDR); w.writeheader()
        for r in rows: w.writerow(r)

def row(name, sales, qty=1, cost=0.0):
    return {"Product Name": name, "Product Quantity": qty, "$ Sales": f"${sales:.2f}",
            "Total Tax": f"${sales/11:.2f}", "Cost": f"${cost:.2f}", "% of Quantity": "1%",
            "% of Sale Amount": "1%", "Gross Profit %": "80%"}

def run(date, venue="marilynas"):
    env = dict(os.environ, REPO_ROOT=str(BT))
    r = subprocess.run([sys.executable, str(REPO/"scripts/daily_aggregator.py"), "--venue", venue, date],
                       capture_output=True, text=True, env=env, cwd=str(REPO))
    return r.stdout + r.stderr

def rev(date, prefix="mari"):
    p = BT/"data"/f"{prefix}_daily_{date}.json"
    return json.load(p.open())["sales"]["revenue_ex_gst"] if p.exists() else None

def fresh():
    if BT.exists(): shutil.rmtree(BT)
    (BT/"data").mkdir(parents=True)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))

# a Stow till: 2 Mari dine-in rows, 1 Mari pizza, 1 Stow beer, 1 HG food
TILL = [row("Regular Sanchez [Dine-in]", 110.00), row("Add Pineapple", 5.50),
        row("Large Sanchez", 80.91), row("Pepsi Max Glass", 12.00),
        row("Unlimited Dumplings", 24.20)]
MARI_EX = (110.00 + 5.50 + 80.91) / 1.1     # the 'm' rows, ex GST

print("=" * 78)
print("1. HER EXPORT CANNOT DROP REVENUE  (the $612.70 leak, now unreachable)")
fresh(); d = "2099-01-01"
write(BT/"data"/f"insights_stow_{d}.csv", TILL)
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez", 80.91)])   # filter dropped the dine-ins
out = run(d)
check("Mari is the till's 'm' rows, not her broken export",
      abs(float(rev(d)) - MARI_EX) < 0.5, f"got {rev(d)}, want ~{MARI_EX:.2f}")
check("the drift is reported", "MARI FILTER DRIFT" in out)

print("\n2. HER EXPORT CANNOT DOUBLE-COUNT  (the $60 BANQUET, now unreachable)")
fresh(); d = "2099-01-02"
write(BT/"data"/f"insights_stow_{d}.csv", TILL)
# her filter gained a Stow product — it must NOT reach her, and must stay on Stow
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez", 80.91), row("Pepsi Max Glass", 12.00)])
out = run(d)
check("a Stow product in her export does not become Mari's",
      abs(float(rev(d)) - MARI_EX) < 0.5, f"got {rev(d)}")
run(d, venue="stowaway")
check("and Stow still keeps it", abs(float(rev(d, "stow")) - 12.00/1.1) < 0.5, f"got {rev(d,'stow')}")

print("\n3. THE SPLIT IS EXHAUSTIVE  (nothing reaches no venue, nothing reaches two)")
fresh(); d = "2099-01-03"
write(BT/"data"/f"insights_stow_{d}.csv", TILL)
write(BT/"data"/f"insights_hg_{d}.csv", [])
run(d); run(d, venue="stowaway"); run(d, venue="harry")
mari, stow, hg = float(rev(d)), float(rev(d, "stow")), float(rev(d, "hg") or 0)
site = sum(float(r["$ Sales"].strip("$")) for r in TILL) / 1.1
check("mari + stow + hg == the whole till", abs((mari + stow + hg) - site) < 0.5,
      f"{mari:.2f}+{stow:.2f}+{hg:.2f}={mari+stow+hg:.2f} vs {site:.2f}")

print("\n4. NO STOW EXPORT -> Mari has no source at all (she has no till)")
fresh(); d = "2099-01-04"
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez", 80.91)])
out = run(d)
check("says so plainly, does not silently use her export",
      "she has no till of her own" in out or "data_missing" in out)
check("does not crash", "Traceback" not in out)

print("\n5. AGREEMENT IS SILENT  (no crying wolf when the filter is right)")
fresh(); d = "2099-01-05"
write(BT/"data"/f"insights_stow_{d}.csv", TILL)
write(BT/"data"/f"insights_mari_{d}.csv",
      [row("Regular Sanchez [Dine-in]", 110.00), row("Add Pineapple", 5.50), row("Large Sanchez", 80.91)])
out = run(d)
check("no drift warning when export == till", "MARI FILTER DRIFT" not in out)
check("revenue unchanged either way", abs(float(rev(d)) - MARI_EX) < 0.5)

print("\n6. IDEMPOTENT")
fresh(); d = "2099-01-06"
write(BT/"data"/f"insights_stow_{d}.csv", TILL)
run(d); first = rev(d); run(d); second = rev(d)
check("second run == first", first == second, f"{first} vs {second}")

print("\n7. STOW'S EXPORT NARROWED -> still caught")
fresh(); d = "2099-01-07"
write(BT/"data"/f"insights_stow_{d}.csv", [row("Pepsi Max Glass", 12.00)])
out = run(d, venue="stowaway")
check("NARROWED fires", "NARROWED" in out)

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL: print("FAILURES: " + ", ".join(FAIL))
shutil.rmtree(BT.parent, ignore_errors=True)
sys.exit(1 if FAIL else 0)
