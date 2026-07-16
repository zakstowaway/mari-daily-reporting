"""Pull what payroll ACTUALLY paid, per employee per payroll week, from Xero.

Runs on the Mac (the Xero refresh token lives in .secrets/ and rotates on every
use, so it can't live in Actions). Writes data/xero_pay_weekly.json:

    {"Firstname Lastname": {"2026-07-12": 1442.31, ...}, ...}

Why this exists: Deputy knows who clocked on; only Xero knows what they were
paid. Everything the pipeline synthesizes is an estimate standing in for this
file. For any week Xero has posted, we don't have to estimate at all.

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
for i, r in enumerate(runs):
    pr = (get(f"https://api.xero.com/payroll.xro/1.0/PayRuns/{r['PayRunID']}").get("PayRuns") or [{}])[0]
    wk = str(xdate(r["PayRunPeriodEndDate"]))
    for s in pr.get("Payslips", []) or []:
        nm = f"{s.get('FirstName','')} {s.get('LastName','')}".strip()
        pay[nm][wk] = round(pay[nm].get(wk, 0) + (s.get("Wages") or 0), 2)   # supplementary runs add
    time.sleep(0.3)

OUT.write_text(json.dumps({k: dict(sorted(v.items())) for k, v in sorted(pay.items())}, indent=1))
print(f"{len(runs)} pay runs, {len(pay)} employees -> {OUT}")
