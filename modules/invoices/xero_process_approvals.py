"""
Turn human-approved invoices into Xero DRAFT bills. Runs on the Mac (where the
Xero token lives) — the browser only ever records a decision.

Reads data/invoice_approvals/*.json (written by the app via the worker). For each
still-"pending" record:
  * decision "reject"  -> mark rejected, do nothing in Xero.
  * decision "approve" -> build the DRAFT bill from the APPROVED coding (exactly
    what the admin saw/edited), reconcile it to the invoice total (±$0.50), and —
    only if it balances — create it in Xero as a DRAFT. Idempotent by bill number.
The record is updated in place with the outcome (drafted / rejected /
needs_review) and the Xero invoice id, so the app can show status and re-runs
never double-post.

    python3 modules/invoices/xero_process_approvals.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import xero_pull as xp  # noqa: E402
from modules.invoices import xero_push  # noqa: E402
from modules.invoices.account_map import ACCOUNT_NAME  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
APPROVALS = ROOT / "data" / "invoice_approvals"
TAX = {"gst": "INPUT", "gst_free": "EXEMPTEXPENSES", "wet": "INPUT"}
TOL = Decimal("0.50")


def _payload(rec: dict) -> tuple[dict, Decimal]:
    lines, total = [], Decimal("0")
    for l in rec.get("lines", []):
        amt = Decimal(str(l.get("amount", "0")))
        if amt == 0 or not l.get("account_code"):
            continue
        li = {
            "Description": (l.get("description") or ACCOUNT_NAME.get(l["account_code"], "item"))[:400],
            "Quantity": 1, "UnitAmount": float(amt), "LineAmount": float(amt),
            "AccountCode": l["account_code"], "TaxType": TAX.get(l.get("tax"), "INPUT"),
        }
        if rec.get("tracking_category") and rec.get("tracking_option"):
            li["Tracking"] = [{"Name": rec["tracking_category"], "Option": rec["tracking_option"]}]
        lines.append(li)
        total += amt
    payload = {
        "Type": "ACCPAY", "Status": "DRAFT", "LineAmountTypes": "Inclusive",
        "Contact": {"Name": rec.get("supplier") or rec.get("supplier_key") or "Unknown supplier"},
        "LineItems": lines,
    }
    if rec.get("ref"):
        payload["InvoiceNumber"] = rec["ref"]
    if rec.get("date"):
        payload["Date"] = rec["date"]
    return payload, total


def process(dry_run: bool = False) -> list[dict]:
    if not APPROVALS.exists():
        return []
    access = tenant = None
    if not dry_run:
        access, tenant = xp.token()
    results = []
    for f in sorted(APPROVALS.glob("*.json")):
        rec = json.loads(f.read_text())
        if rec.get("status") not in (None, "pending"):
            continue                              # already handled
        outcome = {"ref": rec.get("ref"), "supplier": rec.get("supplier")}

        if rec.get("decision") == "reject":
            rec["status"] = "rejected"
        else:
            payload, built = _payload(rec)
            stated = Decimal(str(rec.get("total") or built))
            if not payload["LineItems"]:
                rec["status"], outcome["note"] = "needs_review", "no codeable lines"
            elif abs(built - stated) > TOL:
                rec["status"] = "needs_review"
                outcome["note"] = f"built ${built} != total ${stated}"
            elif dry_run:
                rec["status"] = "pending"          # unchanged
                outcome["note"] = f"ready (dry-run) ${built}"
            elif xero_push.already_exists(access, tenant, xp.api_get,
                                          payload["Contact"]["Name"], rec.get("ref", "")):
                rec["status"], outcome["note"] = "drafted", "already in Xero"
            else:
                resp = xero_push.api_post(access, tenant, "Invoices", {"Invoices": [payload]})
                created = (resp.get("Invoices") or [{}])[0]
                rec["status"] = "drafted"
                rec["xero_invoice_id"] = created.get("InvoiceID")
                outcome["xero_invoice_id"] = created.get("InvoiceID")

        outcome["status"] = rec["status"]
        if not dry_run:
            rec["processed_at"] = datetime.now().isoformat(timespec="seconds")
            f.write_text(json.dumps(rec, indent=2))
        results.append(outcome)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = process(args.dry_run)
    done = sum(r["status"] == "drafted" for r in res)
    print(f"{date.today()}: processed {len(res)} approval(s) — {done} drafted in Xero")
    for r in res:
        extra = r.get("xero_invoice_id") or r.get("note") or ""
        print(f"  {r['status']:12} {r.get('supplier','')[:24]:24} {r.get('ref','')}  {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
