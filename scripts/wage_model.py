"""Canonical wage model. One place, so the daily pull, the backfill and the
roster forecast can't drift apart.

THE RULE (verified against Xero payroll, 2026-07-15):

  A salaried employee costs `annual / 52` per week. Every week. Regardless of
  how many hours they log in Deputy.

Why this matters: Deputy returns Cost=0 for salaried staff (they're not on
hourly rates), so their cost has to be synthesized. The old model synthesized
`hours_logged x (annual/52/40)`, which quietly assumed hours logged == hours
paid. They are not the same thing:

  Kris Hegarty, week ending 2026-07-12: logged 16.0h in Deputy.
    old model: 16.0 x 44.95      = $719
    Xero paid: 38.5h + 1.5h leave = $1,798   -> $1,079 of real cost vanished

  Renan, same week: logged 41.8h.
    old model: 41.8 x 36.06 = $1,505
    Xero paid: 40u          = $1,442        -> $63 of cost invented

Across the 8 salaried staff that was -$3,328/week ex-super (-$3,728 inc), and
-$53,603 over Apr-Jul: our stored wages ran 12.2% under what payroll actually
paid — about 3 points on a wage line measured against a 30% target.

Xero payslips also show leave sits INSIDE the 40 units (Kris: 38.5 worked +
1.5 leave = 40), so salaried leave must not be charged on top of the weekly
salary or it double-counts.

Allocation: the weekly salary is spread across the shifts the employee actually
logged, pro-rata by hours, so the money lands on the venue/dept where they
worked. Hours decide WHERE the cost goes; they no longer decide HOW MUCH it is.
"""
from collections import defaultdict


def salaried_week_cost(annual, weeks_per_year=52):
    """What a salaried employee costs for one payroll week, ex-super."""
    return annual / weeks_per_year


def allocate_week(shifts, salaried, weeks_per_year=52):
    """Assign an ex-super cost to every shift in ONE payroll week.

    shifts:   [{"employee_id","hours","cost","date","bucket", ...}] — `bucket` is
              whatever the caller wants the cost attributed to (venue/dept/leave).
    salaried: {employee_id: annual}

    Returns (costed_shifts, warnings). Hourly shifts keep Deputy's own Cost.
    Salaried shifts share out annual/52 pro-rata by logged hours.
    """
    out, warn = [], []
    by_emp = defaultdict(list)
    for s in shifts:
        by_emp[str(s["employee_id"])].append(s)

    for eid, group in by_emp.items():
        if eid not in salaried:
            for s in group:
                c = s.get("cost") or 0
                if not c and (s.get("hours") or 0) > 0:
                    # Real hours, no rate, not salaried -> this labour is invisible.
                    # Don't silently book it at $0; surface it.
                    warn.append({"type": "zero_cost_shift", "employee_id": eid,
                                 "date": s.get("date"), "hours": s.get("hours"),
                                 "bucket": s.get("bucket")})
                out.append({**s, "cost_final": c})
            continue

        week_cost = salaried_week_cost(salaried[eid], weeks_per_year)
        total_h = sum((s.get("hours") or 0) for s in group)
        if total_h <= 0:
            # Paid, but clocked nothing we can attribute to a venue.
            warn.append({"type": "salaried_no_hours", "employee_id": eid,
                         "week_cost": round(week_cost, 2)})
            for s in group:
                out.append({**s, "cost_final": 0.0})
            continue
        for s in group:
            share = (s.get("hours") or 0) / total_h
            out.append({**s, "cost_final": week_cost * share})
    return out, warn
