"""Xero bill-import CSV tests — pure. Lock the row shape, tax-name mapping, GST
line drop, and exact-total (Quantity 1 + inclusive amount)."""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from datetime import date  # noqa: E402

from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,  # noqa: E402
                                     TaxTreatment, Venue)
from modules.invoices.xero_csv import bill_rows  # noqa: E402


def _line(desc, incl, cls=LineClass.STOCK, tax=TaxTreatment.GST, qty="1"):
    return InvoiceLine(description=desc, qty=Decimal(qty), line_total_incl=Decimal(incl),
                       unit_price_incl=Decimal(incl), pack_size=1, line_class=cls,
                       tax_treatment=tax, cost_basis=CostBasis.PER_UNIT)


def _inv(lines, key="be_foods", name="B&E Foods", venue=Venue.STOWAWAY):
    return Invoice(supplier_key=key, supplier_name_raw=name, invoice_ref="INV1",
                   invoice_date=date(2026, 5, 1), total_incl=Decimal("0"), lines=lines, venue=venue)


def test_row_shape_and_tax_names():
    rows = bill_rows(_inv([_line("PRODUCE", "10.00", tax=TaxTreatment.GST_FREE),
                           _line("BEER", "10.00", tax=TaxTreatment.GST)]))
    tt = {r["Description"]: r["*TaxType"] for r in rows}
    assert tt["PRODUCE"] == "GST Free Expenses" and tt["BEER"] == "GST on Expenses"
    assert all(r["*Quantity"] == "1" for r in rows)          # exact-total convention
    assert rows[0]["TrackingOption1"] == "Kitchen"


def test_gst_line_dropped():
    rows = bill_rows(_inv([_line("FOOD", "50.00"), _line("GST", "5.00", LineClass.EXTRA)]))
    assert [r["Description"] for r in rows] == ["FOOD"]


def test_qty_is_kept_in_description():
    rows = bill_rows(_inv([_line("WINGS", "24.00", qty="3")]))
    assert rows[0]["Description"].startswith("3 x WINGS") and rows[0]["*UnitAmount"] == "24.00"


def test_split_coding_across_accounts():
    rows = bill_rows(_inv([_line("CHICKEN", "20.00"), _line("NAPKINS", "5.00"),
                           _line("Fuel Levy", "3.00", LineClass.EXTRA)]))
    assert sorted(r["*AccountCode"] for r in rows) == ["115", "117", "342"]
