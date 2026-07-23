"""Pull what payroll ACTUALLY paid, per employee per payroll week, from Xero.

Runs on the Mac (the Xero refresh token lives in .secrets/ and rotates on every
use, so it can't live in Actions). Writes TWO files:

    data/xero_pay_weekly.json    {"Firstname Lastname": {"2026-07-12": 1442.31}}
    data/xero_super_weekly.json  {"Firstname Lastname": {"2026-07-12":  173.08}}

Why this exists: Deputy knows who clocked on; only Xero knows what they were
paid. Everything the pipeline synthesizes is an estimate standing in for this
file. For any week Xero has posted, we don't have to estimate at all.

SUPER IS NOT 12% (added 2026-07-18)
-----------------------------------
The pipeline grossed every wage by a flat 12%. Payroll doesn't work that way:
super is payable on ORDINARY TIME earnings, so overtime and some allowances
attract none. Week ending 2026-07-12, Xero's effective rate was 11.79%, and it
is not uniform — Herminder Khera 12.00%, Devon Lukiana 11.91%, Annarita
Dagostino 11.55%, David Armour 11.31%. Flat 12% overstated wages by $52.63 that
week (~$2,737/yr).

So super is now pulled per person per week and used as an actual, not a rate.
Two files rather than one nested structure, because xero_pay_weekly.json's
shape is load-bearing in rebuild_wages and build_employee_map and this change
has no business breaking either.

It also recovers people Xero's Employees endpoint won't return: that endpoint
lists ACTIVE staff only (45), but 100 pay runs name 122 — every chef who has
since left is in here, and their cost was missing from history entirely.

    python3 scripts/pull_xero_pay_weekly.py
"""
import base64, json, re, sys, time, urllib.parse, urllib.request, urllib.error
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = Path("/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting/.secrets")
OUT = ROOT / "data" / "xero_pay_weekly.json"
OUT_SUPER = ROOT / "data" / "xero_super_weekly.json"
OUT_LEAVE = ROOT / "data" / "xero_leave_weekly.json"

app = json.loads((SECRETS / "xero_app.json").read_text())
cf = SECRETS / "xero_token_cache.json"
cache = json.loads(cf.read_text())


def refresh():
    basic = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                   "refresh_token": cache["refresh_token"]}).encode()
    r = urllib.request.Request("https://identity.xero.com/connect/token", data=body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    t = json.loads(urllib.request.urlopen(r).read())
    cache["refresh_token"] = t["refresh_token"]      # rotates — persist immediately
    cf.write_text(json.dumps(cache, indent=1))
    return t["access_token"]


TOK = refresh()


def get(u, retry=True):
    global TOK
    q = urllib.request.Request(u, headers={"Authorization": f"Bearer {TOK}",
        "Xero-tenant-id": cache["tenant_id"], "Accept": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(q).read())
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry:
            TOK = refresh(); return get(u, False)
        if e.code == 429:
            time.sleep(20); return get(u, retry)
        raise


def xdate(s):
    m = re.search(r"/Date\((\d+)", s or "")
    return date.fromtimestamp(int(m.group(1)) / 1000) if m else None


runs = [r for r in get("https://api.xero.com/payroll.xro/1.0/PayRuns").get("PayRuns", [])
        if xdate(r.get("PayRunPeriodEndDate"))]
runs.sort(key=lambda r: xdate(r["PayRunPeriodEndDate"]))
pay = defaultdict(dict)
sup = defaultdict(dict)
lev = defaultdict(dict)   # leave $ per person per week (from payslip LeaveEarningsLines)


def leave_amount(payslip_detail):
    """Sum the leave paid on a detailed payslip. Xero AU Payroll v1 exposes leave
    as LeaveEarningsLines; each line's dollar value is Amount, or NumberOfUnits x
    RatePerUnit, or FixedAmount depending on how the pay item is set up. Try each
    so a config difference can't silently zero it. Returns ex-super dollars."""
    total = 0.0
    for ln in (payslip_detail.get("LeaveEarningsLines") or []):
        amt = ln.get("Amount")
        if amt is None:
            u, rate = ln.get("NumberOfUnits"), ln.get("RatePerUnit")
            amt = (u or 0) * (rate or 0) if (u is not None and rate is not None) else (ln.get("FixedAmount") or 0)
        total += amt or 0
    return round(total, 2)


_leave_probe = []
for i, r in enumerate(runs):
    pr = (get(f"https://api.xero.com/payroll.xro/1.0/PayRuns/{r['PayRunID']}").get("PayRuns") or [{}])[0]
    wk = str(xdate(r["PayRunPeriodEndDate"]))
    for s in pr.get("Payslips", []) or []:
        nm = f"{s.get('FirstName','')} {s.get('LastName','')}".strip()
        pay[nm][wk] = round(pay[nm].get(wk, 0) + (s.get("Wages") or 0), 2)   # supplementary runs add
        sup[nm][wk] = round(sup[nm].get(wk, 0) + (s.get("Super") or 0), 2)
        # detailed payslip for the leave breakdown (summary payslip has none)
        pid = s.get("PayslipID")
        if pid:
            try:
                det = (get(f"https://api.xero.com/payroll.xro/1.0/Payslips/{pid}").get("Payslips") or [{}])[0]
                la = leave_amount(det)
                if la:
                    lev[nm][wk] = round(lev[nm].get(wk, 0) + la, 2)
                    if len(_leave_probe) < 5:
                        _leave_probe.append(f"{nm} {wk}: ${la:,.2f}")
                time.sleep(0.2)
            except Exception as e:
                print(f"  (leave detail skipped for {nm} {wk}: {e})")
    time.sleep(0.3)

OUT.write_text(json.dumps({k: dict(sorted(v.items())) for k, v in sorted(pay.items())}, indent=1))
OUT_SUPER.write_text(json.dumps({k: dict(sorted(v.items())) for k, v in sorted(sup.items())}, indent=1))
OUT_LEAVE.write_text(json.dumps({k: dict(sorted(v.items())) for k, v in sorted(lev.items())}, indent=1))
print(f"{len(runs)} pay runs, {len(pay)} employees -> {OUT}")
print(f"{' ' * len(str(len(runs)))} super for {len(sup)} employees -> {OUT_SUPER}")
_leave_total = sum(v for e in lev.values() for v in e.values())
print(f"{' ' * len(str(len(runs)))} leave for {len(lev)} employees (${_leave_total:,.2f} total) -> {OUT_LEAVE}")
if _leave_probe:
    print("   sample leave lines:", "; ".join(_leave_probe))
if _leave_total == 0:
    print("   !! leave came back ZERO across all payslips — LeaveEarningsLines may be")
    print("      named differently on this Xero plan. rebuild_wages stays inert (no")
    print("      closed-week leave split) until this is non-zero. Inspect one payslip.")

# Sanity: the whole point is that this is NOT 12%. If it comes back at exactly
# 12.00% across the board, the Super field didn't populate and we've just
# rebuilt the flat rate with extra steps.
_w = sum(v for e in pay.values() for v in e.values())
_s = sum(v for e in sup.values() for v in e.values())
print(f"\neffective super across all pay runs: {_s / _w * 100:.3f}%  (${_s:,.2f} on ${_w:,.2f})")
if _s == 0:
    print("!! Super came back ZERO — the payslip summary has no Super field on this")
    print("   Xero plan/endpoint. DO NOT use xero_super_weekly.json; rebuild_wages")
    print("   will fall back to 12%. Investigate before trusting any wage number.")
