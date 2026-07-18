"""Propose Deputy -> Xero aliases by WEEK ALIGNMENT. Suggests only; never writes.

THE PROBLEM
-----------
rebuild_wages reports 66 people who worked but have no Xero payslip, worth
$333,746 of Deputy-costed labour. They are not unpaid — 197 Deputy accounts are
unmatched, and almost every one is a first name or nickname ("Agustin", "Audi",
"Avee", "Billy") where Xero holds the legal name. The cost lands on a venue from
Deputy and ALSO in the corp-payroll residual from Xero: counted twice, silently.

WHY NOT FUZZY-MATCH THE NAMES
-----------------------------
Because it is wrong in the worst possible way. build_employee_map's own comment
records the near-miss: a fuzzy match would have mapped Deputy 'Will N' onto Xero
'Toby Williams' (substring 'will' in 'Williams'). And this repo already holds
THREE similar names that are three different people — Olly (Olliver Case,
kitchen), Oliver (Oliver Iaccarino, OWNER), Olivia Giuliano / Olivia Allen-Hall.
Mapping an owner onto a venue wage line is a five-figure error that reconciles
perfectly.

WHAT THIS DOES INSTEAD
----------------------
A person's weeks are a fingerprint. If Deputy id 189 logged hours in exactly the
60 weeks Xero paid "Camila Green", and in no others, that is not a coincidence —
and unlike a name, it cannot be shared by two different people who happen to be
called the same thing.

Scored by Jaccard overlap of the two week-sets, and — the decisive test — how
many weeks the Deputy person worked but Xero did NOT pay them. For a true match
that number should be ~0: you cannot work a week and not be paid for it.

    DEPUTY_TOKEN=... python scripts/suggest_employee_aliases.py

Output is a list for Zak to CONFIRM. Nothing is written. Every alias in
build_employee_map.py was verified by hand and that is the standard; this only
does the searching, not the deciding.
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("DEPUTY_TOKEN")
if not TOKEN:
    sys.exit("DEPUTY_TOKEN not set")
HOST = "https://831d4015123255.au.deputy.com"
OFFSET_H = 10

xero = json.loads((ROOT / "data" / "xero_pay_weekly.json").read_text())
emap = json.loads((ROOT / "data" / "employee_map.json").read_text())
mapped_xero = set(emap.values())
cfg = json.loads((ROOT / "scripts" / "salaried_employees.json").read_text())
owners = set(cfg["_corp_payroll_only"]["names"])
exempt = set(cfg.get("_xero_exempt", {}).get("ids", {}))


def post(path, body):
    r = urllib.request.Request(HOST + path, data=json.dumps(body).encode(),
                               headers={"Authorization": f"OAuth {TOKEN}",
                                        "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read())


emps = post("/api/v1/resource/Employee/QUERY", {"search": {}, "max": 500})
names = {str(e["Id"]): (e.get("DisplayName") or "").strip() for e in emps}

# ---- Deputy: which weeks did each person actually work? ----
start = date(2024, 10, 21)
end = date.today() + timedelta(days=7)
t0 = int(datetime(start.year, start.month, start.day,
                  tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())
t1 = int(datetime(end.year, end.month, end.day,
                  tzinfo=timezone(timedelta(hours=OFFSET_H))).timestamp())

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
        wk = (d + timedelta(days=6 - d.weekday())).isoformat()
        e = str(ts.get("Employee"))
        d_weeks[e].add(wk)
        d_hours[e] += h
    if len(batch) < 500:
        break
    offset += 500
print(f"Deputy: {len(d_weeks)} people with hours")

x_weeks = {n: {w for w, v in ws.items() if v > 0} for n, ws in xero.items()}
unmapped_x = {n: w for n, w in x_weeks.items()
              if n not in mapped_xero and n not in owners and w}
print(f"Xero: {len(unmapped_x)} paid people not yet mapped to a Deputy id\n")

rows = []
for eid, dw in d_weeks.items():
    # EXEMPT PEOPLE ARE CHECKED, NOT SKIPPED (fixed 2026-07-18).
    #
    # This used to `continue` on `eid in exempt`, and that is exactly how Long
    # Long (id 225) hid for 39 weeks. He was exempted on my claim that Xero had
    # never paid him "under any name" — a claim I tested by SEARCHING THE NAME
    # "Long Long", which could never have found him, because his Xero name is
    # Teramet Tongsong ($26,293.62). This tool would have caught it on week
    # alignment. It didn't get the chance: the exemption suppressed the only
    # check capable of questioning the exemption.
    #
    # An exemption is a CLAIM ("this person and this payroll have never met").
    # A claim is the thing you test most, not the thing you stop testing.
    if eid in emap or not dw:
        continue
    best = []
    for xn, xw in unmapped_x.items():
        inter = dw & xw
        if not inter:
            continue
        union = dw | xw
        jac = len(inter) / len(union)
        d_only = len(dw - xw)          # worked, not paid -> should be ~0
        best.append((jac, d_only, len(inter), len(dw), len(xw), xn))
    best.sort(key=lambda t: (-t[0], t[1]))
    if best:
        rows.append((d_hours[eid], eid, names.get(eid, "?"), best[:2]))

rows.sort(reverse=True)
print("=" * 96)
print("CANDIDATES — highest confidence first. CONFIRM EACH BEFORE ADDING.")
print("=" * 96)
print("  d_only = weeks the Deputy person worked but Xero did NOT pay them.")
print("  For a true match that is ~0. A big number means it is NOT the same person.\n")
strong = 0
for hrs, eid, dn, best in rows:
    if hrs < 20:
        continue
    jac, d_only, inter, ndw, nxw, xn = best[0]
    verdict = ("STRONG" if jac >= 0.8 and d_only <= 1 else
               "likely" if jac >= 0.6 and d_only <= 3 else "weak")
    if verdict == "STRONG":
        strong += 1
    # An exempt person with a plausible candidate is the LOUDEST thing here: the
    # exemption asserts Xero has never paid them, and a candidate says otherwise.
    # If that assertion is wrong, their Deputy cost is on a venue AND their Xero
    # pay is in the corp-payroll residual — double counted, with the group total
    # tying perfectly the whole time. That is how Long Long hid for 39 weeks.
    ex = "  ⚠️ EXEMPT — but this says Xero MAY pay them. Check before trusting." \
         if eid in exempt else ""
    print(f"  deputy {eid:>4} {dn[:22]:22} {hrs:>7.1f}h  ->  {xn[:28]:28} "
          f"overlap {inter:>2}/{ndw:>2}wk  d_only {d_only:>2}  jac {jac:.2f}  [{verdict}]{ex}")
    if len(best) > 1 and best[1][0] >= 0.5:
        j2, d2, i2, _, _, x2 = best[1]
        print(f"       {'runner-up:':>27} {x2[:28]:28} "
              f"overlap {i2:>2}      d_only {d2:>2}  jac {j2:.2f}  <- ambiguous, be careful")

print(f"\n  {strong} STRONG candidate(s). Add confirmed ones to ALIASES in")
print("  scripts/build_employee_map.py — by hand, one at a time, like every other.")
