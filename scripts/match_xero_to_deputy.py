"""REVERSE matcher: for each Xero payee we can't place, list the Deputy people
who worked the weeks they were paid — archived accounts included.

WHY
---
suggest_employee_aliases.py goes Deputy -> Xero and shows one best candidate per
Deputy id. That is the wrong direction for the question Zak has now: "who ARE
these four Xero people?" They are almost certainly ARCHIVED Deputy accounts —
Deputy's People screen shows 41 active out of 280 total, so 239 accounts are
invisible in the UI and every departed chef is in there.

These four are paid by Xero and map to no Deputy id, so their cost never reaches
a venue — it falls into the corp-payroll residual, where owner salary lives.
Every venue is understated by their pay.

  Angela Rinaudo          5 wks  $2,742.30
  Fatima Mitra            5 wks  $2,563.85
  Nattachat Thongsrinoon  4 wks  $2,387.48
  Agustin Neme            3 wks  $2,171.15

HOW TO READ IT
--------------
x_only = weeks Xero PAID them but the Deputy candidate did NOT work. For a true
match that should be ~0: payroll does not pay people for weeks they never
worked. It is the decisive column — a high jaccard with a high x_only is two
people who happened to overlap, not one person under two names.

Nothing is written. Long Long spent 39 weeks double-counted because I trusted a
name search over Zak; the tool searches, Zak decides.

    DEPUTY_TOKEN=... python scripts/match_xero_to_deputy.py
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("DEPUTY_TOKEN")
if not TOKEN:
    sys.exit("DEPUTY_TOKEN not set")
HOST = "https://831d4015123255.au.deputy.com"

xero = json.loads((ROOT / "data" / "xero_pay_weekly.json").read_text())
emap = json.loads((ROOT / "data" / "employee_map.json").read_text())
cfg = json.loads((ROOT / "scripts" / "salaried_employees.json").read_text())
owners = set(cfg["_corp_payroll_only"]["names"])
mapped = set(emap.values())


def post(path, body):
    r = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
                               headers={"Authorization": f"OAuth {TOKEN}",
                                        "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())


emps = post("/api/v1/resource/Employee/QUERY", {"search": {}, "max": 500})
names, active = {}, {}
for e in emps:
    i = str(e["Id"])
    names[i] = (e.get("DisplayName") or "").strip()
    active[i] = bool(e.get("Active"))
print(f"Deputy accounts: {len(emps)}  |  active {sum(active.values())}  |  "
      f"ARCHIVED {len(emps) - sum(active.values())}")

start, end = date(2024, 10, 21), date.today() + timedelta(days=7)
d_weeks = defaultdict(set)
d_hours = defaultdict(float)
offset = 0
while True:
    batch = post("/api/v1/resource/Timesheet/QUERY", {
        "search": {"s1": {"field": "Date", "type": "ge", "data": str(start)},
                   "s2": {"field": "Date", "type": "le", "data": str(end)}},
        "start": offset, "max": 500,
    })
    for ts in batch:
        h = ts.get("TotalTime") or 0
        if h <= 0:
            continue
        d = date.fromisoformat(str(ts["Date"])[:10])
        e = str(ts.get("Employee"))
        d_weeks[e].add((d + timedelta(days=6 - d.weekday())).isoformat())
        d_hours[e] += h
    if len(batch) < 500:
        break
    offset += 500

unmapped_x = {n: {w for w, v in ws.items() if v > 0}
              for n, ws in xero.items()
              if n not in mapped and n not in owners}
unmapped_x = {n: w for n, w in unmapped_x.items() if w}
paid = {n: sum(xero[n].values()) for n in unmapped_x}

print(f"Xero payees with no Deputy id: {len(unmapped_x)}"
      f"  (${sum(paid.values()):,.2f})\n")
print("=" * 100)
print("WHO ARE THEY? Deputy accounts that worked the weeks Xero paid them.")
print("=" * 100)
print("  x_only = weeks Xero PAID them that the Deputy candidate did NOT work.")
print("  ~0 for a true match — payroll doesn't pay people for weeks they never worked.\n")

for xn in sorted(unmapped_x, key=lambda n: -paid[n]):
    xw = unmapped_x[xn]
    if paid[xn] < 300:
        continue
    cands = []
    for eid, dw in d_weeks.items():
        if eid in emap:
            continue                      # already spoken for
        inter = xw & dw
        if not inter:
            continue
        cands.append((len(inter) / len(xw | dw), len(xw - dw), len(inter),
                      len(dw), eid))
    cands.sort(key=lambda t: (t[1], -t[0]))
    ks = sorted(xw)
    print(f"\n  XERO: {xn}   {len(xw)} wks  ${paid[xn]:,.2f}   {ks[0]} .. {ks[-1]}")
    if not cands:
        print("        no Deputy account worked ANY of those weeks — they may never "
              "have been rostered at all.")
        continue
    for jac, x_only, inter, ndw, eid in cands[:4]:
        tag = "active " if active.get(eid) else "ARCHIVED"
        verdict = ("<-- STRONG" if x_only == 0 and jac >= 0.5 else
                   "<-- worth a look" if x_only <= 1 else "")
        print(f"        deputy {eid:>4} {names.get(eid, '?')[:24]:24} {tag}  "
              f"worked {inter:>2}/{len(xw):>2} of their paid weeks  "
              f"x_only {x_only:>2}  jac {jac:.2f}  {verdict}")
