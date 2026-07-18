"""Pins WHY Stowaway's stored revenue is legitimately ABOVE Lightspeed's
Stow-site total, so nobody ever "reconciles" it by deleting real money.

THE TRAP (found 2026-07-18)
---------------------------
Our history said the Stow+Mari pair was $1,313.63 more than Lightspeed's
Stow-site query returned, over 549 days. That looks exactly like an overstatement
and invites an obvious fix: source both venues from Lightspeed, and watch the
reconciliation go green.

It would have gone green. It would also have destroyed $1,158.29 of real
Stowaway revenue -- because Stow's food gets rung on the HARRY GATOS TILL, Harry
Gatos is a separate SITE in Lightspeed, and a Stow-site query cannot return those
rows no matter how right the query is. Lightspeed would have agreed with itself,
and every check would have passed.

These tests exist so that fix fails loudly instead.

Needs the weekly-report masters, which live outside the repo. Skips (does not
fail) when they aren't mounted, so CI stays green.

    python3.12 scripts/test_revenue_reconciliation.py
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTERS = Path("/Users/Shared/ClaudeShared/STOW/Daily Sales")
LS = Path("/tmp/ls_rg_cost.csv")
CUT = "2026-07-06"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def norm_rg(rg):
    k = (rg or "").strip().lower()
    return k[:-len(" [harrys]")] if k.endswith(" [harrys]") else k


MARILYNAS_RGS = {
    "marilyna's pizza", "marilynas pizza",
    "marilyna's soft drinks", "marilynas soft drinks",
    "add-ons - pizza", "dine-in pizza", "delivery alcohol",
}

if not (MASTERS / "HarryGatos.csv").exists():
    print(f"SKIP — masters not mounted at {MASTERS}")
    sys.exit(0)


def read_master(fname):
    rows = []
    with (MASTERS / fname).open() as f:
        for r in csv.DictReader(f):
            if r["Date"] >= CUT:
                continue
            try:
                r["_ex"] = float(r["Sale Amount"] or 0) / 1.1
            except ValueError:
                continue
            r["_rg"] = norm_rg(r["ReportingGroup"])
            rows.append(r)
    return rows


hg = read_master("HarryGatos.csv")

print("=" * 78)
print("1. STOW FOOD IS RUNG ON HARRY'S TILL — and it is Stow's money")
stf = [r for r in hg if r["_rg"] == "stow food"]
stf_total = sum(r["_ex"] for r in stf)
stf_days = {r["Date"] for r in stf}
check("Stow Food exists on the HG till", stf_total > 0,
      f"${stf_total:,.2f} over {len(stf_days)} days")
check("it is material enough to notice if deleted", stf_total > 500,
      f"${stf_total:,.2f}")
# The exact figure the investigation landed on. If this drifts, the masters
# changed and the numbers in backfill_mari_rg_split's docstring are stale.
check("still ~$1,158.29 (the figure the docs cite)", abs(stf_total - 1158.29) < 5,
      f"${stf_total:,.2f}")

print("\n2. LIGHTSPEED'S STOW SITE CANNOT SEE IT")
if not LS.exists():
    print(f"  SKIP — {LS} not present (re-pull the Insights salelines export)")
else:
    sites = set()
    ls_stow_rgs = defaultdict(float)
    with LS.open() as f:
        for r in csv.DictReader(f):
            sites.add(r["Location Site Name"])
            if r["Location Site Name"] == "Stowaway Bar" and \
                    r["Sales Data Sale Closed Date"] < CUT:
                ls_stow_rgs[norm_rg(r["Products Reporting Group Name"])] += \
                    float(r["Exclusive of tax"] or 0)
    # The pull is Stow-site-only — which IS the point. Harry Gatos is a separate
    # site and simply isn't in this file, so no query against it can ever return
    # Stow food rung on Harry's till. Asserting "more than one site" would be
    # asserting the opposite of the thing that makes this dangerous.
    check("the Lightspeed pull covers the Stow site only", sites == {"Stowaway Bar"},
          f"sites: {sorted(sites)} — HG's till is not in this file at all, which is "
          f"exactly why it cannot see the ${stf_total:,.2f}")
    check("'stow food' on the HG till is absent from the Stow site query",
          ls_stow_rgs.get("stow food", 0) < 500,
          f"Stow-site 'stow food' = ${ls_stow_rgs.get('stow food', 0):,.2f} "
          f"vs ${stf_total:,.2f} on HG's till")

print("\n3. THE PAIR TOTAL MUST BE CONSERVED, NOT SET FROM LIGHTSPEED")
print("   (a backfill that sources Stow from the Stow site deletes the section-1")
print("    money and still reconciles — this is the guard against that)")
import glob
baks = sorted(glob.glob(str(ROOT / "data/_backups/stow_daily_history.bak_*.csv")))
if not baks:
    print("  SKIP — no pre-backfill backup to compare against")
else:
    def load(p):
        with open(p) as f:
            return {r["date"]: r for r in csv.DictReader(f)}

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    stow_now = load(ROOT / "data/stow_daily_history.csv")
    mari_now = load(ROOT / "data/mari_daily_history.csv")
    stow_bak = load(baks[-1])
    mari_bak = load(sorted(glob.glob(str(ROOT / "data/_backups/mari_daily_history.bak_*.csv")))[-1])
    days = [d for d in stow_now if d < CUT and d in stow_bak and d in mari_bak]
    before = sum(num(stow_bak[d]["revenue_ex_gst"]) + num(mari_bak[d]["revenue_ex_gst"]) for d in days)
    after = sum(num(stow_now[d]["revenue_ex_gst"]) + num(mari_now[d]["revenue_ex_gst"]) for d in days)
    check("the Mari<->Stow backfill conserved the pair to the cent",
          abs(after - before) < 0.5, f"drift ${after - before:+,.2f} over {len(days)} days")

print("\n4. MARI IS NEVER RUNG ON HARRY'S TILL")
print("   (this is what makes sourcing her from the Stow site alone complete —")
print("    if it ever stops being true, her backfill starts losing money)")
mari_on_hg = sum(r["_ex"] for r in hg if r["_rg"] in MARILYNAS_RGS)
check("Mari's revenue on the HG till is immaterial", mari_on_hg < 50,
      f"${mari_on_hg:,.2f} over 21 months")

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
sys.exit(1 if FAIL else 0)
