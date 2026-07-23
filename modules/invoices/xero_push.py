"""
Push a validated invoice into Xero as a DRAFT bill (ACCPAY) — the write half of
the Dext replacement.

Safety, by construction:
  * DRAFT only. Never Authorised/Approved — a human opens the draft and clicks.
  * Reconcile-gated. We rebuild the bill from the coded lines and only post if it
    totals the invoice's printed total (±$0.50). A bill that doesn't add up is
    never sent; it's left for review — same philosophy as the extractor's gate.
  * Idempotent. If a bill with this supplier + invoice number already exists we
    skip it, so re-runs don't duplicate.
  * dry_run=True by default: build and check, print, but do NOT call Xero.

Requires the wider OAuth scope (accounting.transactions + accounting.contacts) —
run scripts/xero_reauth.py once first.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from modules.invoices.account_map import suggest_coding  # noqa: E402
from modules.invoices.models import Invoice, TaxTreatment  # noqa: E402

# AU tax types for a purchase (bill).
TAX_TYPE = {
    TaxTreatment.GST: "INPUT",          # GST on Expenses (10%)
    TaxTreatment.GST_FREE: "EXEMPTINPUT",  # GST Free Expenses
    TaxTreatment.WET: "INPUT",          # WET is embedded; treat the line as GST on expenses
}
RECONCILE_TOL = Decimal("0.50")


def _d(x) -> Decimal:
    return Decimal(str(x))


def api_post(access, tenant, path, body) -> dict:
    req = urllib.request.Request(
        f"https://api.xero.com/api.xro/2.0/{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {access}", "Xero-tenant-id": tenant,
                 "Accept": "application/json", "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def already_exists(access, tenant, api_get, contact_name: str, invoice_number: str) -> bool:
    if not invoice_number:
        return False
    where = f'Type=="ACCPAY" AND InvoiceNumber=="{invoice_number}"'
    try:
        res = api_get(access, tenant, "Invoices", {"where": where})
    except Exception:
        return False
    for iv in res.get("Invoices", []):
        if (iv.get("Contact", {}).get("Name", "").strip().lower() == contact_name.strip().lower()):
            return True
    return False


def build_bill(inv: Invoice, coding=None) -> tuple[dict, Decimal, list[str]]:
    """
    Build the Xero ACCPAY draft payload from the invoice + its coding.
    Returns (payload, rebuilt_total_incl, warnings). Pure — no network.
    """
    coding = coding or suggest_coding(inv)
    warnings: list[str] = []
    line_items = []
    rebuilt = Decimal("0")
    for line, lc in zip(inv.lines, coding.lines):
        if lc.account_code is None:            # pure-GST reconciliation line — tax, not a GL line
            continue
        amt = _d(line.line_total_incl)
        if amt == 0:
            continue
        li = {
            "Description": (line.description or lc.account_name or "item")[:400],
            "Quantity": float(line.qty or 1),
            "UnitAmount": float(_d(line.unit_price_incl) if line.unit_price_incl else amt),
            "AccountCode": lc.account_code,
            "TaxType": TAX_TYPE.get(line.tax_treatment, "INPUT"),
            "LineAmount": float(amt),
        }
        if coding.tracking_category and coding.tracking_option:
            li["Tracking"] = [{"Name": coding.tracking_category, "Option": coding.tracking_option}]
        line_items.append(li)
        rebuilt += amt

    payload = {
        "Type": "ACCPAY",
        "Contact": {"Name": inv.supplier_name_raw or inv.supplier_key or "Unknown supplier"},
        "LineAmountTypes": "Inclusive",
        "Status": "DRAFT",
        "LineItems": line_items,
    }
    if inv.invoice_ref:
        payload["InvoiceNumber"] = inv.invoice_ref
    if inv.invoice_date:
        payload["Date"] = inv.invoice_date.isoformat()
    if inv.po_refs:
        payload["Reference"] = ", ".join(inv.po_refs)
    if not coding.tracking_option:
        warnings.append("no venue tracking option resolved")
    return payload, rebuilt, warnings


def push_bill(inv: Invoice, access=None, tenant=None, *, api_get=None, dry_run=True) -> dict:
    """
    Build → reconcile-gate → (optionally) POST a DRAFT bill. Returns a status dict;
    never raises for a business reason (only for genuine transport errors).
    """
    coding = suggest_coding(inv)
    payload, rebuilt, warnings = build_bill(inv, coding)
    diff = abs(rebuilt - _d(inv.total_incl))
    status = {
        "supplier": inv.supplier_name_raw, "invoice_ref": inv.invoice_ref,
        "venue": coding.tracking_option, "rebuilt_total": str(rebuilt),
        "invoice_total": str(inv.total_incl), "diff": str(diff),
        "line_count": len(payload["LineItems"]), "warnings": warnings,
    }
    if not payload["LineItems"]:
        status["action"] = "skipped"; status["reason"] = "no codeable lines"
        return status
    if diff > RECONCILE_TOL:
        status["action"] = "needs_review"
        status["reason"] = f"rebuilt bill (${rebuilt}) != invoice total (${inv.total_incl}); not pushing"
        return status
    if dry_run or access is None:
        status["action"] = "ready (dry-run)"
        status["payload"] = payload
        return status
    if api_get and already_exists(access, tenant, api_get, payload["Contact"]["Name"], inv.invoice_ref):
        status["action"] = "skipped"; status["reason"] = "draft/bill already in Xero"
        return status
    resp = api_post(access, tenant, "Invoices", {"Invoices": [payload]})
    created = (resp.get("Invoices") or [{}])[0]
    status["action"] = "created_draft"
    status["xero_invoice_id"] = created.get("InvoiceID")
    return status
