"""
Turn human-approved invoices into Xero DRAFT bills. Runs on the Mac (where the
Xero token lives) — the browser only ever writes an approval row to Supabase.

Reads the invoice_approvals table (status = pending) straight from Supabase via
its REST API, using the service key that lives ONLY on this machine. For each:
  * decision "reject"  -> mark rejected, do nothing in Xero.
  * decision "approve" -> build the DRAFT from the APPROVED coding (exactly what
    the admin saw/edited), reconcile it to the invoice total (±$0.50), and — only
    if it balances — create it in Xero as a DRAFT. Idempotent by bill number.
Each row is patched with the outcome (drafted / rejected / needs_review) and the
Xero invoice id, so the app shows status and re-runs never double-post.

No Pipedream. Supabase is the queue; the Mac is the worker.

    python3 modules/invoices/xero_process_approvals.py [--dry-run]

Setup: put the Supabase service_role key in ~/Documents/STOW/.secrets/supabase_service_key
(one line). It bypasses row-level security so this reader sees every pending row.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import xero_pull as xp  # noqa: E402
from modules.invoices import xero_push  # noqa: E402
from modules.invoices.account_map import ACCOUNT_NAME  # noqa: E402

SUPA_URL = "https://fyqhvyvwbedoowjkrxyj.supabase.co"
KEY_FILE = Path.home() / "Documents" / "STOW" / ".secrets" / "supabase_service_key"
TABLE = "invoice_approvals"
TAX = {"gst": "INPUT", "gst_free": "EXEMPTEXPENSES", "wet": "INPUT"}
TOL = Decimal("0.50")


def _svc_key() -> str:
    if not KEY_FILE.exists():
        raise SystemExit(f"Supabase service key not found at {KEY_FILE} — see the module docstring.")
    return KEY_FILE.read_text().strip()


def _sb(method: str, query: str, key: str, payload=None):
    req = urllib.request.Request(
        f"{SUPA_URL}/rest/v1/{TABLE}{query}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"apikey": key, "authorization": f"Bearer {key}",
                 "content-type": "application/json", "prefer": "return=representation"})
    with urllib.request.urlopen(req) as r:
        body = r.read()
        return json.loads(body) if body else []


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
    if rec.get("invoice_date"):
        payload["Date"] = rec["invoice_date"]
    return payload, total


def process(dry_run: bool = False) -> list[dict]:
    key = _svc_key()
    pending = _sb("GET", "?status=eq.pending&select=*", key)
    if not pending:
        return []
    access = tenant = None
    if not dry_run:
        access, tenant = xp.token()
    results = []
    for rec in pending:
        out = {"ref": rec.get("ref"), "supplier": rec.get("supplier")}
        patch = {}

        if rec.get("decision") == "reject":
            patch = {"status": "rejected"}
        else:
            payload, built = _payload(rec)
            stated = Decimal(str(rec.get("total") or built))
            if not payload["LineItems"]:
                patch = {"status": "needs_review", "note": "no codeable lines"}
            elif abs(built - stated) > TOL:
                patch = {"status": "needs_review", "note": f"built ${built} != total ${stated}"}
            elif dry_run:
                out["note"] = f"ready (dry-run) ${built}"
            elif xero_push.already_exists(access, tenant, xp.api_get,
                                          payload["Contact"]["Name"], rec.get("ref", "")):
                patch = {"status": "drafted", "note": "already in Xero"}
            else:
                resp = xero_push.api_post(access, tenant, "Invoices", {"Invoices": [payload]})
                created = (resp.get("Invoices") or [{}])[0]
                patch = {"status": "drafted", "xero_invoice_id": created.get("InvoiceID")}
                out["xero_invoice_id"] = created.get("InvoiceID")

        if patch and not dry_run:
            patch["processed_at"] = datetime.now().isoformat(timespec="seconds")
            _sb("PATCH", f"?ref=eq.{urllib.parse.quote(rec['ref'])}", key, patch)
        out["status"] = patch.get("status", rec.get("status", "pending"))
        results.append(out)
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
        print(f"  {r['status']:12} {r.get('supplier', '')[:24]:24} {r.get('ref', '')}  {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
