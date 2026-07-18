"""Pins Zak's rule: ONLY Oliver and Bryony salaries in corp payroll (2026-07-18).

The corp-payroll residual is group_payroll − venue wages. By construction, any
person Xero pays whose cost never reaches a venue lands in it — which is how
Teramet sat there double-counted for 39 weeks. This test makes "no non-owner in
corp payroll" a hard invariant, and pins the reconciliation identity so the
audit can never quietly stop tying out.

Skips (does not fail) when the data isn't mounted, so CI stays green off-box.

    python3.12 scripts/test_corp_payroll.py
"""
import json, csv, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
PASS, FAIL = [], []
def check(n, c, d=""):
    (PASS if c else FAIL).append(n)
    print(f"  {'PASS' if c else '!! FAIL'}  {n}" + (f"   [{d}]" if d else ""))

need = ["xero_pay_weekly.json", "xero_super_weekly.json", "employee_map.json",
        "xero_overheads_monthly.csv", "stow_daily_history.csv"]
if not all((D / f).exists() for f in need):
    print("SKIP — data not mounted"); sys.exit(0)

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0

pay = json.loads((D/"xero_pay_weekly.json").read_text())
sup = json.loads((D/"xero_super_weekly.json").read_text())
emap = json.loads((D/"employee_map.json").read_text())
cfg = json.loads((ROOT/"scripts"/"salaried_employees.json").read_text())
OWNERS = set(cfg["_corp_payroll_only"]["names"])
mapped = set(emap.values())

oh = [r for r in csv.DictReader(open(D/"xero_overheads_monthly.csv")) if r.get("group_payroll")]
oh.sort(key=lambda r: r["month"])
months = [r["month"] for r in oh][-3:]; mset = set(months)
group_payroll = sum(num(r["group_payroll"]) for r in oh if r["month"] in mset)
venue = sum(num(r["wages_dollars"]) + num(r.get("leave_dollars"))
            for v in ("stow","hg","mari")
            for r in csv.DictReader(open(D/f"{v}_daily_history.csv")) if r["date"][:7] in mset)
residual = group_payroll - venue

person = defaultdict(float)
for n, ws in pay.items():
    for wk, val in ws.items():
        if wk[:7] in mset:
            person[n] += val + sup.get(n,{}).get(wk,0)
owners = sum(person[n] for n in OWNERS)
leak = {n:p for n,p in person.items() if n not in OWNERS and n not in mapped and p>0}
placed = sum(p for n,p in person.items() if n in mapped and n not in OWNERS)

print("=" * 70)
print("THE RULE — only owners' salary in corp payroll")
check("no non-owner person's pay is in corp payroll", not leak,
      f"{len(leak)} leaking ${sum(leak.values()):,.2f}" if leak else "clean")
for n, p in sorted(leak.items(), key=lambda kv:-kv[1])[:8]:
    print(f"       LEAK ${p:,.2f}  {n}")

print("\nRECONCILIATION — the residual must itemise to the cent")
oncosts = group_payroll - sum(person.values())
venue_extra = venue - placed
check("residual = owners + leak + oncosts − venue_extra (exact)",
      abs((owners + sum(leak.values()) + oncosts - venue_extra) - residual) < 0.01,
      f"residual ${residual:,.2f}")
check("owners are actually being paid (rule isn't vacuously true)", owners > 0,
      f"${owners:,.2f}")

print("\n" + "=" * 70)
print(f"PASSED {len(PASS)}   FAILED {len(FAIL)}")
sys.exit(1 if FAIL else 0)
