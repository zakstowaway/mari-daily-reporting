"""Reconcile scripts/salaried_employees.json against Xero payroll.

XERO IS THE SOURCE OF TRUTH, not Deputy (Zak, 2026-07-15). Deputy only knows who
clocked on; it returns Cost=0 for salaried staff and has no idea what anyone is
paid. So a new salary-earner is invisible to the pipeline until someone adds them
here by hand — and until then every shift they work is booked at $0, while their
real cost silently lands in the corp-payroll residual (Xero group payroll MINUS
Deputy group wages). The venue looks cheaper than it is and head office looks
dearer than it is.

That is exactly how Pujan Tamang ($75k, ~40h/wk in Stow Kitchen) went unnoticed.
This check exists so it can't happen again.

Reports three things:
  NEW      — salaried in Xero, absent here. Either add them (operational staff)
             or list them under _corp_payroll_only (owners).
  CHANGED  — salary differs from Xero. Xero wins.
  GONE     — here but no longer salaried/active in Xero.

Owners live in _corp_payroll_only and are expected to be absent from `employees`:
their pay reaches the P&L via the residual precisely BECAUSE they're not in Deputy.

Exit 1 if anything needs attention, so CI can shout.

    python scripts/check_salaried_roster.py
"""
import base64, json, sys, urllib.parse, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).parent
SECRETS = Path("/Users/Shared/ClaudeShared/STOW/Sales Reports/Daily Reporting/.secrets")
CFG = HERE / "salaried_employees.json"


def xero_session():
    app = json.loads((SECRETS / "xero_app.json").read_text())
    cache_f = SECRETS / "xero_token_cache.json"
    cache = json.loads(cache_f.read_text())
    basic = base64.b64encode(f"{app['client_id']}:{app['client_secret']}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "refresh_token",
                                   "refresh_token": cache["refresh_token"]}).encode()
    r = urllib.request.Request("https://identity.xero.com/connect/token", data=body,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    tok = json.loads(urllib.request.urlopen(r).read())
    # rotating refresh token — persist before anything can throw
    cache["refresh_token"] = tok["refresh_token"]
    cache_f.write_text(json.dumps(cache, indent=1))
    return tok["access_token"], cache["tenant_id"]


def get(url, tok, tid):
    r = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
        "Xero-tenant-id": tid, "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())


def main():
    cfg = json.loads(CFG.read_text())
    tok, tid = xero_session()
    ours = {e.get("xero_name", e["name"]): (eid, e["annual"])
            for eid, e in cfg["employees"].items()}
    owners = set(cfg.get("_corp_payroll_only", {}).get("names", []))

    xero = {}
    for e in get("https://api.xero.com/payroll.xro/1.0/Employees", tok, tid)["Employees"]:
        if e.get("Status") != "ACTIVE":
            continue
        d = get(f"https://api.xero.com/payroll.xro/1.0/Employees/{e['EmployeeID']}", tok, tid)
        x = (d.get("Employees") or [{}])[0]
        sal = next((l for l in ((x.get("PayTemplate") or {}).get("EarningsLines") or [])
                    if l.get("CalculationType") == "ANNUALSALARY"), None)
        if sal:
            xero[f"{e['FirstName']} {e['LastName']}"] = sal["AnnualSalary"]

    new = {n: a for n, a in xero.items() if n not in ours and n not in owners}
    changed = {n: (ours[n][1], a) for n, a in xero.items() if n in ours and abs(ours[n][1] - a) > 0.5}
    gone = {n: v for n, v in ours.items() if n not in xero}

    print(f"Xero salaried: {len(xero)} | our roster: {len(ours)} | owners (corp payroll): {len(owners)}")
    problems = 0
    if new:
        problems += len(new)
        print("\n*** NEW SALARY-EARNERS — not in salaried_employees.json ***")
        for n, a in sorted(new.items(), key=lambda kv: -kv[1]):
            print(f"    {n:<28}${a:>9,.0f}/yr  = ${a/52*1.12:>8,.2f}/wk inc super")
            print(f"    {'':28}every shift is costing $0 until this is fixed")
        print("    -> operational staff: add to `employees` with their Deputy employee id")
        print("    -> owners/directors:  add to `_corp_payroll_only.names`")
    if changed:
        problems += len(changed)
        print("\n*** SALARY CHANGED — Xero wins ***")
        for n, (o, x) in changed.items():
            print(f"    {n:<28}ours ${o:>9,.0f}  xero ${x:>9,.0f}  ({x-o:+,.0f})")
    if gone:
        print("\n*** NO LONGER SALARIED/ACTIVE IN XERO ***")
        for n, (eid, a) in gone.items():
            print(f"    {n:<28}(id {eid}, ${a:,.0f}) — left, or moved to hourly?")
    if not problems and not gone:
        print("\nOK — roster matches Xero.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
