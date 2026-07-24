"""
Lion (Beer, Spirits & Wine) — deterministic parser (coordinate-based).

A wide template: each product line carries its own unit/product value, discount,
WET, fuel surcharge, freight and handling columns, then a right-most LINE VALUE
(Excl. GST). Those line values sum to the printed SUBTOTAL, and the whole
subtotal is GST-taxable (GST == subtotal/10), so line_excl x 1.1 gives the incl
line and the column sums to the "INVOICE TOTAL ... AUD" (inc GST). Product codes
are 6-7 digit numbers in the left column; the payment panel on the right is
ignored (no numbers on product rows past the line-value column).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

MONEY = re.compile(r"^-?[\d,]+\.\d{2}$")   # line values always carry 2 decimals


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


@register("lionco.com")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"INVOICE\s+No\s*(\d+)", flat, re.I) or re.search(r"\b(94\d{6})\b", flat)
    ref = m.group(1) if m else ""
    # Lion's summary panel (right column, x>=700) stacks the invoice DATE and the
    # PAYMENT DUE DATE as bare d/m/y values. Earliest = invoice date, latest = due.
    panel = []
    for r in rows:
        for x0, _, t in r:
            if x0 >= 700:
                dm = re.match(r"(\d{2})/(\d{2})/(\d{4})$", t)
                if dm:
                    try:
                        panel.append(datetime(int(dm[3]), int(dm[2]), int(dm[1])).date())
                    except ValueError:
                        pass
    date = min(panel) if panel else None
    due = max(panel) if len(panel) >= 2 else None
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    items = []
    for r in rows:
        code = r[0][2].strip() if r else ""
        if not (r[0][0] < 20 and code.isdigit() and len(code) >= 6):
            continue
        line_excl = next((v for x0, _, t in reversed(r) if x0 >= 600 and (v := _m(t)) is not None), None)
        if line_excl is None or line_excl == 0:
            continue
        # qty prints as a bare integer just after the UM, before the value columns
        qraw = next((t for x0, _, t in r if 200 <= x0 < 265 and t.strip().isdigit()), None)
        qty = Decimal(qraw) if qraw and Decimal(qraw) > 0 else Decimal("1")
        incl = (line_excl * Decimal("1.1")).quantize(Decimal("0.01"))
        desc = " ".join(t for x0, _, t in r if 30 <= x0 < 170)
        items.append(InvoiceLine(
            description=desc or code, qty=qty, line_total_incl=incl,
            unit_price_incl=(incl / qty).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.STOCK, tax_treatment=TaxTreatment.GST,
            cost_basis=CostBasis.PER_UNIT, supplier_code=code))
    if not items:
        raise ValueError("Lion: no line items parsed")

    # Invoice total (inc GST) sits with "AUD" in the summary box.
    total_incl = None
    for r in rows:
        aud_x = next((x0 for x0, _, t in r if t == "AUD"), None)
        if aud_x is None:
            continue
        vals = [v for x0, _, t in r if 700 <= x0 < aud_x and (v := _m(t)) is not None]
        if vals:
            total_incl = vals[-1]
            break
    if total_incl is None:
        raise ValueError("Lion: invoice total not found")

    return Invoice(
        supplier_key="lion", supplier_name_raw="Lion - Beer, Spirits & Wine Pty Ltd",
        invoice_ref=ref, invoice_date=date, due_date=due, total_incl=total_incl, lines=items, venue=venue)
