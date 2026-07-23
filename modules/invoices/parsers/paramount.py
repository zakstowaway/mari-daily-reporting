"""
Paramount Liquor — deterministic parser (coordinate-based).

Standard single-invoice template (system-generated, real text layer). Columns:

    Code | Description | Size | Case/Bottle | Base Cost | Total Net | WET | GST
         | LUC Ex GST | Total Inc GST

Every line carries a per-line "Total Inc GST" figure in the rightmost column,
and those sum exactly to the stated "Invoice Total" — WET and GST are already
folded into each line, so we reconcile on that column directly and never have to
untangle the invoice-level WET/GST split (which the flattened text layer renders
in an ambiguous order). Read by word x-position (pdf_text.word_rows/bucket) so a
wrapped product name or the "0 / 1" bottle-break qty can not shift a figure.

A row is a line item iff its Code cell is a bare product/charge code (all digits)
AND it has a value in the Total-Inc-GST column — that cleanly excludes the
totals/payment footer. MISC charges (Carton Freight, Fuel Levy, Minimum Delivery
Top-Up) are captured as EXTRA lines.

Consolidated statements and the occasional 2-page multi-invoice PDF do not carry
this single-invoice header; they raise and fall back to the LLM.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

# Column x-starts from the header row (Code 33, Description 155, Size 303,
# Case/Bottle 357, Base Cost 422, Total Net 489, WET 556, GST 611, LUC Ex GST
# 651, Total Inc GST 711). Boundaries sit just left of each header/value.
COLS = [("code", 0), ("desc", 75), ("size", 290), ("qty", 356), ("base", 415),
        ("net", 485), ("wet", 548), ("gst", 600), ("luc", 645), ("incgst", 705)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")
# MISC / charge lines that are never entered on a Lightspeed receive.
EXTRA_RE = re.compile(r"freight|fuel levy|delivery|top-?up|surcharge|cartage", re.I)


def _m(s):
    s = (s or "").replace(",", "").strip()
    m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


@register("paramountliquor.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Code" in toks and "Description" in toks and "Net" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Paramount: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        code = c["code"].strip()
        inc = _m(c["incgst"])
        if not re.fullmatch(r"\d{3,}", code):     # real product/charge code only
            continue
        if inc is None or inc == 0:               # zero / substituted line
            continue
        desc = (c["desc"] or code).strip()
        is_extra = c["size"].strip().upper() == "MISC" or bool(EXTRA_RE.search(desc))
        wet = _m(c["wet"])
        items.append(InvoiceLine(
            description=desc, qty=Decimal("1"), line_total_incl=inc,
            unit_price_incl=None, pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=(TaxTreatment.WET if (wet and wet > 0) else TaxTreatment.GST),
            cost_basis=CostBasis.UNKNOWN, supplier_code=code,
            raw_uom=c["size"] or None))
    if not items:
        raise ValueError("Paramount: no line items parsed")

    ref = ""
    for r in rows:
        toks = [t for _, _, t in r]
        if "Invoice" in toks and "#" in toks:
            for _, _, t in r:
                if re.fullmatch(r"\d{5,}", t):
                    ref = t
                    break
            if ref:
                break

    date = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "Invoice" in toks and "Date:" in toks:
            for _, _, t in r:
                m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
                if m:
                    date = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
                    break
            if date:
                break

    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"harry|gatt?os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "Invoice" in toks and "Total" in toks:
            for _, _, t in r:
                v = _m(t)
                if v is not None and v > 0:
                    total_incl = v
            if total_incl is not None:
                break
    if total_incl is None:
        raise ValueError("Paramount: invoice total not found")

    return Invoice(
        supplier_key="paramount", supplier_name_raw="Marlau Nominees Pty Ltd T/A Paramount Liquor",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
