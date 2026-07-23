#!/usr/bin/env python3
"""
Invoice run — the entry point.

    email → Outlook rule → Pipedream → repository_dispatch → THIS → data/

Mirrors the daily_pull.yml / Pipedream pattern already proven in this repo
(see PIPEDREAM_BRIDGE.md): Pipedream extracts the PDF attachment as base64 and
POSTs a repository_dispatch; the workflow decodes it and calls this.

Usage
-----
    # from a Pipedream dispatch (what the workflow does)
    python3 scripts/invoice_run.py --pdf-base64-file payload.b64 --source "ILG inv.pdf"

    # from a local file, e.g. re-running one by hand
    python3 scripts/invoice_run.py --pdf /path/to/invoice.pdf

    # parse a saved extraction without calling the API (cheap; for debugging)
    python3 scripts/invoice_run.py --json extraction.json

Exit codes
----------
    0  PASS    — written to data/invoices/
    2  REVIEW  — written to data/invoices_review/, findings printed
    1  ERROR   — could not extract at all

REVIEW IS NOT FAILURE. An invoice that lands in review cost five minutes.
An invoice that silently passes with a wrong number costs a wrong margin on a
dish for a month (skill Rule 8: Average Cost Price is computed from receive
transactions, so a bad number persists ~30 days regardless of later fixes).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root

from modules.invoices.extract import ExtractionError, extract, parse          # noqa: E402
from modules.invoices.validator import Status, Validator                       # noqa: E402

ROOT = Path(__file__).parent.parent
CONFIG = Path(__file__).parent / "suppliers.yaml"
OUT_PASS = ROOT / "data" / "invoices"
OUT_REVIEW = ROOT / "data" / "invoices_review"


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)          # money is Decimal; serialise as string, never float
    if isinstance(o, Enum):
        return o.value
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"unserialisable: {type(o)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract, validate and file one supplier invoice.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", type=Path, help="local PDF")
    src.add_argument("--pdf-base64-file", type=Path, help="file containing base64 PDF (Pipedream payload)")
    src.add_argument("--json", type=Path, help="pre-extracted JSON (skips the API call)")
    ap.add_argument("--source", default="", help="original filename / email subject, for provenance")
    ap.add_argument("--dry-run", action="store_true", help="validate but write nothing")
    args = ap.parse_args()

    # ---- extract -----------------------------------------------------------
    try:
        if args.json:
            inv = parse(args.json.read_text(), source=args.source or str(args.json))
        else:
            if args.pdf_base64_file:
                pdf = base64.b64decode(args.pdf_base64_file.read_text())
                name = args.source or args.pdf_base64_file.stem
            else:
                pdf = args.pdf.read_bytes()
                name = args.source or args.pdf.name
            inv = extract(pdf, filename=name)
    except ExtractionError as e:
        print(f"EXTRACTION FAILED: {e}", file=sys.stderr)
        return 1

    # ---- validate — the gate. No model involved. ---------------------------
    result = Validator(yaml.safe_load(CONFIG.read_text())).validate(inv)

    print(f"{inv.supplier_name_raw} · {inv.invoice_ref} · {inv.invoice_date} · ${inv.total_incl}")
    print(result.report())
    if result.extras_total:
        print(f"  note: LS receive should be ${result.expected_ls_receive_total} "
              f"(${result.extras_total} of extras excluded — that gap is expected)")

    if args.dry_run:
        return 0 if result.ok else 2

    # ---- file it -----------------------------------------------------------
    out_dir = OUT_PASS if result.ok else OUT_REVIEW
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{inv.invoice_date}_{inv.supplier_key or 'unknown'}_{inv.invoice_ref or 'noref'}".replace("/", "-")
    payload = {
        "invoice": asdict(inv),
        "validation": {
            "status": result.status.value,
            "findings": [
                {"code": f.code, "severity": f.severity.value, "message": f.message,
                 "line_index": f.line_index,
                 "expected": f.expected, "actual": f.actual}
                for f in result.findings
            ],
            "expected_ls_receive_total": result.expected_ls_receive_total,
            "extras_total": result.extras_total,
        },
    }
    path = out_dir / f"{stem}.json"
    path.write_text(json.dumps(payload, indent=2, default=_json_default))
    print(f"-> {path.relative_to(ROOT)}")

    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
