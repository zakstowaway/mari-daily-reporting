"""
The Fresh Fruit Team — deterministic parser (coordinate-based).

Columns:  QTY | SKU | UNIT | ITEM | UNIT PRICE | GST | AMOUNT
Descriptions and units sometimes wrap to extra visual rows, but the reconcile
fields (qty, sku, price, gst, amount) always sit on ONE "money row" — so we read
by word x-position (pdf_text.word_rows/bucket) and treat any row carrying
qty+price+amount as a line item. Footer Delivery Fee / Fuel Levy become EXTRA
lines; the stated "Total" is the reconcile target.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

# Column x-starts from the header row (QTY 30, SKU 68, UNIT 145, ITEM 200,
# UNIT PRICE 363, GST 451, AMOUNT 508).
COLS = [("qty", 0), ("sku", 64), ("unit", 143), ("desc", 198),
        ("price", 360), ("gst", 449), ("amt", 506)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")


def _m(s):
    s = (s or "").replace(",", "").strip()
    m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


@register("tfft.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"\bINB\d+\b", flat)
    ref = m.group(0) if m else ""
    date = None
    for x in flat.splitlines():
        if re.match(r"^\s*\d{1,2} [A-Za-z]{3} \d{4}\s*$", x):
            date = datetime.strptime(x.strip(), "%d %b %Y").date()
            break
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"harry\s*gatt?os", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "QTY" in toks and "SKU" in toks and "AMOUNT" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("FFT: header row not found")

    def is_money(row):
        cc = pdf_text.bucket(row, COLS)
        return (_m(cc["qty"]) is not None and _m(cc["price"]) is not None
                and _m(cc["amt"]) not in (None, Decimal("0")))

    body = rows[hi + 1:]
    items = []
    for idx, r in enumerate(body):
        c = pdf_text.bucket(r, COLS)
        qty, price, amt = _m(c["qty"]), _m(c["price"]), _m(c["amt"])
        if qty is None or price is None or amt is None:   # not a stock money row
            continue
        if amt == 0:                                      # substituted / zero-qty
            continue
        # FFT prints the money row in the MIDDLE of a wrapped description, so when
        # this row has no description of its own, stitch in the desc from the rows
        # immediately above and below (which carry no money).
        desc = c["desc"].strip()
        if not desc:
            parts = []
            if idx - 1 >= 0 and not is_money(body[idx - 1]):
                parts.append(pdf_text.bucket(body[idx - 1], COLS)["desc"].strip())
            if idx + 1 < len(body) and not is_money(body[idx + 1]):
                parts.append(pdf_text.bucket(body[idx + 1], COLS)["desc"].strip())
            desc = " ".join(p for p in parts if p).strip()
        g = _m(c["gst"]) or Decimal("0")
        unit = c["unit"]
        cb = CostBasis.PER_KG if re.search(r"kilo|kg", unit, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["sku"], qty=qty, line_total_incl=amt + g,
            unit_price_incl=price, pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST if g > 0 else TaxTreatment.GST_FREE),
            cost_basis=cb, supplier_code=c["sku"] or None, raw_uom=unit or None, gst_amount=g))
    if not items:
        raise ValueError("FFT: no line items parsed")

    L = [x.strip() for x in flat.splitlines() if x.strip()]
    # Footer extras as EXTRA lines: Delivery/Fuel, plus the GST Total — the
    # produce is GST-free, so the invoice's GST sits entirely on the taxable
    # extras (10% of the fuel levy). Capturing it here makes the sum reconcile.
    extras = [("Delivery Fee", TaxTreatment.GST_FREE), ("Fuel Levy", TaxTreatment.GST_FREE),
              ("GST Total", TaxTreatment.GST)]
    for label, tt in extras:
        for i, x in enumerate(L):
            if x == label and i + 1 < len(L):
                v = _m(L[i + 1])
                if v and v > 0:
                    items.append(InvoiceLine(
                        description=("GST" if label == "GST Total" else label),
                        qty=Decimal("1"), line_total_incl=v, unit_price_incl=v, pack_size=1,
                        line_class=LineClass.EXTRA, tax_treatment=tt, cost_basis=CostBasis.UNKNOWN))
                break

    total = None
    for i, x in enumerate(L):
        if x == "Total" and i + 1 < len(L) and _m(L[i + 1]) is not None:
            total = _m(L[i + 1])
            break
    if total is None:
        raise ValueError("FFT: invoice total not found")

    return Invoice(
        supplier_key="fresh_fruit_team", supplier_name_raw="The Fresh Fruit Team Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total, lines=items, venue=venue)
