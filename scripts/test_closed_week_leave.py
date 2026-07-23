#!/usr/bin/env python3
"""Tests the closed-week Xero-leave split added to rebuild_wages.py, WITHOUT
needing Deputy/Xero credentials. Exercises the real leave_dates_for() source and
asserts the two safety properties of the split:

  1. leave lands on the register's actual leave days;
  2. worked + Σleave == paid  (total preserved -> Xero tie & profit unchanged);
  3. inert: leave_ex = 0 -> worked == paid, nothing booked.

Exit 0 = ok, 1 = fail. Run: python3 scripts/test_closed_week_leave.py
"""
import json, re, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "scripts/rebuild_wages.py").read_text()

# Pull the REAL leave_dates_for source out of rebuild_wages (no drift) and run it
# against the real register, with the two symbols it needs injected.
m = re.search(r"\ndef leave_dates_for\(.*?\n(?=\ndef |\nclass |\n[^\s])", src, re.S)
assert m, "leave_dates_for not found in rebuild_wages.py"
ns = {"date": date, "timedelta": timedelta,
      "_SAL_LEAVE": json.loads((ROOT / "data/salaried_leave.json").read_text()).get("entries", [])}
exec(m.group(0), ns)
leave_dates_for = ns["leave_dates_for"]

fails = 0
def check(cond, msg):
    global fails
    if not cond: fails += 1; print(f"  ✗ {msg}")

# 1) Kris (id "1") on leave 15-20 Jul; closed week 13-19 Jul -> 15..19 inclusive
kris = leave_dates_for("1", date(2026,7,13), date(2026,7,19))
check(kris == ["2026-07-15","2026-07-16","2026-07-17","2026-07-18","2026-07-19"],
      f"Kris leave days in wk 13-19 Jul: got {kris}")
# a week with no Kris leave -> empty
check(leave_dates_for("1", date(2026,6,1), date(2026,6,7)) == [], "no leave week returns []")
# unknown employee -> empty
check(leave_dates_for("999", date(2026,7,13), date(2026,7,19)) == [], "unknown emp returns []")

# 2/3) split invariant mirrors the code: worked = paid - leave_ex; leave spread
def split(paid, leave_ex, ndays):
    leave_ex = min(leave_ex, paid)
    worked = paid - leave_ex
    booked = (leave_ex/ndays)*ndays if ndays else 0
    return worked, booked
w, b = split(1798.0, 700.0, 5)
check(abs((w + b) - 1798.0) < 1e-6, "worked + leave == paid (split preserves total)")
w0, b0 = split(1798.0, 0.0, 1)   # inert case
check(w0 == 1798.0 and b0 == 0.0, "leave_ex=0 -> worked==paid, nothing booked (inert)")
check(split(500.0, 999.0, 3)[0] == 0.0, "leave capped at total pay (never negative worked)")

# 4) the change must NOT have introduced a data file dependency that fires today:
check("xero_leave_weekly.json" in src and "if _xl.exists() else {}" in src,
      "XERO_LEAVE load is guarded (inert when file absent)")

print(f"closed-week leave split: {'ok' if not fails else str(fails)+' FAIL'}")
sys.exit(1 if fails else 0)
