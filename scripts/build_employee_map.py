"""Map Deputy employee ids -> Xero payroll names.

Needed because the two systems disagree on names: Deputy's 'Vincent' is Xero's
'Vincentius Adijaya', 'Min' is 'Herminder Khera', 'Zak' is 'Zakaria Britton'.
Get this wrong and you attribute one person's pay to another, so:

  * exact full-name matches are accepted automatically;
  * everything else must be listed in ALIASES below, by hand;
  * anything still unmatched is REPORTED and left alone — rebuild_wages falls
    back to its estimate rather than guessing.

A fuzzy first-name match would have mapped Deputy 'Will N' onto Xero 'Toby
Williams' (substring 'will' in 'Williams'). That is exactly the class of error
this file exists to prevent.

    DEPUTY_TOKEN=... python scripts/build_employee_map.py
"""
import json, os, sys, urllib.request
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("DEPUTY_TOKEN")
if not TOKEN: sys.exit("DEPUTY_TOKEN not set")

# Deputy display name -> Xero payslip name. Verified individually against pay
# history (hours worked, dates employed, weekly amounts) — not guessed.
ALIASES = {
    "Vincent": "Vincentius Adijaya",
    "Min": "Herminder Khera",
    "Zak": "Zakaria Britton",
    "Will N": "William Norris",
    "Royani": "Royani Royani",
    "Marssheel": "Marssheel Marssheel",
    "Devon Lukiana": "Devon Saputra Lukiana",
    "Maria Flor Da Silva Quelhas Campinos Pocas": "Maria Flor Da Silva Quelhas Campinos Pocas",
    "Emily": "Emily Duncan",
    "Aleisha": "Aleisha Armitage",
}

xero = json.loads((ROOT / "data" / "xero_pay_weekly.json").read_text())
xnames = set(xero)

req = urllib.request.Request("https://831d4015123255.au.deputy.com/api/v1/resource/Employee/QUERY",
    data=json.dumps({"search": {}, "max": 500}).encode(),
    headers={"Authorization": f"OAuth {TOKEN}", "Content-Type": "application/json"})
emps = json.loads(urllib.request.urlopen(req).read())

mapping, unmatched = {}, []
for e in emps:
    eid = str(e.get("Id"))
    nm = (e.get("DisplayName") or "").strip()
    if not nm: continue
    if nm in ALIASES and ALIASES[nm] in xnames:
        mapping[eid] = ALIASES[nm]
    elif nm in xnames:
        mapping[eid] = nm
    else:
        unmatched.append((eid, nm))

out = ROOT / "data" / "employee_map.json"
out.write_text(json.dumps(dict(sorted(mapping.items(), key=lambda kv: int(kv[0]))), indent=1))
print(f"Deputy employees: {len(emps)} | mapped to Xero: {len(mapping)} -> {out}")
print(f"\nUNMATCHED ({len(unmatched)}) — no Xero payslip found; rebuild_wages will")
print("fall back to its estimate for these. Add to ALIASES if any are real:")
for eid, nm in sorted(unmatched, key=lambda t: t[1].lower()):
    print(f"  {eid:>5}  {nm}")
