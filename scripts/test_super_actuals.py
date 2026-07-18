"""Pins why super is NOT a flat 12%, so nobody restores the constant.

THE TRAP
--------
`SUPER_RATE = 0.12` reads like a fact. It is the current Superannuation
Guarantee rate, so grossing every wage by 1.12 looks obviously correct. It is
wrong twice over, and both are invisible unless you look at Xero:

  1. THE RATE CHANGED. 11% from 1 Jul 2023, 11.5% from 1 Jul 2024, 12% from
     1 Jul 2025. Most of our history predates 12% entirely.
  2. UNDER-18s WORKING <=30 h/wk GET NO SUPER AT ALL. Not a reduced rate —
     none. Marilyna's runs on teenage delivery drivers, so a flat 12% invented
     ~$5,600 of super for people legally entitled to zero, and Mari wore it.

Measured over 100 pay runs: actual $289,768.94 on $2,587,065.11 = 11.201%.
Flat 12% books $310,447.81 — $20,678.87 too much.

The signature that proves the zeros are real and not an API fault: a person's
rate steps 0% -> the exact statutory rate on ONE date and never returns. That's
an 18th birthday. Reef Poole 0% -> 11.50% on 2025-06-15 (the FY24-25 rate);
Toby Williams 0% -> 12.00% on 2026-03-15. A dropped-field bug cannot do that.

    python3.12 scripts/test_super_actuals.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else '!! FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


if not (D / "xero_super_weekly.json").exists():
    print("SKIP — data/xero_super_weekly.json not present; run pull_xero_pay_weekly.py")
    sys.exit(0)

pay = json.loads((D / "xero_pay_weekly.json").read_text())
sup = json.loads((D / "xero_super_weekly.json").read_text())

print("=" * 78)
print("1. THE SUPER FILE IS REAL — it ties to the Xero UI")
w = sum(v.get("2026-07-12", 0) for v in pay.values())
s = sum(v.get("2026-07-12", 0) for v in sup.values())
# Screenshot of payroll.xero.com, week ending 12 Jul 2026, status Posted.
check("wk 2026-07-12 earnings == Xero UI $25,487.13", abs(w - 25487.13) < 0.01, f"${w:,.2f}")
check("wk 2026-07-12 super    == Xero UI  $3,005.82", abs(s - 3005.82) < 0.01, f"${s:,.2f}")
check("super is not zero (the Super field populated)", s > 0)

print("\n2. SUPER IS NOT 12% — if it were, none of this would matter")
tw = sum(v for e in pay.values() for v in e.values())
ts = sum(v for e in sup.values() for v in e.values())
eff = ts / tw * 100
check("effective rate across all pay runs is well under 12%", eff < 11.6,
      f"{eff:.3f}%  (${ts:,.2f} on ${tw:,.2f})")
check("flat 12% overstates by a material amount", tw * 0.12 - ts > 10000,
      f"${tw * 0.12 - ts:,.2f}")

print("\n3. SOME PEOPLE LEGALLY GET NO SUPER — under 18, <=30 h/wk")
zero = [n for n, v in pay.items()
        if sum(v.values()) > 500 and sum(sup.get(n, {}).values()) == 0]
zw = sum(sum(pay[n].values()) for n in zero)
check("people with real wages and zero super exist", len(zero) >= 5,
      f"{len(zero)} people, ${zw:,.2f} of wages")
check("a flat 12% would invent super for them", zw * 0.12 > 3000,
      f"${zw * 0.12:,.2f} of phantom super")

print("\n4. THE ZEROS ARE AN 18th BIRTHDAY, NOT A DATA FAULT")
print("   (rate steps 0% -> statutory rate on ONE date, never returns)")


def steps_once(name):
    ws = sorted(pay.get(name, {}))
    rates = [(sup.get(name, {}).get(k, 0) / pay[name][k]) if pay[name][k] else 0 for k in ws]
    first = next((i for i, r in enumerate(rates) if r > 0.001), None)
    if first is None or first == 0:
        return False, "no transition"
    # every week before -> 0; a clear majority after -> paying super
    before_all_zero = all(r <= 0.001 for r in rates[:first])
    after = rates[first:]
    after_mostly_paid = sum(1 for r in after if r > 0.001) >= len(after) * 0.8
    return (before_all_zero and after_mostly_paid,
            f"{first} wks at 0%, then {after[0]*100:.2f}%")


for n in ("Toby Williams", "Reef Poole", "Ethan Reboredo"):
    if n in pay:
        ok, d = steps_once(n)
        check(f"{n}: one-way step to the statutory rate", ok, d)

print("\n5. THE CODE USES ACTUALS, NOT THE CONSTANT")
src = (ROOT / "scripts" / "rebuild_wages.py").read_text()
check("rebuild_wages reads xero_super_weekly.json", "xero_super_weekly.json" in src)
check("it grosses per person inside cost_week, not per dept bucket",
      "def gross(" in src and "SUPER_MULT_FOR(eid, wk_key)" in src)
# SUPER_MULT must survive ONLY as the fallback — the open week and people Xero
# has never paid. If it creeps back onto the dept buckets, super is flat again.
bucket_flat = [ln.strip() for ln in src.splitlines()
               if "SUPER_MULT" in ln and "|Kitchen" in ln or "SUPER_MULT" in ln and "|FOH" in ln]
check("no dept-bucket multiply reintroduced", not bucket_flat, str(bucket_flat[:2]))

print("\n6. THE OPEN WEEK USES EACH PERSON'S OWN RATE, NOT 12%")
print("   (before: the 9am number ran ~0.8% pessimistic, then moved at 6:30am")
print("    for no reason Zak could see)")
sys.path.insert(0, str(ROOT / "scripts"))
from wage_model import super_lookup   # noqa: E402

emap = json.loads((D / "employee_map.json").read_text())
rev = {v: k for k, v in emap.items()}
f = super_lookup(pay, sup, emap, 0.12)
OPEN = "2099-01-03"          # a week Xero will never have posted

# A closed week must still be the actual — tier 2 must not shadow tier 1.
for n in ("Herminder Khera", "David Armour"):
    did = rev.get(n)
    if did and "2026-07-12" in pay.get(n, {}):
        actual = 1 + sup[n]["2026-07-12"] / pay[n]["2026-07-12"]
        check(f"{n}: closed week still uses Xero's actual",
              abs(f(did, "2026-07-12") - actual) < 1e-9, f"{actual:.4f}")

# The whole point: a junior legally on 0% must STAY on 0% in the open week.
zero = [n for n, v in pay.items()
        if sum(v.values()) > 500 and sum(sup.get(n, {}).values()) == 0 and n in rev]
if zero:
    n = zero[0]
    check(f"{n}: zero-super junior stays at 0% in the open week",
          abs(f(rev[n], OPEN) - 1.0) < 1e-9, f"mult {f(rev[n], OPEN):.4f}")
else:
    print("   (no zero-super person is currently mapped to Deputy — skipped)")

check("someone Xero has never seen falls back to the statutory rate",
      abs(f("99999", OPEN) - 1.12) < 1e-9, f"{f('99999', OPEN):.4f}")

print("\n7. ONE DEFINITION — all three writers share it")
for f_, label in ((ROOT / "scripts/rebuild_wages.py", "rebuild_wages"),
                  (ROOT / "scripts/daily_aggregator.py", "daily_aggregator"),
                  (ROOT / "scripts/roster_pull.py", "roster_pull")):
    src = f_.read_text()
    check(f"{label} resolves super via wage_model.super_lookup",
          "super_lookup" in src)
# roster_pull's old flat gross-up sat on the dept totals, where the person is
# already gone. If it comes back, the roster and the actuals silently disagree.
rp = (ROOT / "scripts/roster_pull.py").read_text()
check("roster_pull no longer grosses the dept totals flat",
      "v * SUPER_MULT" not in rp)

print("\n" + "=" * 78)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
sys.exit(1 if FAIL else 0)
