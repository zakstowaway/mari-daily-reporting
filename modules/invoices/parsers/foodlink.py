"""
Foodlink Australia — deterministic parser (coordinate-based).

Columns:  No. | Description | Qty. | UOM | Weight | Unit Price Excl GST | GST | Total Amount Excl GST
Line amounts are EX-GST; a "GST" token in the GST column marks a taxable line,
so its incl total is ex x 1.1 (GST-free lines are unchanged). Fuel Levy is a
taxable line but classed EXTRA. Reconcile target: "Total AUD Incl GST".
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 0), ("desc", 60), ("qty", 245), ("uom", 270), ("weight", 320),
        ("price", 360), ("gstflag", 455), ("total", 490)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")
EXTRA_DESC = re.compile(r"fuel\s*levy|freight|delivery|cartage", re.I)


def _m(s):
    s = (s or "").replace(",", "").strip()
    m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


@register("foodlinkaustralia.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Tax\s+Invoice\s+(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Date:\s*(\d{2}/\d{2}/\d{4})", flat)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Description" in toks and "Qty." in toks and ("UOM" in toks or "Total" in toks):
            hi = i
            break
    if hi is None:
        raise ValueError("Foodlink: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        qty, price, total = _m(c["qty"]), _m(c["price"]), _m(c["total"])
        if qty is None or price is None or total is None:
            continue
        if total == 0:
            continue
        taxable = "GST" in c["gstflag"].upper()
        f = Decimal("1.1") if taxable else Decimal("1")
        incl = (total * f).quantize(Decimal("0.01"))
        up_incl = (price * f).quantize(Decimal("0.0001"))
        desc = c["desc"]
        is_extra = bool(EXTRA_DESC.search(desc))
        uom = c["uom"]
        cb = CostBasis.PER_KG if re.fullmatch(r"KG|KILO(GRAM)?", uom, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["code"], qty=qty, line_total_incl=incl,
            unit_price_incl=up_incl, pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if taxable else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else cb,
            supplier_code=None if is_extra else (c["code"] or None), raw_uom=uom or None))
    if not items:
        raise ValueError("Foodlink: no line items parsed")

    # Reconcile target: "Total AUD Incl GST" — the row carrying 'Incl' + a number.
    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if "Incl." in toks or "Incl" in toks:
            nums = [_m(t) for _, _, t in r if _m(t) is not None]
            if nums:
                total_incl = nums[-1]
                break
    if total_incl is None:
        raise ValueError("Foodlink: incl-GST total not found")

    return Invoice(
        supplier_key="foodlink", supplier_name_raw="Foodlink Australia Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
