"""Reconcile Harry Gatos' stored history against the weekly-report master.

Harry Gatos is a SEPARATE Lightspeed company — the Insights salelines pull that
verified Stowaway to $0.00 contains only the "Stowaway Bar" site, so HG's
revenue has never been checked against Lightspeed directly. That leg needs the
HG login.

What CAN be checked now is the same test that caught the Stow defect: does the
stored history reproduce the master it was built from? For Stowaway that test
turned up $1,158.29 of Stow food rung on Harry's till. The master itself agreed
with Lightspeed to 0.0037%, so master-agreement is strong evidence.

backfill_history.py's rules, applied in reverse:
  * HG's own master rows, MINUS 'stow food' (routed to Stowaway)
  * PLUS 'harry gatos food' rung on the STOWAWAY till (routed to HarryGatos)
"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path("/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting")
M = Path("/Users/Shared/ClaudeShared/STOW/Daily Sales")
CUT = "2026-07-06"


def norm(rg):
    k = (rg or "").strip().lower()
    return k[:-len(" [harrys]")] if k.endswith(" [harrys]") else k


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---- master-derived HG revenue, per backfill_history's route() ----
mday = defaultdict(float)
rgs = defaultdict(float)
with (M / "HarryGatos.csv").open() as f:
    for r in csv.DictReader(f):
        d = r["Date"]
        if d >= CUT:
            continue
        rg = norm(r["ReportingGroup"])
        v = num(r["Sale Amount"]) / 1.1
        rgs[rg] += v
        if rg == "stow food":
            continue                      # Stow's food on Harry's till -> Stowaway
        mday[d] += v

hgf_in = defaultdict(float)
with (M / "Stowaway.csv").open() as f:
    for r in csv.DictReader(f):
        d = r["Date"]
        if d >= CUT:
            continue
        if norm(r["ReportingGroup"]) == "harry gatos food":
            hgf_in[d] += num(r["Sale Amount"]) / 1.1
for d, v in hgf_in.items():
    mday[d] += v

hist = {}
with (ROOT / "data/hg_daily_history.csv").open() as f:
    for r in csv.DictReader(f):
        hist[r["date"]] = r

days = sorted(set(mday) & set(hist))
print(f"HARRY GATOS — stored history vs the master it was built from")
print(f"  {len(days)} days before {CUT}")
h = sum(num(hist[d]["revenue_ex_gst"]) for d in days)
m = sum(mday[d] for d in days)
print(f"\n  stored history : ${h:>13,.2f}")
print(f"  master-derived : ${m:>13,.2f}")
print(f"  difference     : ${h - m:>+13,.2f}   ({(h - m) / m * 100:+.4f}%)")

bad = [(d, num(hist[d]["revenue_ex_gst"]), mday[d]) for d in days
       if abs(num(hist[d]["revenue_ex_gst"]) - mday[d]) > 0.5]
print(f"\n  days differing > $0.50: {len(bad)}")
for d, a, b in sorted(bad, key=lambda t: -abs(t[1] - t[2]))[:12]:
    print(f"    {d}  history ${a:>10,.2f}  master ${b:>10,.2f}  {a - b:>+9,.2f}")

print(f"\n  cross-venue flows on Harry's till (pre-{CUT}):")
print(f"    'stow food' rung at Harry's -> Stowaway : ${rgs.get('stow food', 0):>10,.2f}")
print(f"    'harry gatos food' rung at Stow -> HG   : ${sum(hgf_in.values()):>10,.2f}")

print(f"\n  reporting groups on Harry's own till:")
for rg, v in sorted(rgs.items(), key=lambda kv: -kv[1])[:12]:
    print(f"    ${v:>12,.2f}  {rg}")
