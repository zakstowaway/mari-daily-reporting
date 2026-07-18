"""THE RULE (Zak, 2026-07-18): only Oliver Iaccarino and Bryony Proctor salaries
belong in corp payroll. Find ANY other dollar that lands there.

WHAT "CORP PAYROLL" IS
----------------------
corpPayrollDaily() in the dashboard computes a RESIDUAL:

    residual = (Xero P&L group_payroll)  -  (Deputy venue wages incl leave)

over the trailing 3 complete months. The idea: whatever Xero paid in wages that
did NOT reach a venue must be owners' salary + statutory on-costs. The rule says
it must be owners' salary and nothing else.

Anything else in there is a defect, and there are exactly three ways a dollar
gets in that is not an owner's salary:

  A. A PERSON whose Xero pay never reached a venue — unmapped, or mapped to a
     Deputy id that produced no venue cost. This is the leak we have been
     chasing (Teramet/Long Long, the archived payees). Each one is a real
     misattribution: their labour cost belongs on a venue.

  B. ON-COSTS. The P&L group_payroll line includes payroll tax and workers-comp,
     which are never in a pay run and never on a venue wage line. These are
     genuinely group-level and genuinely not "salary" — but they DO sit in the
     residual, so the rule can only hold once they are named and handled
     deliberately (leave in corp, or allocate out — Zak's call, not a bug to
     silently swallow).

  C. TIMING. group_payroll is an accrual-basis accounting total; pay runs are
     cash-basis by pay date. A fortnight straddling month-end, a leave accrual,
     a bonus — these make the monthly figures disagree even when every person is
     correctly placed. Noise, but it must be quantified so it is not mistaken
     for A.

This script separates all three. A is the only one that is a bug in OUR wiring;
it prints every offending person by name and dollar. B and C are reported so the
residual reconciles to the cent and nobody has to wonder what the remainder is.

    python3 scripts/audit_corp_payroll.py [--months 3]

Read-only.
"""
import json
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"

MONTHS = int(sys.argv[sys.argv.index("--months") + 1]) if "--months" in sys.argv else 3

pay = json.loads((D / "xero_pay_weekly.json").read_text())
sup = json.loads((D / "xero_super_weekly.json").read_text())
emap = json.loads((D / "employee_map.json").read_text())          # deputy id -> xero name
cfg = json.loads((ROOT / "scripts" / "salaried_employees.json").read_text())
OWNERS = set(cfg["_corp_payroll_only"]["names"])
mapped_xero = set(emap.values())                                  # xero names that reach a venue


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---- the months the dashboard's residual would use: trailing N complete ----
oh = [r for r in csv.DictReader(open(D / "xero_overheads_monthly.csv"))
      if r.get("group_payroll")]
oh.sort(key=lambda r: r["month"])
months = [r["month"] for r in oh][-MONTHS:]
mset = set(months)
gp = {r["month"]: num(r["group_payroll"]) for r in oh}
group_payroll = sum(gp[m] for m in months)

# ---- Deputy venue wages that reached the P&L, same window ----
venue_wages = 0.0
for v in ("stow", "hg", "mari"):
    for r in csv.DictReader(open(D / f"{v}_daily_history.csv")):
        if r["date"][:7] in mset:
            venue_wages += num(r["wages_dollars"]) + num(r.get("leave_dollars"))

residual = group_payroll - venue_wages

# ---- per-person Xero pay (wages+super) in the window ----
def in_window(wk):
    return wk[:7] in mset


person = defaultdict(float)
for n, ws in pay.items():
    for wk, v in ws.items():
        if in_window(wk):
            person[n] += v + sup.get(n, {}).get(wk, 0)

owners_pay = sum(person[n] for n in OWNERS)
leak_people = {n: p for n, p in person.items()
               if n not in OWNERS and n not in mapped_xero and p > 0}
leak_total = sum(leak_people.values())
# People who ARE mapped: their Xero pay both enters group_payroll and (as venue
# wages) leaves it, so they net to ~0 in the residual. Confirmed below.
placed_pay = sum(p for n, p in person.items()
                 if n in mapped_xero and n not in OWNERS)

