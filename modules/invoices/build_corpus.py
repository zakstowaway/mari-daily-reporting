#!/usr/bin/env python3
"""
Build a local validation corpus of REAL invoices per supplier.

    python3 modules/invoices/build_corpus.py [--per 40] [--months 4]

The corpus is the test set the deterministic parsers must pass. More real
invoices = every layout variation (wrapped descriptions, odd units, credit
notes, multi-page, $0 substitutions) shows up, so a parser can be iterated
until it handles them — that is what drives error rates down.

Pulls up to `--per` invoices per known supplier domain from the last `--months`
months of the accounts@ inbox into data/invoice_corpus/<supplier_key>/, named by
content hash (natural dedup). Idempotent — re-run to grow the set; already-saved
PDFs are skipped. GITIGNORED: these are real invoices, never committed.

parser_regression.py measures the parsers against this corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices import pull_mailbox as P   # noqa: E402

CORPUS = ROOT / "data" / "invoice_corpus"

# Sender domain -> supplier key (matches the parser registry + build_cogs_list).
DOMAIN_KEY = {
    "selectprovidores.com.au": "select_fresh", "foodlinkaustralia.com.au": "foodlink",
    "befoods.com.au": "be_foods", "tfft.com.au": "fresh_fruit_team",
    "gullifood.com.au": "gulli", "suncircle.com.au": "sun_circle",
    "junpacific.com": "jun_pacific", "ilg.com.au": "ilg",
    "lionco.com": "lion", "paramountliquor.com.au": "paramount",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=40, help="max invoices per supplier")
    ap.add_argument("--months", type=int, default=4, help="how far back to pull")
    args = ap.parse_args()

    token = P.get_token()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.months * 30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    qs = urllib.parse.urlencode({
        "$filter": f"receivedDateTime ge {cutoff} and hasAttachments eq true",
        "$select": "id,subject,from", "$top": "100",
    }, quote_via=urllib.parse.quote)
    url = f"/mailFolders/inbox/messages?{qs}"

    saved = {k: 0 for k in set(DOMAIN_KEY.values())}
    have = {k: len(list((CORPUS / k).glob("*.pdf"))) if (CORPUS / k).exists() else 0
            for k in saved}
    pages = 0
    while url and pages < 60:
        d = P._req(token, "GET", url)
        pages += 1
        for m in d.get("value", []):
            dom = ((m.get("from", {}).get("emailAddress", {}) or {}).get("address", "")).split("@")[-1].lower()
            key = DOMAIN_KEY.get(dom)
            if not key or have[key] + saved[key] >= args.per:
                continue
            if P.SKIP_SUBJECT.search(m.get("subject", "")):
                continue
            for _, data in P.pdf_attachments(token, m["id"]):
                dst = CORPUS / key
                dst.mkdir(parents=True, exist_ok=True)
                fn = dst / f"{hashlib.sha1(data).hexdigest()[:12]}.pdf"
                if not fn.exists():
                    fn.write_bytes(data)
                    saved[key] += 1
                break   # first PDF per email
        # stop paging once every supplier is full
        if all(have[k] + saved[k] >= args.per for k in saved):
            break
        url = d.get("@odata.nextLink")

    print(f"corpus at {CORPUS.relative_to(ROOT)} (pages scanned: {pages})")
    for k in sorted(saved):
        print(f"  {k:<18} +{saved[k]:>3} new  ({have[k] + saved[k]} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
