"""
Self-improving coding — learn from every correction a human makes.

When an admin changes an invoice's coding before approving (a line's account, or
the venue), that change is the ground truth. This reads the approvals from
Supabase, compares each chosen value against what the system had suggested, and
writes the differences to learned_overrides.json:

  * line_account   (supplier + item)  -> the account a human moved it to
                    e.g. Foodlink "SAUCE BOTTLE EMPTY" 115 -> 373 Kitchen Supplies
  * supplier_venue (supplier)         -> a venue a human repeatedly re-picks

account_map applies these at the TOP priority, so the same correction never has
to be made twice. Run after the approval poller (or on a schedule).

    python3 modules/invoices/learn_overrides.py
"""

from __future__ import annotations

import collections
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.invoices.account_map import _norm            # noqa: E402
from modules.invoices.xero_process_approvals import SUPA_URL, _svc_key  # noqa: E402

OUT = Path(__file__).parent / "learned_overrides.json"
TABLE = "invoice_approvals"


def _item_key(line: dict) -> str:
    code = (line.get("supplier_code") or "").strip()
    return code.lower() if code else _norm(line.get("description", ""))


def _fetch(key: str) -> list:
    req = urllib.request.Request(
        f"{SUPA_URL}/rest/v1/{TABLE}?decision=eq.approve&select=*",
        headers={"apikey": key, "authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def learn() -> dict:
    rows = _fetch(_svc_key())
    line_acct = collections.defaultdict(collections.Counter)
    sup_venue = collections.defaultdict(collections.Counter)

    for rec in rows:
        sup = _norm(rec.get("supplier") or rec.get("supplier_key") or "")
        if not sup:
            continue
        # venue correction — chosen differs from what was suggested
        opt, sug_opt = rec.get("tracking_option"), rec.get("suggested_tracking_option")
        cat = rec.get("tracking_category")
        if opt and sug_opt and (cat, opt) != (rec.get("suggested_tracking_category"), sug_opt):
            sup_venue[sup][f"{cat}|{opt}"] += 1
        # per-line account corrections
        for l in rec.get("lines") or []:
            chosen, sugg = l.get("account_code"), l.get("suggested_account")
            if chosen and sugg and chosen != sugg:
                line_acct[(sup, _item_key(l))][chosen] += 1

    overrides = {"line_account": {}, "supplier_venue": {}}
    for (sup, item), c in line_acct.items():
        code, n = c.most_common(1)[0]
        overrides["line_account"][f"{sup}|{item}"] = {"code": code, "n": n}
    for sup, c in sup_venue.items():
        top, n = c.most_common(1)[0]
        if n >= 2:                              # venue varies per bill — need repetition
            cat, opt = top.split("|", 1)
            overrides["supplier_venue"][sup] = {"category": cat, "option": opt, "n": n}
    return overrides


def main() -> int:
    try:
        ov = learn()
    except Exception as e:
        print(f"learn_overrides failed: {e}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(ov, indent=2))
    print(f"learned {len(ov['line_account'])} line corrections, "
          f"{len(ov['supplier_venue'])} venue corrections -> {OUT.name}")
    for k, v in list(ov["line_account"].items())[:12]:
        print(f"  {k} -> {v['code']} (x{v['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
