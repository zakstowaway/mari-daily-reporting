"""Is EVERY wage dollar reconciled? Answers it exhaustively, two independent ways.

Zak asked the question directly (2026-07-18). This makes it answerable on demand
and guards against regressions — a new unmapped payee, or a mapping that breaks,
shows up here immediately.

LEG 1 — PERSON: every dollar Xero paid a person must land in one of:
    * a venue        (mapped Deputy id -> their cost reaches a venue wage line)
    * corp payroll   (owner: Oliver, Bryony)
    * a documented exception (Xero pays, no mappable Deputy shift — Zak-confirmed)
  Anything else is UNKNOWN and is a bug: that person's labour cost is sitting in
  the group residual (payroll-tax/WC overhead bucket) instead of a venue.

LEG 2 — P&L: every dollar of Xero group_payroll must split into
    venue wages + owners + (payroll tax + WC + timing).
  This ties to $0 by construction; it's here as a cross-check.

Exit non-zero if any person's pay is UNKNOWN. Read-only.

    python3 scripts/reconcile_wages.py
"""
import json
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


pay = json.loads((D / "xero_pay_weekly.json").read_text())
sup = json.loads((D / "xero_super_weekly.json").read_text())
emap = json.loads((D / "employee_map.json").read_text())
cfg = json.loads((ROOT / "scripts" / "salaried_employees.json").read_text())
OWNERS = set(cfg["_corp_payroll_only"]["names"])
mapped = set(emap.values())

# Xero payees with no mappable Deputy shift, each classified by Zak. Keep in sync
# with salaried_employees.json _xero_exempt._2026-07-18b_xero_no_deputy.
NO_DEPUTY = {
    "Paula Tarrago": "group marketing (social media 2022-Dec 2025 payout) — overhead",
    "Rhianna Grove": "Stowaway staff, Deputy account missing — belongs to Stow",
}

print("=" * 72)
print("LEG 1 — every dollar Xero paid a person")
print("=" * 72)
tot = own = ven = nod = unk = 0.0
unknown = {}
for n, ws in pay.items():
    p = sum(ws.values()) + sum(sup.get(n, {}).values())
    tot += p
    if n in OWNERS:
        own += p
    elif n in mapped:
        ven += p
    elif n in NO_DEPUTY:
        nod += p
    else:
        unk += p
        unknown[n] = p
print(f"  total Xero payslips (wages+super), all history : ${tot:,.2f}")
print(f"    venue (mapped staff)          ${ven:>13,.2f}   {ven / tot * 100:6.2f}%")
print(f"    owners (corp payroll)         ${own:>13,.2f}   {own / tot * 100:6.2f}%")
print(f"    documented no-Deputy          ${nod:>13,.2f}   {nod / tot * 100:6.2f}%")
print(f"    UNKNOWN (bug)                 ${unk:>13,.2f}   {unk / tot * 100:6.3f}%")
for n, v in sorted(unknown.items(), key=lambda kv: -kv[1]):
    print(f"        ${v:,.2f}  {n}  <- unmapped; run scripts/match_xero_to_deputy.py")

print("\n" + "=" * 72)
print("LEG 2 — every dollar of Xero group_payroll (P&L cross-check)")
print("=" * 72)
oh = [r for r in csv.DictReader(open(D / "xero_overheads_monthly.csv")) if r.get("group_payroll")]
months = {r["month"] for r in oh}
gp = sum(num(r["group_payroll"]) for r in oh)
venue_wages = sum(num(r["wages_dollars"]) + num(r.get("leave_dollars"))
                  for v in ("stow", "hg", "mari")
                  for r in csv.DictReader(open(D / f"{v}_daily_history.csv")) if r["date"][:7] in months)
own_pl = sum(pay[n][wk] + sup.get(n, {}).get(wk, 0)
             for n in OWNERS for wk in pay.get(n, {}) if wk[:7] in months)
oncost = gp - venue_wages - own_pl
print(f"  group_payroll ({len(months)} months)     ${gp:>13,.2f}")
print(f"    staff wages on venues       ${venue_wages:>13,.2f}")
print(f"    owners (corp payroll)       ${own_pl:>13,.2f}")
print(f"    payroll tax + WC + timing   ${oncost:>13,.2f}   (overheads, lumpy)")
print(f"    {'-' * 40}")
print(f"    unaccounted                 ${gp - (venue_wages + own_pl + oncost):>13,.2f}")

print("\n" + "=" * 72)
if unk < 0.01:
    print("✓ EVERY wage dollar is classified — venue, owner, or documented exception.")
    sys.exit(0)
print(f"!! ${unk:,.2f} of wages is UNKNOWN — a person's cost is in overheads, not a venue.")
sys.exit(1)
