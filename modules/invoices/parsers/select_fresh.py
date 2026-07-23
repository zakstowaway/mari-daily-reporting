"""
Select Fresh Providores — deterministic parser (coordinate-based).

Columns:  Code | Description | Order | Supply | Unit | Price | Total
Read by word x-position so wrapped descriptions don't break it. Supply is the
delivered qty (Supply x Price = Total). GST-free produce; the GST Total and any
freight land as EXTRA lines. Venue from the billed-to name, billed-to wins
(Zak): an invoice on the Harry Gatos account delivered to Stowaway is HG.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from modules.invoices import pdf_text
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

COLS = [("code", 0), ("desc", 78), ("order", 290), ("supply", 348),
        ("unit", 378), ("price", 460), ("total", 525)]
MONEY = re.compile(r"-?\d[\d,]*\.?\d*")   # search, not full-match: tolerate trailing
                                          # markers like "SD" (short delivery) after a value
UNIT_BASIS = {
    "KG": CostBasis.PER_KG, "LT": CostBasis.PER_KG, "L": CostBasis.PER_KG,
    "BUNCH": CostBasis.PER_UNIT, "EACH": CostBasis.PER_UNIT, "EA": CostBasis.PER_UNIT,
    "DOZEN": CostBasis.PER_UNIT, "DOZ": CostBasis.PER_UNIT, "PUNNET": CostBasis.PER_UNIT,
    "BOX": CostBasis.PER_UNIT, "TRAY": CostBasis.PER_UNIT, "PACKET": CostBasis.PER_UNIT,
    "PKT": CostBasis.PER_UNIT, "BAG": CostBasis.PER_UNIT, "BOTTLE": CostBasis.PER_UNIT,
    "PIECE": CostBasis.PER_UNIT, "PC": CostBasis.PER_UNIT,
}


def _m(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    m = MONEY.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


@register("selectprovidores.com.au")
def parse(pdf_bytes: bytes) -> Invoice:
    rows = pdf_text.word_rows(pdf_bytes)
    flat = pdf_text.text(pdf_bytes)
    L = [x.strip() for x in flat.splitlines() if x.strip()]

    ref = ""
    for x in L:
        m = re.search(r"TAX\s+INVOICE\s+(\S+)", x)
        if m:
            ref = m.group(1)
            break
    date = None
    for x in L:
        m = re.search(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2})\b", x)
        if m:
            date = datetime.strptime(m.group(1).upper(), "%d-%b-%y").date()
            break
    # billed-to wins: Marilyna's / Harry Gatos (HARGAT) before Stowaway (which
    # also appears as the delivery address on HG invoices).
    venue = (Venue.MARILYNAS if re.search(r"marilyna", flat, re.I)
             else Venue.HARRY_GATOS if re.search(r"gatt?os|HARGAT", flat, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway|STOW", flat, re.I) else Venue.UNKNOWN)

    hi = None
    for i, r in enumerate(rows):
        toks = [t for _, _, t in r]
        if "Code" in toks and "Description" in toks and "Total" in toks:
            hi = i
            break
    if hi is None:
        raise ValueError("Select Fresh: header row not found")

    items = []
    for r in rows[hi + 1:]:
        c = pdf_text.bucket(r, COLS)
        supply, price, total = _m(c["supply"]), _m(c["price"]), _m(c["total"])
        if supply is None or price is None or total is None:   # not a stock money row
            continue
        if total == 0:
            continue
        unit = c["unit"]
        cb = UNIT_BASIS.get(unit.upper(), CostBasis.PER_UNIT)
        items.append(InvoiceLine(
            description=c["desc"] or c["code"], qty=supply, line_total_incl=total,
            unit_price_incl=price, pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=TaxTreatment.GST_FREE, cost_basis=cb,
            supplier_code=c["code"] or None, raw_uom=unit or None))
    if not items:
        raise ValueError("Select Fresh: no line items parsed")

    # Footer extras as EXTRA lines: GST Total + any freight/delivery/cartage.
    for i, x in enumerate(L):
        if x == "GST" and i + 1 < len(L):
            v = _m(L[i + 1])
            if v and v > 0:
                items.append(InvoiceLine(description="GST", qty=Decimal("1"), line_total_incl=v,
                    unit_price_incl=v, pack_size=1, line_class=LineClass.EXTRA,
                    tax_treatment=TaxTreatment.GST, cost_basis=CostBasis.UNKNOWN))
            break
    for i, x in enumerate(L):
        if re.fullmatch(r"(Freight|Delivery|Cartage|Fuel Levy)", x, re.I) and i + 1 < len(L):
            v = _m(L[i + 1])
            if v and v > 0:
                items.append(InvoiceLine(description=x, qty=Decimal("1"), line_total_incl=v,
                    unit_price_incl=v, pack_size=1, line_class=LineClass.EXTRA,
                    tax_treatment=TaxTreatment.GST_FREE, cost_basis=CostBasis.UNKNOWN))

    total_incl = None
    for i, x in enumerate(L):
        if x.lower() == "invoice total" and i + 1 < len(L) and _m(L[i + 1]) is not None:
            total_incl = _m(L[i + 1])
            break
    if total_incl is None:
        raise ValueError("Select Fresh: invoice total not found")

    return Invoice(
        supplier_key="select_fresh", supplier_name_raw="Select Fresh Providores",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl, lines=items, venue=venue)
