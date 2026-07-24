"""
Learn how bills have ACTUALLY been coded, straight from Xero history.

Once the wider scope is granted (scripts/xero_reauth.py), this pulls historical
ACCPAY bills and, per supplier (Contact), finds the account each one was coded to
and the tracking option used. It writes the empirical map to learned_coding.json,
which account_map.py prefers over its rule-based guesses when present — so the
suggester converges on exactly how Donna/Dext has been coding, per supplier.

    python3 modules/invoices/learn_coding.py [--months 18]

Read-only. Writes one JSON next to account_map.py.
"""

from __future__ import annotations

import argparse
import collections
import statistics
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import xero_pull as xp  # noqa: E402

HERE = Path(__file__).parent
OUT = HERE / "learned_coding.json"


def learn(months: int = 18) -> dict:
    access, tenant = xp.token()
    since = (date.today() - timedelta(days=30 * months)).isoformat()
    acct_by_sup = collections.defaultdict(collections.Counter)
    track_by_sup = collections.defaultdict(collections.Counter)
    kw_by_acct = collections.defaultdict(collections.Counter)   # account -> line-word freq (future use)
    due_by_sup = collections.defaultdict(list)                  # supplier -> [days bill->due]
    scanned = 0
    for page in range(1, 60):
        res = xp.api_get(access, tenant, "Invoices",
                         {"where": f'Type=="ACCPAY" AND Date>=DateTime({since[:4]},{int(since[5:7])},{int(since[8:10])})',
                          "page": str(page), "order": "Date DESC"})
        bills = res.get("Invoices", [])
        if not bills:
            break
        for iv in bills:
            sup = (iv.get("Contact") or {}).get("Name", "").strip()
            if not sup:
                continue
            for li in iv.get("LineItems", []):
                code = li.get("AccountCode")
                if code:
                    acct_by_sup[sup][code] += 1
                    for w in (li.get("Description") or "").lower().split():
                        if len(w) > 3:
                            kw_by_acct[code][w] += 1
                for t in li.get("Tracking", []):
                    if t.get("Option"):
                        track_by_sup[sup][t["Option"]] += 1
            # payment terms: days between bill date and its due date
            d, due = iv.get("DateString", "")[:10], iv.get("DueDateString", "")[:10]
            if d and due:
                try:
                    dd = (date.fromisoformat(due) - date.fromisoformat(d)).days
                    if 0 <= dd <= 90:
                        due_by_sup[sup].append(dd)
                except ValueError:
                    pass
            scanned += 1

    learned = {"scanned_bills": scanned, "since": since, "suppliers": {}}
    for sup, counter in acct_by_sup.items():
        top_acct, n = counter.most_common(1)[0]
        total = sum(counter.values())
        tracks = track_by_sup[sup].most_common(1)
        gaps = due_by_sup.get(sup, [])
        tv = track_by_sup[sup]
        tv_total = sum(tv.values())
        learned["suppliers"][sup] = {
            "account_code": top_acct,
            "account_confidence": round(n / total, 2),
            "account_distribution": dict(counter),
            # how this supplier's bills are actually tracked (venue/dept), + how
            # consistent that is — so a supplier that ALWAYS codes to one venue
            # (e.g. Gulli -> Marilyna's) is trusted over the billed-to address.
            "tracking_option": tracks[0][0] if tracks else None,
            "tracking_confidence": round(tracks[0][1] / tv_total, 2) if tracks and tv_total else 0,
            "tracking_samples": tv_total,
            # each supplier's real terms = median gap between bill date and due date
            "due_days": int(statistics.median(gaps)) if gaps else None,
            "due_days_samples": len(gaps),
        }
    return learned


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=18)
    args = ap.parse_args()
    try:
        learned = learn(args.months)
    except Exception as e:
        print(f"FAILED (is the wider scope granted? run scripts/xero_reauth.py): {e}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(learned, indent=2))
    print(f"Scanned {learned['scanned_bills']} bills since {learned['since']}.")
    print(f"Learned coding for {len(learned['suppliers'])} suppliers -> {OUT.name}")
    for sup, d in sorted(learned["suppliers"].items(), key=lambda kv: -sum(kv[1]['account_distribution'].values()))[:20]:
        print(f"  {sup[:34]:34} -> acct {d['account_code']} ({int(d['account_confidence']*100)}%)  venue {d['tracking_option']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
