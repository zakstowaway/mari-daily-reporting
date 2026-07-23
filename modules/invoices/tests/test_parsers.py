"""
Deterministic supplier parsers — free, no API. Fixtures mirror the real PDF
text layer (each field on its own line, as PyMuPDF yields it).
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.invoices.models import CostBasis, TaxTreatment, Venue  # noqa: E402
from modules.invoices.parsers.select_fresh import parse as select_fresh_parse  # noqa: E402

SELECT_FRESH = """TAX  INVOICE   3054116
Invoice Date
12-JUN-26
HARGAT
Code
Description
Order
Supply
Unit
Price
Total
CHLR
CHILLI RED LONG KG
0.20
0.20
KG
13.50
2.70
HCHI
HERB CHIVES BCH
6.00
6.00
BUNCH
2.00
12.00
Total
14.70
GST
0.00
Invoice Total
14.70
Terms: 14 days
"""


def test_select_fresh_parses_and_reconciles():
    inv = select_fresh_parse(SELECT_FRESH)
    assert inv.invoice_ref == "3054116"
    assert inv.invoice_date.isoformat() == "2026-06-12"
    assert inv.venue == Venue.HARRY_GATOS          # HARGAT account -> billed to HG
    assert len(inv.lines) == 2                      # stops at the Total summary
    assert inv.total_incl == Decimal("14.70")       # STATED total, not the sum
    assert sum(l.line_total_incl for l in inv.lines) == inv.total_incl


def test_select_fresh_line_detail():
    inv = select_fresh_parse(SELECT_FRESH)
    chilli = inv.lines[0]
    assert chilli.supplier_code == "CHLR"
    assert chilli.qty == Decimal("0.20")
    assert chilli.unit_price_incl == Decimal("13.50")
    assert chilli.line_total_incl == Decimal("2.70")
    assert chilli.cost_basis == CostBasis.PER_KG    # KG unit
    assert chilli.tax_treatment == TaxTreatment.GST_FREE
    herbs = inv.lines[1]
    assert herbs.cost_basis == CostBasis.PER_UNIT   # BUNCH
