"""
Gulli Food Distributors — deterministic parser (coordinate-based).

Columns:  Product Code | Description | Quantity | (UOM) | Unit Price | GST% | Amount
The Amount column is EX-GST and the GST column is a per-line rate (0% / 10%), so
each line's incl total is amount x (1 + rate). Reconcile target: footer "Total".
"Standard Delivery" lines come through as EXTRA. Venue from the customer code /
ship-to name in the flat text.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

MONEY = re.compile(r"^-?[\d,]+\.?\d*$")
EXTRA_DESC = re.compile(r"delivery|freight|fuel\s*levy|cartage", re.I)


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


@register("gullifood.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Tax\s+Invoice\s+(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Invoice\s+Date:\s*(\d{2}/\d{2}/\d{4})", flat, re.I)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = (Venue.MARILYNAS if re.search(r"marilyna|MARI0", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT|HGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway|STOW0", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "DESCRIPTION" in toks and "AMOUNT" in toks and ("QUANTITY" in toks or "GST" in toks):
            hi = i
            break
    if hi is None:
        raise ValueError("Gulli: header row not found")

    # Row parsing anchored on the GST% token (qty/price column x-positions drift
    # so much between invoices that a fixed split can't separate them, but the
    # GST% cell is stable): the two numbers LEFT of GST% are qty (first) and unit
    # price (last); the amount is the number RIGHT of it. Footer rows (Total /
    # "GST 10% on $..") have <2 numbers left of their %, so they fall out.
    items = []
    for r in rows[hi + 1:]:
        gi = next(((x0, m) for x0, _, t in r if (m := re.match(r"(\d+)%$", t))), None)
        if not gi:
            continue
        gst_x, gm = gi
        left = [v for x0, _, t in r if 335 <= x0 < gst_x and (v := _m(t)) is not None]
        amt = next((v for x0, _, t in reversed(r) if x0 > gst_x and (v := _m(t)) is not None), None)
        if len(left) < 2 or amt is None:
            continue
        qty, price = left[0], left[-1]
        code = next((t for x0, _, t in r if x0 < 125 and t.strip()), "")
        desc = " ".join(t for x0, _, t in r if 125 <= x0 < 335)
        pct = Decimal(gm.group(1))
        f = 1 + pct / 100
        incl = (amt * f).quantize(Decimal("0.01"))
        if incl == 0:
            continue
        is_extra = bool(EXTRA_DESC.search(desc))
        items.append(InvoiceLine(
            description=desc or code, qty=qty, line_total_incl=incl,
            unit_price_incl=(price * f).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if pct > 0 else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else CostBasis.PER_UNIT,
            supplier_code=None if is_extra else (code.strip() or None), raw_uom=None))
    if not items:
        raise ValueError("Gulli: no line items parsed")

    # Grand total: footer "Total" (x ~345). Not "Account Balance".
    total_incl = None
    for r in rows:
        for x0, _, t in r:
            if t == "Total" and 330 <= x0 <= 360:
                nums = [_m(tt) for _, _, tt in r if _m(tt) is not None]
                if nums:
                    total_incl = nums[-1]
    if total_incl is None:
        raise ValueError("Gulli: invoice total not found")

    return Invoice(
        supplier_key="gulli", supplier_name_raw="Gulli Food Distributors Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
