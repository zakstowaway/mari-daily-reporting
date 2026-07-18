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
    "Sam Hall": "Samuel Hall",              # 8 wks, 8 both, 0 D-only
    # 2026-07-18 — found by scripts/suggest_employee_aliases.py, which matches on
    # WEEK ALIGNMENT rather than name. 66 people were working with no Xero
    # payslip ($333,746 of Deputy-costed labour); their cost was landing on a
    # venue from Deputy AND in the corp-payroll residual from Xero. Counted
    # twice, silently.
    #
    # Each of these has BOTH: the first name matches exactly, AND the weeks they
    # logged hours are the weeks Xero paid them. `d_only` = weeks worked but not
    # paid, which for a true match must be ~0 — you cannot work a week and not be
    # paid for it. Neither test alone would be enough; together they are.
    #
    #   deputy id  name        xero                     overlap   d_only  jaccard
    "Ishwor": "Ishwor Purja",              # 239        40/41         1     0.98
    "Victor": "Victor Flores",             # 259        11/11         0     1.00
    "Rob Bolt": "Robert Bolt",             # 279        10/10         0     0.91
    "Jordan": "Jordan Taylor",             # 224        18/19         1     0.95
    "Cesar": "Cesar Machuca",              # 278         8/9          1     0.89
    "Maeva": "Maeva Boutin-Kelly",         # 219        36/38         2     0.95
    "Julio": "Julio Cesar Mendes",         # 217        27/29         2     0.93
    "Ailu": "Ailen Gaitan",                # 218        22/24         2     0.92
    "Mais": "Maisie Hughes",               # 235        16/18         2     0.89
    "Camila Green": "Camila Gomez Green",  # 189        60/64         4     0.94
    "Audi": "Audi Audi",                   # 250        24/28         4     0.86
    "Suraj": "Suraj Khadgi",               # 157        29/34         5     0.85
    "Milan": "Milan Khanal",               # 270        12/15         3     0.80
    "Arata": "Arata Kitamoto",             # 193         9/13         4     0.69
    "Dom": "Dom Lees-Bell",                # 179         6/10         4     0.60
    # Zak, 2026-07-18: "billy is buillermo" [Guillermo]. The matcher ranked this
    # first (jac 0.78) but flagged it ambiguous, because id 213 "Wilson Cortes"
    # also fitted Guillermo at 0.74 and both could not be right. Zak's call
    # resolves it — and it means 213 is someone ELSE, not that 213 has no match.
    "Billy": "Guillermo De las Carreras",  # 200        21/27         6     0.78
    # Zak, 2026-07-18: "teramet is long long."
    #
    # Long Long (id 225) sat in _xero_exempt for a DAY AND A HALF on my say-so:
    # "hours in 4 weeks, Xero pay in ZERO. Verified by week-alignment Mar-Jul and
    # by name search across all 122 people in the pay history." The name search
    # was worthless — his Xero name is Teramet Tongsong, so searching "Long Long"
    # was never going to find him. "Not in Xero under any name" only ever meant
    # "not under any name I recognised".
    #
    # And the week-alignment matcher, which WOULD have found this (39 weeks,
    # $26,293.62), skips anyone on the exempt list. The exemption suppressed the
    # one tool that could have questioned it. That is now fixed in
    # suggest_employee_aliases.py — exempt ids are checked, and loudly.
    #
    # The cost of the mistake: his Deputy cost was on a venue AND his $26,293.62
    # of Xero pay was in the corp-payroll residual. Counted twice, for 39 weeks,
    # and the group total tied the whole time.
    "Long Long": "Teramet Tongsong",       # 225        39 wks, $26,293.62
    # 2026-07-18 — the MIRROR of the alias problem, found by
    # scripts/match_xero_to_deputy.py. These people are PAID BY XERO but no
    # Deputy id claimed them, so their cost never reached a venue: it fell into
    # the corp-payroll residual, which is where OWNER salary lives. Every venue
    # was understated by their pay and corp payroll overstated by it.
    #
    # They were invisible because Deputy's People screen shows 41 active of 280
    # accounts — all 239 ARCHIVED ones, every departed chef, are hidden in the UI.
    # Found by matching Xero -> Deputy on week alignment. x_only = weeks Xero PAID
    # them that the Deputy account did NOT work; 0 on every one of these, and
    # payroll does not pay people for weeks they never worked.
    #
    # Zak confirmed all four, 2026-07-18: "that matching is correct".
    #
    #   deputy id  archived   worked / paid weeks   x_only   jaccard
    "Angie": "Angela Rinaudo",             # 233        5/5        0     1.00
    "Faith": "Fatima Mitra",               # 290        5/5        0     1.00
    "Agustin": "Agustin Neme",             # 281        3/3        0     0.60
    # Zak: "nattachat is oak". Deputy 268 "Paola" ALSO scored a perfect 1.00 here
    # — she worked the identical weeks — so the maths could not separate them.
    # The shared SURNAME did. Worth remembering: a perfect week alignment is not
    # proof of identity when two people work the same roster.
    "Oak Thongsrinoon": "Nattachat Thongsrinoon",   # 271  4/4     0     1.00
    # Same run, same evidence, not explicitly named by Zak but the names speak:
    "Saif": "Saif Quader",                 # 231        6/6        0     0.67
    "Coco": "Corentin Golbry",             # 155       12/12       0     1.00
    #
    # Confirming Billy freed Guillermo and let the matcher settle five more —
    # each a PERFECT week alignment (every week worked is a week paid, d_only 0)
    # with the name agreeing too:
    "Alison": "Alison Almeida",            # 280         5/5          0     1.00
    "Sofia": "Sofia Maria Sastre",         # 220         4/4          0     1.00
    "Bishal": "Bishal Dangi Chhetri",      # 229         6/6          0     1.00
    "Pauli": "Paula Andrea Romero",        # 234         7/7          0     1.00
    "Avee": "Abishek Bhattarai",           # 291         8/8          0     1.00
    #     ^ weeks are a flawless 8/8, but "Avee" -> "Abishek" is the one name
    #       here I am inferring rather than reading. Zak: worth a glance.
    #
    # ⚠️ STILL NOT MAPPED — the matcher has no honest answer for these:
    #   id 213 "Wilson Cortes" -> Teramet Tongsong (jac 0.51, d_only 4). It only
    #     ever fitted Guillermo because Guillermo was unclaimed; with him taken
    #     the best remaining candidate shares nothing with the name. Needs Zak.
    #   id 221 "Patrick"       -> Corentin Golbry (jac 0.21, d_only 22). 22 weeks
    #     worked and not paid is not one person, it is two.
    #   id 252 "Sanjida"       -> Corentin Golbry (jac 0.22, d_only 11). Same.
    #   id 208 "Harry R"       -> Sofia Maria Sastre (jac 0.50) — and Sofia is
    #     already 220's, at a perfect 4/4. Not this.
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
    # NOT MAPPED, DELIBERATELY — Xero has never paid them, under any name:
    #   "pedro f"   (id 261): hours in 14 separate weeks, Xero pay in ZERO.
    #
    # ⚠️ "Long Long" (id 225) USED TO BE ON THIS LIST AND IT WAS WRONG. He is
    # Xero's Teramet Tongsong — 39 weeks, $26,293.62 — mapped above on Zak's word
    # (2026-07-18). I had "verified" him by searching the name "Long Long" across
    # the pay history, which could never have worked.
    #
    # So treat the claim above about pedro with the same suspicion: it rests on
    # the same name search. It is CORROBORATED by week-alignment now that
    # suggest_employee_aliases.py checks exempt ids (it proposes no candidate for
    # 261), but week-alignment cannot prove a negative either — it can only say
    # nobody in Xero looks like him. Zak's knowledge outranks both.
    #
    # Zak, 2026-07-17: "assume pedro at his deputy rates. same with long long."
    # So the existing fallback IS the answer: unmapped -> rebuild_wages costs them
    # from Deputy's own Cost. That is not a bug and needs no code; it is why the
    # fallback exists. An ALIAS would be actively wrong — it would point them at
    # someone else's payslip and make the totals tie by lying.
    #
    # The audit will keep reporting them as "no xero -> deputy/model" every week.
    # That line is CORRECT. Do not silence it: the day Xero does start paying
    # them, the line disappearing is how you find out.
}

