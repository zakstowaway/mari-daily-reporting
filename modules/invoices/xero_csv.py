"""
Export validated + coded invoices as a Xero BILL IMPORT csv — the no-API-scope
path to the same outcome.

Xero's Business > Bills > Import accepts a CSV with per-line AccountCode, tax and
tracking, and creates the bills as DRAFTS for a human to review and approve. That
means we get Dext-style split coding into Xero without the accounting.transactions
OAuth scope (which the current app can't be granted) and with the human firmly in
the loop — nothing posts until someone opens the draft in Xero and clicks.

Import in Xero as **Tax Inclusive** (amounts here include GST). One row per line
item; rows sharing ContactName + InvoiceNumber become one bill. Pure-GST
reconciliation lines are dropped — Xero recomputes GST from each line's tax rate.

    # from saved pipeline output (data/invoices/*.json):
    python3 modules/invoices/xero_csv.py --in data/invoices --out xero_bills.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.invoices.account_map import suggest_coding  # noqa: E402
from modules.invoices.models import Invoice, InvoiceLine, LineClass, TaxTreatment, Venue  # noqa: E402

# Xero AU tax-rate display names (bill import wants the name, not the code).
TAX_NAME = {
    TaxTreatment.GST: "GST on Expenses",
    TaxTreatment.GST_FREE: "GST Free Expenses",
    TaxTreatment.WET: "GST on Expenses",
}
COLUMNS = ["*ContactName", "*InvoiceNumber", "*InvoiceDate", "*DueDate",
           "Description", "*Quantity", "*UnitAmount", "*AccountCode", "*TaxType",
           "TrackingName1", "TrackingOption1"]


def bill_rows(inv: Invoice, coding=None) -> list[dict]:
    """One CSV row per codeable line. Quantity 1 + inclusive line total keeps the
    bill total exact; the real pack qty is kept in the description for the reader."""
    coding = coding or suggest_coding(inv)
    inv_date = inv.invoice_date.isoformat() if inv.invoice_date else date.today().isoformat()
    rows = []
    for line, lc in zip(inv.lines, coding.lines):
        if lc.account_code is None:
            continue
        amt = Decimal(str(line.line_total_incl))
        if amt == 0:
            continue
        qty = line.qty or Decimal("1")
        desc = line.description or lc.account_name or "item"
        if qty and qty != 1:
            desc = f"{qty} x {desc}"
        rows.append({
            "*ContactName": inv.supplier_name_raw or inv.supplier_key or "Unknown supplier",
            "*InvoiceNumber": inv.invoice_ref or f"{inv.supplier_key}-{inv_date}",
            "*InvoiceDate": inv_date,
            "*DueDate": inv_date,
            "Description": desc[:4000],
            "*Quantity": "1",
            "*UnitAmount": f"{amt:.2f}",
            "*AccountCode": lc.account_code,
            "*TaxType": TAX_NAME.get(line.tax_treatment, "GST on Expenses"),
            "TrackingName1": coding.tracking_category or "",
            "TrackingOption1": coding.tracking_option or "",
        })
    return rows


def write_csv(invoices: Iterable[Invoice], out: Path) -> tuple[int, int]:
    n_bills = n_rows = 0
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for inv in invoices:
            rows = bill_rows(inv)
            if rows:
                n_bills += 1
                n_rows += len(rows)
                w.writerows(rows)
    return n_bills, n_rows


# --- load Invoices back from saved pipeline JSON --------------------------------
def _invoice_from_json(d: dict) -> Invoice:
    iv = d.get("invoice", d)
    lines = [InvoiceLine(
        description=L.get("description", ""), qty=Decimal(str(L.get("qty", "0"))),
        line_total_incl=Decimal(str(L.get("line_total_incl", "0"))),
        unit_price_incl=Decimal(str(L["unit_price_incl"])) if L.get("unit_price_incl") else None,
        pack_size=L.get("pack_size"),
        line_class=LineClass(L.get("line_class", "stock")) if L.get("line_class") else LineClass.STOCK,
        tax_treatment=TaxTreatment(L.get("tax_treatment", "gst")) if L.get("tax_treatment") else TaxTreatment.GST,
        supplier_code=L.get("supplier_code")) for L in iv.get("lines", [])]
    return Invoice(
        supplier_key=iv.get("supplier_key", ""), supplier_name_raw=iv.get("supplier_name_raw", ""),
        invoice_ref=iv.get("invoice_ref", ""),
        invoice_date=date.fromisoformat(iv["invoice_date"]) if iv.get("invoice_date") else None,
        total_incl=Decimal(str(iv.get("total_incl", "0"))), lines=lines,
        venue=Venue(iv["venue"]) if iv.get("venue") else Venue.UNKNOWN,
        po_refs=iv.get("po_refs") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", default="data/invoices",
                    help="folder of validated invoice JSONs")
    ap.add_argument("--out", default="xero_bills.csv")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    indir = (root / args.indir) if not Path(args.indir).is_absolute() else Path(args.indir)
    files = sorted(indir.glob("*.json"))
    invoices = []
    for f in files:
        try:
            invoices.append(_invoice_from_json(json.loads(f.read_text())))
        except Exception as e:
            print(f"  skip {f.name}: {e}", file=sys.stderr)
    out = (root / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    n_bills, n_rows = write_csv(invoices, out)
    print(f"Wrote {n_bills} bills ({n_rows} coded lines) -> {out}")
    print("Import in Xero: Business > Bills to pay > Import, as **Tax Inclusive**. "
          "Bills arrive as DRAFTS to review + approve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
