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


CONTRACT_HOURS = 40.0


def allocate_week(shifts, salaried, weeks_per_year=52, week_days=None):
    """Assign an ex-super cost to every shift in ONE payroll week.

    shifts:   [{"employee_id","hours","cost","date","bucket", ...}] — `bucket` is
              whatever the caller wants the cost attributed to (venue/dept/leave).
    salaried: {employee_id: annual}
    week_days: optional [iso dates] of the payroll week, for spreading leave.

    Returns (costed_shifts, warnings). Hourly shifts keep Deputy's own Cost.

    SALARIED SHORTFALL IS LEAVE (Zak, 2026-07-17)
      A salaried person is contracted to 40 hours. If they aren't on for 40, the
      shortfall is leave — which is exactly what Xero shows: leave sits INSIDE
      the 40 (Kris: 38.5 worked + 1.5 leave = 40).

      So the denominator is 40, not "whatever they logged", and the shortfall's
      share of annual/52 goes to the `leave` bucket. The weekly total is
      unchanged; only where it lands changes:

        Kris, 14 Jul: $2,013.76/week, 6.075h logged, nothing else.
          before  6.075/6.075 = 100%  -> $2,013.76 onto ONE Stow shift.
                  Stow took $2,148 that Tuesday. Read 100.9% wages.
          after   6.075/40 = 15.2%    -> $306 Stow, $1,708 leave.

      This is NOT the old `hours x rate` model. That one LOST the money — Kris's
      16h week costed at $719 against $1,798 actually paid, and $268k of real
      labour went missing across 90 weeks. Here the money is conserved to the
      cent; it just stops pretending a manager's whole salary was earned on the
      one Tuesday he happened to clock in.

      Over 40 the denominator is the hours themselves: you're still paid 40 units
      (Xero), so there's no leave to book and nothing to cap — the salary simply
      spreads across everything worked.
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
        # Contracted to 40. Anything short of it is leave, not a heavier shift.
        denom = max(total_h, CONTRACT_HOURS)
        for s in group:
            share = (s.get("hours") or 0) / denom
            out.append({**s, "cost_final": week_cost * share})

        short_h = CONTRACT_HOURS - total_h
        if short_h > 0.01:
            leave_cost = week_cost * (short_h / denom)
            # Leave is a weekly quantity — it didn't happen on a day. Spread it
            # across the week so the group view doesn't grow a spike on whichever
            # day the person happened to clock in. Falls back to their own shift
            # dates when the caller doesn't say what the week's days are.
            days = week_days or sorted({s.get("date") for s in group if s.get("date")})
            if days:
                for d in days:
                    out.append({"employee_id": eid, "hours": short_h / len(days),
                                "cost": 0, "date": d, "bucket": "leave",
                                "_leave_fill": True,
                                "cost_final": leave_cost / len(days)})
            warn.append({"type": "salaried_shortfall_leave", "employee_id": eid,
                         "logged_h": round(total_h, 2), "leave_h": round(short_h, 2),
                         "leave_cost": round(leave_cost, 2)})
    return out, warn
