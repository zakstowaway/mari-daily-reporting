"""
Independent Liquor Group (ILG) — deterministic parser (coordinate-based).

Columns:  Code | Description | Pack | Qty | Cost | Total | FRT/case | LUC ex GST | TOT inc GST
The right-most "TOT inc GST" column already has this line's share of freight,
fuel levy and GST folded in, so the column sums straight to the footer "Total"
(inc) — no separate freight/GST EXTRA lines, or we'd double-count. Venue from
the "Bill To" (left) block, not "Deliver To" on the right (Zak: billed-to wins).
Ignore the "Discounted Invoice Total" (a pay-early direct-debit figure).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 0), ("desc", 65), ("pack", 225), ("qty", 278), ("cost", 308),
        ("total", 355), ("frt", 405), ("luc", 448), ("totinc", 498)]
MONEY = re.compile(r"^-?[\d,]+\.?\d*$")


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    if not MONEY.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _venue(rows) -> Venue:
    # "Bill To" is the left block (x < 300); "Deliver To" is on the right.
    start = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Bill" in toks and "To:" in toks:
            start = i
            break
    blob = ""
    if start is not None:
        for r in rows[start:start + 4]:
            blob += " " + " ".join(t for x0, _, t in r if x0 < 300)
    if re.search(r"marilyna", blob, re.I):
        return Venue.MARILYNAS
    if re.search(r"gatt?os", blob, re.I):
        return Venue.HARRY_GATOS
    if re.search(r"stowaway", blob, re.I):
        return Venue.STOWAWAY
    return Venue.UNKNOWN


@register("ilg.com.au", "members.ilg.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Invoice\s+No\.?\s*(\d+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Invoice\s+Date\s*(\d{1,2}-[A-Z]{3}-\d{4})", flat, re.I)
    date = datetime.strptime(m.group(1).upper(), "%d-%b-%Y").date() if m else None
    venue = _venue(rows)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Code" in toks and "Pack" in toks and ("Qty" in toks or "Cost" in toks):
            hi = i
            break
    if hi is None:
        raise ValueError("ILG: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        code = c["code"].strip()
        # an item line = a product code (NNN-NNNN); the TOT-inc column (right of
        # x498) carries this line's GST-inclusive total, freight+levy already
        # allocated, so the column sums straight to the footer Total.
        if not re.match(r"\d{3}-\d{3,4}", code):
            continue
        totinc = None
        for x0, _, t in r:
            if x0 >= 498:
                v = _m(t)               # skip trailing markers like "3FA"
                if v is not None:
                    totinc = v
        if totinc is None or totinc == 0:
            continue
        qraw = c["qty"].strip()
        qty = _m(qraw.split("/")[-1]) if "/" in qraw else _m(qraw)   # "0/1" repack -> 1
        if qty is None or qty == 0:
            qty = Decimal("1")
        items.append(InvoiceLine(
            description=c["desc"] or code, qty=qty, line_total_incl=totinc,
            unit_price_incl=(totinc / qty).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.STOCK, tax_treatment=TaxTreatment.GST,
            cost_basis=CostBasis.PER_UNIT, supplier_code=code or None,
            raw_uom=(c["pack"].strip() or None)))
    if not items:
        raise ValueError("ILG: no line items parsed")

    # Grand total = footer "Total" in the right-hand totals column (x ~303).
    # Not "Sub Total:" (x~245) nor "Discounted Invoice Total:" (left block).
    total_incl = None
    for r in rows:
        for x0, _, t in r:
            if t == "Total" and 295 <= x0 <= 315:
                nums = [_m(tt) for _, _, tt in r if _m(tt) is not None]
                if nums:
                    total_incl = nums[-1]
    if total_incl is None:
        raise ValueError("ILG: invoice total not found")

    return Invoice(
        supplier_key="ilg", supplier_name_raw="Independent Liquor Group",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
