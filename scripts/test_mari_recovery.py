"""
Adversarial tests for the Mari coverage recovery + the narrowed-export tripwire
in daily_aggregator.py.  Added 2026-07-16.

Runs the REAL aggregator against synthetic fixtures via REPO_ROOT, so it never
touches data/.  No network, no Deputy, no Xero.

    python3 scripts/test_mari_recovery.py      # 13 checks, exits 1 on failure

WHY THIS EXISTS
  Marilyna's has no till. Her CSV is a filtered extract of the Stow POS, so when
  the 'Mari Daily Sales Auto' filter drops a reporting group, Stow strips those
  rows and Mari never receives them -> the revenue reaches NO venue. It went
  unnoticed for days because both sides looked internally consistent.

  The recovery adds back only rows that are on the Stow till AND absent from
  Mari's file. That set is DERIVED from the gap, so it self-heals (test 1). An
  earlier attempt added all 'm' rows blind and double-counted six days.

FIXTURES MUST USE REAL PRODUCT NAMES. classify_product() resolves names through
product_dept_map.json -- an invented name like "Marilyna's Pizza Large Sanchez"
silently classifies as 'b' (Stow FOH), not 'm', and every assertion then passes
or fails for the wrong reason. Ask the classifier, don't guess:
    'Large Sanchez', 'Regular Sanchez [Dine-in]', 'Add Pineapple'  -> 'm'
    'Unlimited Dumplings'                                          -> 'hgf'
    'Pepsi Max Glass'                                              -> 'b'
"""
import csv, json, os, subprocess, shutil, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BT   = Path(tempfile.mkdtemp(prefix="mari_recovery_test_")) / "root"
HDR  = ["Product Name","Product Quantity","$ Sales","Total Tax","Cost","% of Quantity","% of Sale Amount","Gross Profit %"]

def write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HDR); w.writeheader()
        for r in rows: w.writerow(r)

def row(name, sales, tax=None, qty=1, cost=0.0):
    tax = round(sales/11, 2) if tax is None else tax
    return {"Product Name":name,"Product Quantity":qty,"$ Sales":f"${sales:.2f}","Total Tax":f"${tax:.2f}",
            "Cost":f"${cost:.2f}","% of Quantity":"1%","% of Sale Amount":"1%","Gross Profit %":"80%"}

def run(date, venue="marilynas"):
    env = dict(os.environ, REPO_ROOT=str(BT))
    r = subprocess.run([sys.executable, str(REPO/"scripts/daily_aggregator.py"), "--venue", venue, date],
                       capture_output=True, text=True, env=env, cwd=str(REPO))
    return r.stdout + r.stderr

def rev(date, prefix="mari"):
    p = BT/"data"/f"{prefix}_daily_{date}.json"
    if not p.exists(): return None
    return json.load(p.open())["sales"]["revenue_ex_gst"]

def fresh():
    if BT.exists(): shutil.rmtree(BT)
    (BT/"data").mkdir(parents=True)

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))

# Shared: a Stow till with 2 pizza rows + 1 stow row; Mari's report varies per test.
STOW = [row("Regular Sanchez [Dine-in]", 110.00), row("Add Pineapple", 5.50),
        row("Large Sanchez", 80.91), row("Pepsi Max Glass", 12.00),
        row("Unlimited Dumplings", 24.20)]

print("=" * 78)
print("1. SELF-HEALING — filter fixed: Mari's report has the pizza rows")
fresh(); d="2099-01-01"
write(BT/"data"/f"insights_stow_{d}.csv", STOW)
write(BT/"data"/f"insights_mari_{d}.csv", [row("Regular Sanchez [Dine-in]",110.00), row("Add Pineapple",5.50),
                                            row("Large Sanchez",80.91)])
out = run(d)
check("no RECOVERED when filter is correct", "RECOVERED" not in out)
check("no DEDUP UNSOUND", "DEDUP UNSOUND" not in out)
check("Mari revenue = her own file only (no double-count)", abs(float(rev(d)) - (110+5.5+80.91)/1.1) < 0.5,
      f"got {rev(d)}, want ~{(110+5.5+80.91)/1.1:.2f}")

print("\n2. BROKEN FILTER — pizza rows missing from Mari's report")
fresh(); d="2099-01-02"
write(BT/"data"/f"insights_stow_{d}.csv", STOW)
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez", 80.91)])
out = run(d)
check("RECOVERED fires", "RECOVERED" in out)
check("recovers exactly the 2 missing rows", "2 Mari rows" in out, [l for l in out.split("\n") if "RECOVERED" in l])
check("Mari revenue includes recovered", abs(float(rev(d)) - (110+5.5+80.91)/1.1) < 0.5, f"got {rev(d)}")

print("\n3. DEDUP UNSOUND — same product, DIFFERENT values in the two files")
fresh(); d="2099-01-03"
write(BT/"data"/f"insights_stow_{d}.csv", STOW)
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez", 40.00)])  # stow says 80.91
out = run(d)
check("DEDUP UNSOUND fires on value mismatch", "DEDUP UNSOUND" in out)

print("\n4. EMPTY MARI REPORT — filter matches nothing")
fresh(); d="2099-01-04"
write(BT/"data"/f"insights_stow_{d}.csv", STOW)
write(BT/"data"/f"insights_mari_{d}.csv", [])
out = run(d)
check("does not crash", "Traceback" not in out, "crashed" if "Traceback" in out else "")
check("recovers all 3 Mari rows", "3 Mari rows" in out, [l for l in out.split("\n") if "RECOVERED" in l])

print("\n5. IDEMPOTENCY — running twice must not change the answer")
fresh(); d="2099-01-05"
write(BT/"data"/f"insights_stow_{d}.csv", STOW)
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez", 80.91)])
run(d); first = rev(d)
run(d); second = rev(d)
check("second run == first run", first == second, f"{first} vs {second}")

print("\n6. NARROWED STOW EXPORT — the tripwire")
fresh(); d="2099-01-06"
write(BT/"data"/f"insights_stow_{d}.csv", [row("Pepsi Max Glass",12.00)])   # zero cross-venue rows
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez",80.91)])
out = run(d, venue="stowaway")
check("NARROWED fires on Stow-only export", "NARROWED" in out)

print("\n7. FALSE-POSITIVE CHECK — Stow export with 'm' rows but no HG food")
fresh(); d="2099-01-07"
write(BT/"data"/f"insights_stow_{d}.csv", [row("Pepsi Max Glass",12.00), row("Large Sanchez",80.91)])
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez",80.91)])
out = run(d, venue="stowaway")
check("NARROWED stays SILENT (legit: no HG food that day)", "NARROWED" not in out)

print("\n8. MISSING STOW EXPORT — recovery must degrade gracefully")
fresh(); d="2099-01-08"
write(BT/"data"/f"insights_mari_{d}.csv", [row("Large Sanchez",80.91)])
out = run(d)
check("does not crash without the sibling file", "Traceback" not in out)

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL: print("FAILURES: " + ", ".join(FAIL))

shutil.rmtree(BT.parent, ignore_errors=True)
sys.exit(1 if FAIL else 0)
