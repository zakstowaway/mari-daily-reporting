#!/usr/bin/env python3
"""
Score the deterministic parsers against the local corpus.

    python3 modules/invoices/parser_regression.py [supplier_key ...]

For each supplier with invoices in data/invoice_corpus/ (built by
build_corpus.py), parse every PDF and validate it. Reports, per supplier:

    PASS         parsed AND reconciled to the printed total  -> free & correct
    review       parsed but didn't reconcile                 -> falls to the LLM
    parse-fail   no parser / scan / parser errored            -> falls to the LLM

The PASS rate is the number to drive up. Nothing here is unsafe — a non-PASS
invoice simply falls back to the LLM in production; this just measures how much
the free path is covering. The daily triage task uses this to target the worst
suppliers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.invoices.build_corpus import DOMAIN_KEY          # noqa: E402
from modules.invoices.parsers import DOMAIN_TO_PARSER, parse_pdf  # noqa: E402
from modules.invoices.validator import Validator              # noqa: E402

CORPUS = ROOT / "data" / "invoice_corpus"
KEY_DOMAIN = {v: k for k, v in DOMAIN_KEY.items()}


def main() -> int:
    only = set(sys.argv[1:])
    cfg = yaml.safe_load((ROOT / "modules/invoices/suppliers.yaml").read_text())
    V = Validator(cfg)

    keys = sorted(d.name for d in CORPUS.iterdir()) if CORPUS.exists() else []
    if not keys:
        print(f"no corpus at {CORPUS.relative_to(ROOT)} — run build_corpus.py first")
        return 1

    tot_p = tot_n = 0
    print(f"{'supplier':<18} {'pass':>10}   review  parsefail   parser")
    for key in keys:
        if only and key not in only:
            continue
        dom = KEY_DOMAIN.get(key, "")
        pdfs = sorted((CORPUS / key).glob("*.pdf"))
        p = r = f = 0
        for pf in pdfs:
            try:
                inv = parse_pdf(pf.read_bytes(), dom)
            except Exception:
                inv = None
            if inv is None:
                f += 1
            elif V.validate(inv).ok:
                p += 1
            else:
                r += 1
        n = len(pdfs)
        tot_p += p
        tot_n += n
        pct = f"{p}/{n} ({100 * p // n if n else 0}%)"
        has = "yes" if dom in DOMAIN_TO_PARSER else "—"
        print(f"{key:<18} {pct:>10}   {r:>6}   {f:>8}   {has}")
    print(f"{'TOTAL':<18} {f'{tot_p}/{tot_n} ({100 * tot_p // tot_n if tot_n else 0}%)':>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
