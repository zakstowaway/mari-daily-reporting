"""
The Fresh Fruit Team — deterministic parser.

Layout: a 7-field header  QTY | SKU | UNIT | ITEM | UNIT PRICE | GST | AMOUNT
then one record per line item as 7 consecutive text lines (prices are
$-prefixed). Footer carries Subtotal / GST Total / Delivery Fee / Fuel Levy /
Total. Mostly GST-free produce; the Fuel Levy is an extra the validator excludes.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

HEADER = ["QTY", "SKU", "UNIT", "ITEM", "UNIT PRICE", "GST", "AMOUNT"]
NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
MONEY = re.compile(r"^\$?(-?\d+(?:\.\d+)?)$")
UNIT_BASIS = {
    "KILOGRAM": CostBasis.PER_KG, "KG": CostBasis.PER_KG,
    "EACH": CostBasis.PER_UNIT, "BUNCH": CostBasis.PER_UNIT, "BOX": CostBasis.PER_UNIT,
    "TRAY": CostBasis.PER_UNIT, "MARKET": CostBasis.PER_UNIT, "PUNNET": CostBasis.PER_UNIT,
    "BAG": CostBasis.PER_UNIT, "DOZEN": CostBasis.PER_UNIT, "PACKET": CostBasis.PER_UNIT,
}


def _money(s: str):
    m = MONEY.match(s)
    return Decimal(m.group(1)) if m else None


@register("tfft.com.au")
def parse(text: str) -> Invoice:
    L = [x.strip() for x in text.splitlines() if x.strip()]

    # The flattened text puts values before their labels, so match the ref
    # pattern directly (FFT refs are INB followed by digits).
    m = re.search(r"\bINB\d+\b", text)
    ref = m.group(0) if m else ""
    date = None
    for x in L:
        if re.match(r"^\d{1,2} [A-Za-z]{3} \d{4}$", x):
            date = datetime.strptime(x, "%d %b %Y").date()
            break
    venue = (Venue.HARRY_GATOS if re.search(r"harry\s*gatt?os", text, re.I)
             else Venue.STOWAWAY if re.search(r"stowaway", text, re.I) else Venue.UNKNOWN)

    start = None
    for j in range(len(L) - 6):
        if L[j:j + 7] == HEADER:
            start = j + 7
            break
    if start is None:
        raise ValueError("FFT: header row not found")

    items = []
    k = start
    while k + 7 <= len(L):
        qty, sku, unit, desc, up, gst, amt = L[k:k + 7]
        a, g, u = _money(amt), _money(gst), _money(up)
        if not (NUM.match(qty) and a is not None and g is not None and u is not None
                and unit.upper() in UNIT_BASIS):
            break
        k += 7
        if a == 0:                                    # substituted / zero-qty line
            continue
        items.append(InvoiceLine(
            description=desc, qty=Decimal(qty), line_total_incl=a + g,
            unit_price_incl=u, pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=(TaxTreatment.GST if g > 0 else TaxTreatment.GST_FREE),
            cost_basis=UNIT_BASIS[unit.upper()], supplier_code=sku, raw_uom=unit,
            gst_amount=g))
    if not items:
        raise ValueError("FFT: no line items parsed")

    # Footer extras — captured as EXTRA lines so the validator excludes them
    # from the stock reconcile but they still count toward the invoice total.
    for label in ("Delivery Fee", "Fuel Levy"):
        for i, x in enumerate(L):
            if x == label and i + 1 < len(L):
                v = _money(L[i + 1])
                if v and v > 0:
                    items.append(InvoiceLine(
                        description=label, qty=Decimal("1"), line_total_incl=v,
                        unit_price_incl=v, pack_size=1, line_class=LineClass.EXTRA,
                        tax_treatment=TaxTreatment.GST_FREE, cost_basis=CostBasis.UNKNOWN))
                break

    total = None
    for i, x in enumerate(L):
        if x == "Total" and i + 1 < len(L) and _money(L[i + 1]) is not None:
            total = _money(L[i + 1])
            break
    if total is None:
        raise ValueError("FFT: invoice total not found")

    return Invoice(
        supplier_key="fresh_fruit_team", supplier_name_raw="The Fresh Fruit Team Pty Ltd",
        invoice_ref=ref, invoice_date=date, total_incl=total, lines=items, venue=venue)
