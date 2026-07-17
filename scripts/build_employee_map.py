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
    # 2026-07-17 — both cost ~$501/wk of real wages that were falling to Deputy's
    # rate instead of Xero's figure, because Deputy holds a first name and Xero
    # holds the legal one.
    "denis": "denis ferreira rodrigues",   # proven: Deputy $233.56 == Xero $233.56, wk 07-12
    "Olivia": "Pongnapa Chonkaew",         # Zak: "pongnapa is likely olivia chef".
                                           # Corroborated: id 296 works Stow Kitchen +
                                           # Harry's Kitchen. NOT id 205 Olivia Giuliano,
                                           # who is a different person and already mapped.
    # Zak, 2026-07-17: "olly that works in the kitchen is olliver case. oliver on
    # deputy is oliver iaccarino owner." Deputy has THREE similar names and they
    # are three different people. Getting this wrong costs ~$325/wk one way, or
    # puts owner salary on a venue wage line the other.
    #   id 284 "Olly"   = Olliver Case, kitchen casual   -> map (below)
    #   id  24 "Oliver" = Oliver Iaccarino, OWNER        -> NEVER map. See below.
    "Olly": "Olliver Case",
    # 2026-07-17 — Deputy stores a first name / nickname, Xero the legal name, so
    # none of these ever matched and rebuild_wages fell back to Deputy's rate.
    # Each one VERIFIED by week-alignment over Mar–Jul: every week they logged
    # hours is a week Xero paid them (D-only = 0). That is a far stronger test
    # than name similarity, and it is the only reason these are here — a fuzzy
    # match on 'Hugh' would happily have taken 'Maisie Hughes'.
    #
    #   id   deputy       xero                 D wks  both  D-only
    #   283  liv          Olivia Allen-Hall      12     12     0
    #   276  Daniel       Daniel Biesty          14     14     0
    #   294  Mikel        Mikel Martin            7      7     0    (perfect both ways)
    #   269  Zach         Zach Davis             14     14     0
    #   232  Hugh         Hugh Yiend             13     13     0
    #   166  Rei          Rei Ikeda               9      9     0
    #   302  Archie       Archie Humphries        1      1     0    (thin — one week only.
    #        NOT id 55 'Archie Warneford', who is a different person.)
    "liv": "Olivia Allen-Hall",
    "Daniel": "Daniel Biesty",
    "Mikel": "Mikel Martin",
    "Zach": "Zach Davis",
    "Hugh": "Hugh Yiend",
    "Rei": "Rei Ikeda",
    "Archie": "Archie Humphries",
    # ⚠️ DO NOT add "Oliver" (id 24). He is Oliver Iaccarino, an owner, and lives
    # in _corp_payroll_only. His pay reaches corp payroll via the residual (Xero
    # group payroll MINUS Deputy group wages), so mapping him would move owner
    # salary onto a venue.
    #
    # That residual assumes owners are "never rostered in Deputy" — but he HAS a
    # Deputy account, so the assumption is one clocked shift away from being
    # false. Verified 2026-07-17: id 24 has never logged a shift in any Deputy
    # data we hold, and Xero has paid him in 86 weeks. It holds by luck, not by
    # construction. If he ever clocks on, his Deputy cost lands on a venue AND
    # his salary stays in the residual — counted twice, silently.
    #
    # NOT MAPPED — because Xero has never paid them, under any name:
    #   "pedro f"   (id 261): hours in 14 separate weeks, Xero pay in ZERO.
    #   "Long Long" (id 225): hours in 4 weeks, Xero pay in ZERO.
    # Verified by week-alignment Mar–Jul and by name search across all 122 people
    # in the pay history. This is not a mapping gap — it is a person and a
    # payroll that have never met. Do NOT invent an alias to make the totals
    # tie; that would bury it. Needs Zak.
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