xero = json.loads((ROOT / "data" / "xero_pay_weekly.json").read_text())
xnames = set(xero)
# Case-insensitive index. NOT a fuzzy match — the letters must be identical;
# only capitalisation may differ.
#
# Deputy "George Sampson" and Xero "George sampson" are the same man, 13 weeks,
# $1,371.90, and the exact-match test never saw it because of one capital S. He
# then sat in the unmapped pile for months while his pay went to the
# corp-payroll residual instead of his venue.
#
# This is safe in a way ALIASES are not: no judgement, no similarity, no
# threshold. If two names differ by more than case they still do not match here.
_lower = {}
for _n in xnames:
    _lower.setdefault(_n.lower(), []).append(_n)
# A collision would mean Xero holds two people whose names differ only by case —
# then this is guesswork and must not run. Never seen; assert rather than assume.
_ambiguous_case = {k: v for k, v in _lower.items() if len(v) > 1}
if _ambiguous_case:
    sys.exit(f"Xero has names differing only by case: {_ambiguous_case}. "
             f"Case-insensitive matching is unsafe here — resolve by hand.")

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
    elif nm.lower() in _lower:
        # Same name, different capitalisation (George Sampson / George sampson).
        mapping[eid] = _lower[nm.lower()][0]
    else:
        unmatched.append((eid, nm))

out = ROOT / "data" / "employee_map.json"
out.write_text(json.dumps(dict(sorted(mapping.items(), key=lambda kv: int(kv[0]))), indent=1))
print(f"Deputy employees: {len(emps)} | mapped to Xero: {len(mapping)} -> {out}")
print(f"\nUNMATCHED ({len(unmatched)}) — no Xero payslip found; rebuild_wages will")
print("fall back to its estimate for these. Add to ALIASES if any are real:")
for eid, nm in sorted(unmatched, key=lambda t: t[1].lower()):
    print(f"  {eid:>5}  {nm}")
