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


def allocate_week(shifts, salaried, weeks_per_year=52, week_days=None,
                  shortfall_leave=False):
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
        #
        # OPEN WEEK ONLY (shortfall_leave=True). A CLOSED week already states its
        # leave as real Deputy timesheets — they arrive via IsLeave, they're in
        # `group`, they count toward total_h, and the shortfall correctly
        # computes to nothing. Synthesising leave there too would relabel every
        # sloppy clock-off as annual leave: measured across 90 weeks it moved
        # $219,008 off the venue lines (HG 59.8% -> 53.7%) purely because
        # salaried staff log a little under their contract. This rule exists for
        # unapproved timesheets and a live roster, not for restating history.
        denom = max(total_h, CONTRACT_HOURS) if shortfall_leave else total_h
        for s in group:
            share = (s.get("hours") or 0) / denom
            out.append({**s, "cost_final": week_cost * share})

        short_h = (CONTRACT_HOURS - total_h) if shortfall_leave else 0
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


# ---------------------------------------------------------------------------
# SUPER
# ---------------------------------------------------------------------------
# Deputy's Cost is ex-super. Everything we report is inc-super. The gross-up
# used to be a flat 12% everywhere, which is wrong twice over:
#
#   1. The SG rate CHANGED — 11% (Jul 2023), 11.5% (Jul 2024), 12% (Jul 2025).
#      Most of our history predates 12%.
#   2. Under-18s working <=30 h/wk are entitled to NO super. Not a reduced
#      rate — none. Marilyna's runs on teenage drivers, so a flat 12% invented
#      ~$5,413 of super for people who legally receive zero, and booked it to
#      her line.
#
# Measured over 100 Xero pay runs: $289,768.94 on $2,587,065.11 = 11.201%.
# Flat 12% books $310,447.81 — $20,678.87 too much.
#
# This lives here, and only here, because three copies of the gross-up is how
# it drifted in the first place. rebuild_wages, daily_aggregator and roster_pull
# all resolve super through super_lookup().


def super_lookup(xero_pay, xero_super, emp_map, default_rate, as_of=None):
    """-> mult(employee_id, week_ending_iso) giving the inc-super multiplier.

    Three tiers, best first:

      1. ACTUAL — Xero paid this person in this week. Use exactly what it paid.
         Closed weeks are simply not estimated.
      2. TRAILING — Xero knows the person but not this week yet (the OPEN week,
         which is the number Zak reads at 9am). Use their own recent effective
         rate. A junior on 0% stays on 0%; someone who just turned 18 moves to
         12% the week Xero says so, not the week we guess.
      3. DEFAULT — Xero has never seen them (a brand-new hire, or pedro f whom
         payroll has never paid). Only here is a flat rate the right answer,
         and it is the statutory one.

    Tier 2 is what stops the live week disagreeing with the same week once it
    closes. Before this, the open week ran ~0.8% pessimistic and then moved
    under Zak the next morning for no reason he could see.

    as_of: restrict BOTH tiers to weeks strictly before this one. Only the
    backtest passes it — measuring the estimate against a trailing rate that
    saw the answer would flatter it, and a backtest that lies is worse than no
    backtest.
    """
    TRAILING_WEEKS = 8

    def _trailing(xn, before=None):
        weeks = [k for k in sorted(xero_pay.get(xn, {})) if before is None or k < before]
        weeks = weeks[-TRAILING_WEEKS:]
        w = sum(xero_pay[xn][k] for k in weeks)
        s = sum(xero_super.get(xn, {}).get(k, 0) for k in weeks)
        # No wages in the window -> no opinion; fall through to the default.
        return (1.0 + s / w) if w > 0 else None

    cache = {}

    def mult(employee_id, week_key):
        xn = emp_map.get(str(employee_id))
        if xn:
            if as_of is None:
                w = xero_pay.get(xn, {}).get(week_key)
                s = xero_super.get(xn, {}).get(week_key)
                if w and s is not None:
                    return 1.0 + s / w                  # 1. actual
            key = (xn, week_key if as_of is not None else None)
            if key not in cache:
                cache[key] = _trailing(xn, week_key if as_of is not None else None)
            if cache[key] is not None:
                return cache[key]                       # 2. trailing
        return 1.0 + default_rate                       # 3. statutory fallback

    return mult
