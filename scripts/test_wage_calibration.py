"""Pins the calibration's guardrails.

WHAT THIS IS
------------
The estimate behind Zak's 9am wage number was measured (rebuild_wages
--backtest, 13 closed weeks, walk-forward) at:
    raw         MAE $1,065.67/wk   MAPE 3.99%    2/13 weeks within +/-2%
    calibrated  MAE $  485.71/wk   MAPE 1.82%    9/13 weeks within +/-2%

It works by carrying each person's own measured estimate-vs-Xero error forward.
That is powerful and therefore dangerous: a calibration with no brakes will
happily "fix" a broken input by scaling it, hiding the fault forever.

These tests are the brakes.

    python3.12 scripts/test_wage_calibration.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from wage_model import calibration_factor, CALIB_CLAMP, CALIB_MIN_WEEKS   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def wk(n):
    return [f"2026-{1 + i // 4:02d}-{1 + (i % 4) * 7:02d}" for i in range(n)]


print("=" * 78)
print("1. IT LEARNS THE ACTUAL ERROR")
ws = wk(8)
est = {w: 1000.0 for w in ws}
act = {w: 1100.0 for w in ws}
f, n = calibration_factor(est, act)
check("a consistent 10% under-estimate -> factor 1.10", abs(f - 1.10) < 1e-9, f"{f:.4f}, {n} wks")
f, n = calibration_factor({w: 1000.0 for w in ws}, {w: 950.0 for w in ws})
check("a consistent 5% over-estimate -> factor 0.95", abs(f - 0.95) < 1e-9, f"{f:.4f}")

print("\n2. IT REFUSES TO GUESS FROM NOTHING")
f, n = calibration_factor({ws[0]: 1000.0}, {ws[0]: 5000.0})
check("one week of evidence -> no correction", f == 1.0 and n == 0, f"{f:.4f}, {n} wks")
check(f"needs at least {CALIB_MIN_WEEKS} weeks", CALIB_MIN_WEEKS >= 3)
f, n = calibration_factor({}, {})
check("no history at all -> no correction", f == 1.0 and n == 0)

print("\n3. IT CANNOT SEE THE WEEK IT IS PREDICTING")
# Every prior week is clean; the week being predicted is a huge payout. If
# `before` leaks, the factor explodes.
est = {w: 1000.0 for w in ws}
act = {w: 1000.0 for w in ws}
act[ws[-1]] = 90000.0
f_leak, _ = calibration_factor(est, act)
f_safe, _ = calibration_factor(est, act, before=ws[-1])
check("without `before`, a termination payout poisons the factor", f_leak > 1.3, f"{f_leak:.3f}")
check("with `before`, it is unaffected", abs(f_safe - 1.0) < 1e-9, f"{f_safe:.4f}")

print("\n4. IT CLAMPS — a broken input must stay visible")
f, n = calibration_factor({w: 100.0 for w in ws}, {w: 4000.0 for w in ws})
check("a 40x error is clamped, not applied", f <= CALIB_CLAMP[1], f"{f:.3f} <= {CALIB_CLAMP[1]}")
f, n = calibration_factor({w: 4000.0 for w in ws}, {w: 100.0 for w in ws})
check("and clamped on the low side too", f >= CALIB_CLAMP[0], f"{f:.3f} >= {CALIB_CLAMP[0]}")
check("the clamp is tight enough to be a signal, not a shrug",
      CALIB_CLAMP[1] <= 1.5 and CALIB_CLAMP[0] >= 0.5, str(CALIB_CLAMP))

print("\n5. NEAR-ZERO WEEKS CARRY NO RATE INFORMATION")
# A week someone barely worked, or payroll hasn't touched, would wreck the ratio.
est = {w: 1000.0 for w in ws}
act = {w: 1000.0 for w in ws}
est[ws[0]] = 0.01
act[ws[0]] = 800.0
f, n = calibration_factor(est, act)
check("a ~$0 week is excluded, not divided by", abs(f - 1.0) < 1e-9, f"{f:.4f} from {n} wks")

print("\n6. THE PRODUCTION PATH ONLY CALIBRATES ESTIMATES")
src = (ROOT / "scripts" / "rebuild_wages.py").read_text()
check("gross() takes an `estimated` flag", "def gross(eid, ex, estimated=False)" in src)
# The Xero-sourced branch must NOT pass estimated=True. Calibrating a payslip
# against a factor derived from payslips turns a fact back into a guess.
xero_line = [l for l in src.splitlines() if "gross(eid, paid[eid]" in l]
check("the Xero-paid path is never calibrated",
      bool(xero_line) and not any("estimated=True" in l for l in xero_line),
      xero_line[0].strip() if xero_line else "not found")
check("the calibration file is only written on a long window",
      "(d_to - d_from).days > 60" in src)

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
sys.exit(1 if FAIL else 0)
