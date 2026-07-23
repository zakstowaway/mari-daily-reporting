"""
B&E Foods — deterministic parser (coordinate-based).

Columns:  Item Code | Description | Ordered | Shipped | UOM | Ship Doc | Item Price | GST | Line Total
Shipped is the delivered qty; Line Total is GST-INCLUSIVE (ex + the GST column),
so it sums straight to the invoice's "Total" (incl). Venue from the "Sold To"
(billed-to) column on the right, not the "Deliver To" on the left (Zak: billed-to
wins). Non-food lines (chemicals, napkins) come through as ordinary items —
harmless; the recipe side only ever picks food.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 0), ("desc", 70), ("ordered", 215), ("shipped", 260), ("uom", 310),
        ("shipdoc", 340), ("price", 400), ("gst", 460), ("total", 510)]
MONEY = re.compile(r"^\$?(-?[\d,]+\.?\d*)$")
EXTRA_DESC = re.compile(r"fuel\s*levy|freight|delivery|cartage", re.I)


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.match(s if s.startswith("-") or s[:1].isdigit() else "$" + s)
    if not m:
        m = MONEY.match(s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except InvalidOperation:
        return None


def _venue(rows) -> Venue:
    # "Sold To" is the right-hand column (x >= 270). Collect its text from the
    # rows just after the "Sold To:" header and read the billed-to name.
    start = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Sold" in toks and "To:" in toks:
            start = i
            break
    blob = ""
    if start is not None:
        for r in rows[start:start + 5]:
            blob += " " + " ".join(t for x0, _, t in r if x0 >= 270)
    if re.search(r"marilyna", blob, re.I):
        return Venue.MARILYNAS
    if re.search(r"gatt?os", blob, re.I):
        return Venue.HARRY_GATOS
    if re.search(r"stowaway", blob, re.I):
        return Venue.STOWAWAY
    return Venue.UNKNOWN


@register("befoods.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)

    m = re.search(r"Invoice\s+No:\s*(\S+)", flat, re.I)
    ref = m.group(1) if m else ""
    m = re.search(r"Invoice\s+Date:\s*(\d{2}/\d{2}/\d{4})", flat, re.I)
    date = datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None
    venue = _venue(rows)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Description" in toks and "Shipped" in toks and "Total" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("B&E: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        qty, total = _m(c["shipped"]), _m(c["total"])
        if qty is None or total is None or qty == 0 or total == 0:
            continue
        gst = _m(c["gst"]) or Decimal("0")
        desc = c["desc"]
        is_extra = bool(EXTRA_DESC.search(desc))
        uom = c["uom"]
        cb = CostBasis.PER_KG if re.fullmatch(r"KG|KILO(GRAM)?", uom, re.I) else CostBasis.PER_UNIT
        items.append(InvoiceLine(
            description=desc or c["code"], qty=qty, line_total_incl=total,
            unit_price_incl=(total / qty).quantize(Decimal("0.0001")), pack_size=1,
            line_class=LineClass.EXTRA if is_extra else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if gst > 0 else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.UNKNOWN if is_extra else cb,
            supplier_code=None if is_extra else (c["code"] or None),
            raw_uom=uom or None, gst_amount=gst))
    if not items:
        raise ValueError("B&E: no line items parsed")

    # Grand total: the "Total" row with a value and NO 'Ex' (that one is the
    # ex-GST subtotal). Avoid the account 'OUTSTANDING AMOUNT'.
    total_incl = None
    for r in rows:
        toks = [t for _, _, t in r]
        if toks and toks[0] == "Total" and "Ex" not in toks:
            nums = [_m(t) for _, _, t in r if _m(t) is not None]
            if nums:
                total_incl = nums[-1]
    if total_incl is None:
        raise ValueError("B&E: invoice total not found")

    return Invoice(
        supplier_key="be_foods", supplier_name_raw="B&E Foods Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