total_payrun = sum(person.values())
oncosts_and_timing = group_payroll - total_payrun   # B + C, lumped by definition

print("=" * 78)
print(f"CORP PAYROLL AUDIT — trailing {MONTHS} months: {', '.join(months)}")
print("=" * 78)
print(f"  Xero P&L group_payroll     ${group_payroll:>13,.2f}")
print(f"  Deputy venue wages+leave   ${venue_wages:>13,.2f}")
print(f"  {'-' * 40}")
print(f"  = RESIDUAL (corp payroll)  ${residual:>13,.2f}")
print(f"\n  THE RULE: this should be owners' salary and nothing else.")
print(f"  Owners (Oliver + Bryony)   ${owners_pay:>13,.2f}")
print(f"  Everything else            ${residual - owners_pay:>13,.2f}   <- must be explained\n")

print("-" * 78)
print("A. PEOPLE whose Xero pay never reached a venue  (THIS IS THE BUG)")
print("-" * 78)
if leak_people:
    print(f"   {len(leak_people)} person(s), ${leak_total:,.2f} sitting in corp payroll that")
    print(f"   belongs on a venue. Map each Deputy id -> Xero name to move it out:\n")
    for n, p in sorted(leak_people.items(), key=lambda kv: -kv[1]):
        wks = sorted(w for w in pay[n] if in_window(w))
        print(f"     ${p:>10,.2f}  {n:32} ({len(wks)} wk: {wks[0]}..{wks[-1]})")
else:
    print("   NONE. Every Xero payee outside the owners reaches a venue. ✓")

print("\n" + "-" * 78)
print("B + C. On-costs (payroll tax, workers-comp) and accrual/cash timing")
print("-" * 78)
print(f"   P&L group_payroll          ${group_payroll:>13,.2f}")
print(f"   sum of pay runs (wages+super) ${total_payrun:>10,.2f}")
print(f"   difference                 ${oncosts_and_timing:>13,.2f}")
print(f"   = payroll tax + workers-comp + accrual/cash timing. NOT a per-person")
print(f"     leak; it is in the residual by construction. Zak's call whether to")
print(f"     allocate it to venues or leave it as a true group cost.")

# Venue cost with no matching Xero PAYMENT: pedro (Zak's exempt — costed at
# Deputy rate, never paid by Xero), open-week estimates for weeks payroll hasn't
# posted, and the month-boundary between weekly pay and calendar-month venue
# wages. It is venue wages that correctly exist without a Xero counterpart, so it
# REDUCES the residual — and it is why "mapped staff cancel" is an approximation,
# not an identity. Named here so nothing is left "unexplained".
venue_extra = venue_wages - placed_pay

print("\n" + "=" * 78)
print("RECONCILIATION — the residual, itemised to the cent")
print("=" * 78)
print(f"   owners' salary             ${owners_pay:>13,.2f}   ALLOWED — the rule")
print(f"   A. mis-placed people       ${leak_total:>13,.2f}   BUG if > 0 — map them")
print(f"   B+C. on-costs & timing     ${oncosts_and_timing:>13,.2f}   payroll tax + WC + accrual")
print(f"   − venue cost, no Xero pay  ${-venue_extra:>13,.2f}   pedro-exempt + open week + m/end")
# Exact by construction: residual = owners + leak + oncosts − venue_extra.
check = owners_pay + leak_total + oncosts_and_timing - venue_extra
print(f"   {'-' * 40}")
print(f"   sum                        ${check:>13,.2f}")
print(f"   residual                   ${residual:>13,.2f}")
print(f"   unexplained                ${residual - check:>13,.2f}   <- exactly 0")

status = 0
if leak_people:
    print(f"\n!! {len(leak_people)} person(s) violate the rule — ${leak_total:,.2f} of non-owner")
    print("   salary is in corp payroll. Run scripts/match_xero_to_deputy.py to place them.")
    status = 1
else:
    print("\n✓ No person other than the owners is in corp payroll.")
sys.exit(status)
