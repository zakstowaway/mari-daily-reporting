"""
Draft-bill builder tests — pure, no network. Lock the safety guarantees: GST
reconciliation lines are dropped, tracking + tax type land on each line, and the
reconcile gate holds back a bill that doesn't add up.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,  # noqa: E402
                                     TaxTreatment, Venue)
from modules.invoices.xero_push import build_bill, push_bill  # noqa: E402


def _line(desc, incl, cls=LineClass.STOCK, tax=TaxTreatment.GST):
    return InvoiceLine(description=desc, qty=Decimal("1"), line_total_incl=Decimal(incl),
                       unit_price_incl=Decimal(incl), pack_size=1, line_class=cls,
                       tax_treatment=tax, cost_basis=CostBasis.PER_UNIT)


def _inv(lines, total, key="be_foods", name="B&E Foods", venue=Venue.STOWAWAY):
    return Invoice(supplier_key=key, supplier_name_raw=name, invoice_ref="INV1",
                   invoice_date=None, total_incl=Decimal(total), lines=lines, venue=venue)


def test_bill_is_draft_and_inclusive():
    payload, total, _ = build_bill(_inv([_line("CHICKEN 5KG", "50.00")], "50.00"))
    assert payload["Type"] == "ACCPAY" and payload["Status"] == "DRAFT"
    assert payload["LineAmountTypes"] == "Inclusive"


def test_gst_line_is_dropped_and_tracking_applied():
    inv = _inv([_line("CHICKEN", "50.00"), _line("GST", "5.00", LineClass.EXTRA)], "55.00")
    payload, total, _ = build_bill(inv)
    descs = [li["Description"] for li in payload["LineItems"]]
    assert "GST" not in descs and len(payload["LineItems"]) == 1
    assert payload["LineItems"][0]["Tracking"][0]["Option"] == "Kitchen"
    assert payload["LineItems"][0]["AccountCode"] == "115"


def test_tax_type_maps_per_line():
    inv = _inv([_line("PRODUCE", "10.00", tax=TaxTreatment.GST_FREE),
                _line("BEER", "10.00", tax=TaxTreatment.GST)], "20.00")
    payload, _, _ = build_bill(inv)
    tt = {li["Description"]: li["TaxType"] for li in payload["LineItems"]}
    assert tt["PRODUCE"] == "EXEMPTINPUT" and tt["BEER"] == "INPUT"


def test_reconcile_gate_holds_back_a_bad_bill():
    # invoice total says 100 but lines only add to 50 -> must NOT be pushable
    st = push_bill(_inv([_line("X", "50.00")], "100.00"), dry_run=True)
    assert st["action"] == "needs_review"


def test_clean_bill_is_ready():
    st = push_bill(_inv([_line("X", "50.00"), _line("Y", "50.00")], "100.00"), dry_run=True)
    assert st["action"] == "ready (dry-run)" and st["line_count"] == 2
