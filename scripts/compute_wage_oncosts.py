"""Compute the wage on-cost rate (payroll tax + workers-comp, as a % of wages)
and the owners' weekly salary, for the dashboard P&L.

DECISION (Zak, 2026-07-18): payroll tax + workers-comp go into OVERHEADS,
calculated from wages. Corp payroll then holds ONLY owner salary. This script
produces the two numbers that split the old corp-payroll residual:

    residual = group_payroll - venue wages
             = OWNERS  +  (payroll tax + WC)  +  accrual/cash timing
       corp <-'          overheads <-'           noise, averages to ~0/yr

  * owner_weekly_inc_super : Oliver + Bryony, straight from the pay runs. Corp
    payroll becomes exactly this and nothing else — the rule, made true.
  * oncost_rate : (group_payroll − venue wages − owners) / venue wages, blended
    over EVERY month we hold so the timing swings (this window ran −3% to +34%
    month to month) average out. It is applied per venue as rate × that venue's
    wages, so it lands where the wages are — no separate allocation needed.

WHY A RATE AND NOT THE RAW MONTHLY FIGURE
Payroll tax is marginal with a threshold (NSW ~$1.2M/yr, one threshold for the
group) and workers-comp is a flat class rate; neither is knowable per-person and
the monthly P&L figure carries accrual timing a pay run doesn't. A blended rate
is the only stable, fair thing to put on a daily wage line.

CAVEAT written into the output: only a few months exist here. The rate is the
best current estimate; replace `oncost_rate` with Xero's actual annual
(payroll tax + workers-comp) / total wages the moment a full year is available.
Everything downstream reads the file, so that is a one-number change.

    python3 scripts/compute_wage_oncosts.py        # writes data/wage_oncosts.json
    python3 scripts/compute_wage_oncosts.py --show # print only
"""
import json
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
OUT = D / "wage_oncosts.json"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


pay = json.loads((D / "xero_pay_weekly.json").read_text())
sup = json.loads((D / "xero_super_weekly.json").read_text())
cfg = json.loads((ROOT / "scripts" / "salaried_employees.json").read_text())
OWNERS = set(cfg["_corp_payroll_only"]["names"])

oh = [r for r in csv.DictReader(open(D / "xero_overheads_monthly.csv"))
      if r.get("group_payroll")]
oh.sort(key=lambda r: r["month"])
months = [r["month"] for r in oh]
mset = set(months)
group_payroll = sum(num(r["group_payroll"]) for r in oh)

# Venue wages that reached the P&L, same months.
venue_wages = 0.0
for v in ("stow", "hg", "mari"):
    for r in csv.DictReader(open(D / f"{v}_daily_history.csv")):
        if r["date"][:7] in mset:
            venue_wages += num(r["wages_dollars"]) + num(r.get("leave_dollars"))

# Owners' pay (inc super) over the same months, from the pay runs.
owner_pay = 0.0
owner_weeks = set()
for n in OWNERS:
    for wk, w in pay.get(n, {}).items():
        if wk[:7] in mset:
            owner_pay += w + sup.get(n, {}).get(wk, 0)
            owner_weeks.add(wk)

oncost_dollars = group_payroll - venue_wages - owner_pay      # tax + WC + timing
oncost_rate = oncost_dollars / venue_wages if venue_wages else 0.0

# Owners' run-rate per week, for the dashboard's corp-payroll line. Averaged over
# the weeks they were actually paid, not the calendar, so a missed pay-run week
# doesn't halve it.
owner_weekly = owner_pay / len(owner_weeks) if owner_weeks else 0.0

out = {
    "_comment": "Wage on-costs for the dashboard P&L. payroll tax + workers-comp "
                "-> overheads as oncost_rate x wages; owners -> corp payroll. "
                "See scripts/compute_wage_oncosts.py.",
    "_generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "_months_used": months,
    "_caveat": "oncost_rate is blended over only these months and carries some "
               "accrual/cash timing. Replace with Xero's actual annual "
               "(payroll tax + workers-comp) / total wages once a full year "
               "exists — it is a one-number change; everything reads this file.",
    "oncost_rate": round(oncost_rate, 4),
    "owner_weekly_inc_super": round(owner_weekly, 2),
    "_basis": {
        "group_payroll": round(group_payroll, 2),
        "venue_wages_incl_leave": round(venue_wages, 2),
        "owner_pay_incl_super": round(owner_pay, 2),
        "oncost_dollars": round(oncost_dollars, 2),
        "owner_weeks_paid": len(owner_weeks),
    },
}

print(f"months            : {months[0]} .. {months[-1]}  ({len(months)})")
print(f"group_payroll     : ${group_payroll:>13,.2f}")
print(f"venue wages+leave : ${venue_wages:>13,.2f}")
print(f"owners (inc super): ${owner_pay:>13,.2f}   -> ${owner_weekly:,.2f}/wk")
print(f"on-cost dollars   : ${oncost_dollars:>13,.2f}   (payroll tax + WC + timing)")
print(f"ON-COST RATE      : {oncost_rate * 100:.2f}% of wages")
print(f"\n  sanity: NSW payroll tax ~5.45% ABOVE a ~$1.2M/yr group threshold, so")
print(f"  effective payroll tax is well under 5.45%; workers-comp hospitality")
print(f"  ~1.5-2.5%. Expect the true rate ~4-7%. If this is far off, timing is")
print(f"  dominating a short window — get the annual figure from Xero.")

if "--show" not in sys.argv:
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}")
