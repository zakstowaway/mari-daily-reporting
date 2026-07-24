"""
Build the invoice review queue the app renders.

Reads every validated/needs-review invoice the pipeline has produced
(data/invoices/*.json = reconciled, data/invoices_review/*.json = flagged),
attaches the suggested Xero coding, and writes one flat file the static app
fetches: dashboard/invoices/queue.json. Also snapshots the chart of accounts +
tracking options to dashboard/invoices/accounts.json so the page's dropdowns are
always in sync with Xero.

An invoice drops off the queue once data/invoice_approvals/<ref>.json exists
(approved or rejected) — so the app only ever shows what still needs a human.

    python3 modules/invoices/build_invoice_queue.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.invoices.account_map import suggest_coding  # noqa: E402
from modules.invoices.xero_csv import _invoice_from_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC_DIRS = [ROOT / "data" / "invoices", ROOT / "data" / "invoices_review"]
APPROVALS = ROOT / "data" / "invoice_approvals"
OUT_DIR = ROOT / "dashboard" / "invoices"
COA = ROOT / "modules" / "invoices" / "xero_accounts.json"


def _decided_refs() -> set[str]:
    if not APPROVALS.exists():
        return set()
    return {p.stem for p in APPROVALS.glob("*.json")}


def _entry(payload: dict) -> dict:
    inv = _invoice_from_json(payload)
    coding = suggest_coding(inv)
    status = (payload.get("validation") or {}).get("status", "pass")
    lines = []
    for line, lc in zip(inv.lines, coding.lines):
        if lc.account_code is None:      # pure-GST reconciliation line — tax, not a coded row
            continue
        lines.append({
            "description": line.description,
            "qty": str(line.qty),
            "amount": f"{Decimal(str(line.line_total_incl)):.2f}",
            "tax": line.tax_treatment.value if hasattr(line.tax_treatment, "value") else str(line.tax_treatment),
            "account_code": lc.account_code,
            "account_name": lc.account_name,
            "reason": lc.reason,
        })
    return {
        "ref": inv.invoice_ref or f"{inv.supplier_key}-{inv.invoice_date}",
        "supplier": inv.supplier_name_raw or inv.supplier_key,
        "supplier_key": inv.supplier_key,
        "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "total": f"{Decimal(str(inv.total_incl)):.2f}",
        "venue": inv.venue.value if hasattr(inv.venue, "value") else str(inv.venue),
        "tracking_category": coding.tracking_category,
        "tracking_option": coding.tracking_option,
        "tracking_confidence": coding.tracking_confidence,
        "status": status,               # pass | review
        "lines": lines,
    }


def build() -> dict:
    decided = _decided_refs()
    seen, entries = set(), []
    for d in SRC_DIRS:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                payload = json.loads(f.read_text())
            except Exception:
                continue
            try:
                e = _entry(payload)
            except Exception as ex:
                print(f"  skip {f.name}: {ex}", file=sys.stderr)
                continue
            if e["ref"] in decided or e["ref"] in seen:
                continue
            seen.add(e["ref"])
            entries.append(e)
    entries.sort(key=lambda e: (e["status"] != "review", e["date"] or ""), reverse=True)
    return {"generated": date.today().isoformat(), "count": len(entries), "invoices": entries}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    queue = build()
    (OUT_DIR / "queue.json").write_text(json.dumps(queue, indent=2))
    if COA.exists():
        coa = json.loads(COA.read_text())
        # Only the codeable expense/COGS accounts the dropdown needs.
        (OUT_DIR / "accounts.json").write_text(json.dumps({
            "accounts": [{"code": a["code"], "name": a["name"]} for a in coa["accounts"]],
            "tracking": coa["tracking"],
        }, indent=2))
    print(f"queue.json: {queue['count']} invoice(s) awaiting review -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
