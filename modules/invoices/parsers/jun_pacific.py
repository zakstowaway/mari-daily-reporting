"""
Jun Pacific Corporation — deterministic parser (coordinate-based).

Columns:  Code | Description | Size | CoO | Tax | List Price | Unit Price | Qty | Amount
Amounts are EX-GST; the Tax column carries a per-line code ('G' = GST, 'W' = WET),
so a 'G' line's incl total is amount x 1.1 and everything else is GST-free. That
makes the incl column sum to the footer TOTAL (= Sub-Total + GST). Most lines are
GST-free Japanese groceries. Multi-page; each page repeats the header.
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


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


@register("junpacific.com")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Tax\s+Invoice:\s*(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})", flat)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", flat, re.I) else Venue.UNKNOWN)

    # Header appears once per page ("Code ... Description ... Qty ... Amount").
    header_ys = [i for i, r in enumerate(rows)
                 if "Code" in [t for _, _, t in r] and "Description" in [t for _, _, t in r]]
    if not header_ys:
        raise ValueError("Jun Pacific: header row not found")

    seen = set()
    items = []
    for hi in header_ys:
        for r in rows[hi + 1:]:
            toks = [t for _, _, t in r]
            if "Code" in toks or "Sub-Total" in toks or "TOTAL" in toks:
                break                                    # next header / totals block
            code = next((t for x0, _, t in r if x0 < 70 and t.strip()), "")
            if not re.match(r"[A-Z]{1,3}\d{4,}", code):   # a real product code
                continue
            nums = [v for x0, _, t in r if x0 >= 400 and (v := _m(t)) is not None]
            if len(nums) < 3:                             # need unit price, qty, amount
                continue
            amount, qty = nums[-1], nums[-2]
            if amount == 0 or qty == 0:
                continue
            key = (code, amount, qty)
            if key in seen:                               # de-dupe repeated pages
                continue
            seen.add(key)
            taxable = any(re.match(r"[GW]$", t) and 365 <= x0 < 405 for x0, _, t in r)
            f = Decimal("1.1") if taxable else Decimal("1")
            incl = (amount * f).quantize(Decimal("0.01"))
            desc = " ".join(t for x0, _, t in r if 70 <= x0 < 300)
            items.append(InvoiceLine(
                description=desc or code, qty=qty, line_total_incl=incl,
                unit_price_incl=(incl / qty).quantize(Decimal("0.0001")), pack_size=1,
                line_class=LineClass.STOCK,
                tax_treatment=TaxTreatment.GST if taxable else TaxTreatment.GST_FREE,
                cost_basis=CostBasis.PER_UNIT, supplier_code=code.strip() or None))
    if not items:
        raise ValueError("Jun Pacific: no line items parsed")

    # Grand total: the "TOTAL" row carrying a $ value (not "Total Due"/"TOTAL PAID").
    total_incl = None
    for r in rows:
        if "TOTAL" in [t for _, _, t in r]:
            nums = [_m(t) for _, _, t in r if _m(t) is not None]
            if nums:
                total_incl = nums[-1]
                break
    if total_incl is None:
        raise ValueError("Jun Pacific: invoice total not found")

    return Invoice(
        supplier_key="jun_pacific", supplier_name_raw="Jun Pacific Corporation Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
