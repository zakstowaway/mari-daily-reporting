"""
Select Fresh Providores — deterministic parser.

Layout (from the PDF text layer): a 7-field header row
    Code | Description | Order | Supply | Unit | Price | Total
then one record per line item as 7 consecutive text lines, until the "Total"
summary. GST-free produce, so incl == ex. Venue from the Account Code
(HARGAT -> Harry Gatos), honouring "billed-to wins" (Zak).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,
                                     TaxTreatment, Venue)
from modules.invoices.parsers import register

HEADER = ["Code", "Description", "Order", "Supply", "Unit", "Price", "Total"]
NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
UNIT_BASIS = {
    "KG": CostBasis.PER_KG, "LT": CostBasis.PER_KG, "L": CostBasis.PER_KG,
    "BUNCH": CostBasis.PER_UNIT, "EACH": CostBasis.PER_UNIT, "EA": CostBasis.PER_UNIT,
    "DOZEN": CostBasis.PER_UNIT, "DOZ": CostBasis.PER_UNIT, "PUNNET": CostBasis.PER_UNIT,
    "BOX": CostBasis.PER_UNIT, "TRAY": CostBasis.PER_UNIT, "PACKET": CostBasis.PER_UNIT,
    "PKT": CostBasis.PER_UNIT, "BAG": CostBasis.PER_UNIT,
}


@register("selectprovidores.com.au")
def parse(text: str) -> Invoice:
    L = [x.strip() for x in text.splitlines() if x.strip()]

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

    venue = (Venue.HARRY_GATOS if "HARGAT" in text
             else Venue.STOWAWAY if re.search(r"STOW", text, re.I) else Venue.UNKNOWN)

    start = None
    for j in range(len(L) - 6):
        if L[j:j + 7] == HEADER:
            start = j + 7
            break
    if start is None:
        raise ValueError("Select Fresh: header row not found")

    items = []
    k = start
    while k + 7 <= len(L):
        code, desc, order, supply, unit, price, total = L[k:k + 7]
        if not (NUM.match(order) and NUM.match(supply) and NUM.match(price)
                and NUM.match(total) and unit.upper() in UNIT_BASIS):
            break
        items.append(InvoiceLine(
            description=desc, qty=Decimal(supply), line_total_incl=Decimal(total),
            unit_price_incl=Decimal(price), pack_size=1, line_class=LineClass.STOCK,
            tax_treatment=TaxTreatment.GST_FREE, cost_basis=UNIT_BASIS[unit.upper()],
            supplier_code=code, raw_uom=unit))
        k += 7
    if not items:
        raise ValueError("Select Fresh: no line items parsed")

    total_incl = None
    for i, x in enumerate(L):
        if x.lower() == "invoice total" and i + 1 < len(L) and NUM.match(L[i + 1]):
            total_incl = Decimal(L[i + 1])
            break
    if total_incl is None:
        raise ValueError("Select Fresh: invoice total not found")

    return Invoice(
        supplier_key="select_fresh", supplier_name_raw="Select Fresh Providores",
        invoice_ref=ref, invoice_date=date, total_incl=total_incl,
        lines=items, venue=venue)
