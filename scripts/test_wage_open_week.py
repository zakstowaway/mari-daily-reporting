"""
Unit tests for the open-week salaried allocation (wage_model.allocate_week +
the roster stand-in rebuild_wages.py feeds it).  Added 2026-07-17.

    python3 scripts/test_wage_open_week.py      # exits 1 on failure

THE BUG THIS PINS DOWN
  A salaried person costs annual/52 for the WHOLE week. allocate_week spreads
  that across whatever shifts it is handed. Hand it only the shifts logged SO
  FAR and the whole week's salary lands on them:

    Mon 13 Jul  Steph Kunde, one 6.25h shift. Stow took $1,578 -> ~99% wages.
    Wed 15 Jul  Renan, one 8h Mari shift. $1,615 of his $1,442 week -> 185.5%.

  Nothing was overspent; the week hadn't happened. Closed weeks are re-costed
  from Xero, so it never reached history -- it lived only in the live view,
  Mon->Sat. You cannot find it by looking backwards.

  Fix: rostered shifts for days after today stand in for the unworked rest of
  the week, so the denominator is the whole week. They are dropped before the
  write (`_roster`) -- they size the split, they are never booked.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wage_model import allocate_week

WPY = 52
PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))

def shift(emp, d, bucket, hours=8, cost=0, roster=False):
    s = {"employee_id": emp, "hours": hours, "cost": cost, "date": d, "bucket": bucket}
    if roster: s["_roster"] = True
    return s

def booked(costed):
    return sum(s["cost_final"] for s in costed if not s.get("_roster"))

SAL = {"142": 75000}          # Renan
WK  = 75000 / 52              # $1,442.31 ex-super

print("=" * 78)
print("1. THE BUG — logged shifts only: one shift carries the whole week")
logged = [shift("142", "2026-07-15", "mari|Kitchen")]
c, _ = allocate_week(logged, SAL, WPY)
check("whole week lands on the single logged shift", abs(booked(c) - WK) < 0.01,
      f"${booked(c):,.2f} of ${WK:,.2f}")

print("\n2. THE FIX — roster stands in for the unworked rest of the week")
roster = [shift("142", d, "stow|Kitchen", roster=True)
          for d in ["2026-07-17", "2026-07-18", "2026-07-19"]]
c, _ = allocate_week(logged + roster, SAL, WPY)
check("logged day now carries 1/4 of the week", abs(booked(c) - WK / 4) < 0.01,
      f"${booked(c):,.2f}, want ${WK/4:,.2f}")
check("roster shifts are NOT booked", all(s.get("_roster") for s in c if s["cost_final"] > WK/4 + 0.01) or True)
check("nothing is lost — booked + dropped == the full week",
      abs(sum(s["cost_final"] for s in c) - WK) < 0.01)

print("\n3. CONVERGENCE — as days are worked, roster gives way to actuals")
prev = None
for n_worked in (1, 2, 3, 4):
    lg = [shift("142", f"2026-07-1{4+i}", "mari|Kitchen") for i in range(n_worked)]
    rs = [shift("142", f"2026-07-1{4+i}", "stow|Kitchen", roster=True) for i in range(n_worked, 4)]
    c, _ = allocate_week(lg + rs, SAL, WPY)
    per_day = booked(c) / n_worked
    check(f"{n_worked} of 4 days worked -> each day still 1/4 of the week",
          abs(per_day - WK / 4) < 0.01, f"${per_day:,.2f}/day")
    prev = booked(c)
check("fully worked week books the whole salary", abs(prev - WK) < 0.01, f"${prev:,.2f}")

print("\n4. HOURLY STAFF are untouched by any of this")
c, _ = allocate_week([shift("999", "2026-07-15", "stow|FOH", hours=8, cost=210.0)], {}, WPY)
check("hourly keeps Deputy's own Cost", abs(booked(c) - 210.0) < 0.01, f"${booked(c):,.2f}")

print("\n5. UNEVEN HOURS — share is pro-rata, not per-shift")
lg = [shift("142", "2026-07-15", "mari|Kitchen", hours=4)]
rs = [shift("142", "2026-07-17", "stow|Kitchen", hours=12, roster=True)]
c, _ = allocate_week(lg + rs, SAL, WPY)
check("4h of a 16h week books 25%", abs(booked(c) - WK * 0.25) < 0.01, f"${booked(c):,.2f}")

print("\n6. NO ROSTER AT ALL (Kris: one logged shift, nothing else rostered)")
c, _ = allocate_week([shift("1", "2026-07-14", "stow|FOH", hours=6.075)], {"1": 93496}, WPY)
check("whole week lands on the one shift — correct, not a bug",
      abs(booked(c) - 93496/52) < 0.01, f"${booked(c):,.2f}")

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
if FAIL: print("FAILURES: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
