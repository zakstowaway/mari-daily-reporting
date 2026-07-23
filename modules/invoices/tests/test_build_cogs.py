"""
Aggregator: validated invoice JSON -> cogs_list rows.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.invoices.build_cogs_list import _rows_from_invoice  # noqa: E402


def _payload(lines):
    return {"invoice": {
        "supplier_key": "fresh_fruit_team", "supplier_name_raw": "The Fresh Fruit Team Pty Ltd",
        "invoice_ref": "INB999", "invoice_date": "2026-07-22", "venue": "stowaway",
        "lines": lines,
    }}


def test_stock_line_maps_to_a_cogs_row_with_the_short_supplier_name():
    rows = _rows_from_invoice(_payload([
        {"description": "Cauliflower Florets", "supplier_code": "KITCFKG",
         "qty": "3", "line_total_incl": "23.70", "unit_price_incl": "7.90",
         "cost_basis": "per_kg", "line_class": "stock", "notes": []},
    ]))
    assert len(rows) == 1
    r = rows[0]
    assert r["supplier"] == "Fresh Fruit Team"        # short name, not the legal name
    assert r["supplier_code"] == "KITCFKG"
    assert r["cost_per_unit_incl_gst"] == "7.90"
    assert r["basis"] == "per_kg"
    assert r["venue"] == "stowaway"
    assert r["source_invoice"] == "INB999"
    assert r["in_bounds"] == "yes"


def test_non_stock_lines_are_excluded():
    rows = _rows_from_invoice(_payload([
        {"description": "Delivery Fee", "supplier_code": "", "qty": "1",
         "line_total_incl": "5.50", "unit_price_incl": "5.50",
         "cost_basis": "per_unit", "line_class": "extra", "notes": []},
        {"description": "Waiting on stock", "supplier_code": "X", "qty": "0",
         "line_total_incl": "0", "line_class": "wos", "notes": []},
    ]))
    assert rows == []                                  # freight + WOS are not ingredients


def test_unit_price_is_derived_when_missing():
    rows = _rows_from_invoice(_payload([
        {"description": "Onion Spanish", "supplier_code": "OSKG", "qty": "1.2",
         "line_total_incl": "2.90", "unit_price_incl": None,
         "cost_basis": "per_kg", "line_class": "stock", "notes": []},
    ]))
    assert rows[0]["cost_per_unit_incl_gst"] == "2.4167"   # 2.90 / 1.2


def test_notes_carry_through_for_pack_context():
    rows = _rows_from_invoice(_payload([
        {"description": "Tomatoes Cherry", "supplier_code": "TCPUN", "qty": "1",
         "line_total_incl": "1.76", "unit_price_incl": "1.76",
         "cost_basis": "per_unit", "line_class": "stock", "notes": ["Punnet"]},
    ]))
    assert rows[0]["note"] == "Punnet"
