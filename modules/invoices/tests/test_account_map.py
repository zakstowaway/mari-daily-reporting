"""
Coding-suggester tests — pure, no PDFs. Lock the layered rule precedence and the
produce-vs-packaging trap (a "Limes Tray" is food, a "Pizza Box" is packaging).
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modules.invoices.account_map import (BEVERAGE, CLEANING, FOOD, FREIGHT,  # noqa: E402
                                          OTHER_COGS, PACKAGING, suggest_coding)
from modules.invoices.models import (CostBasis, Invoice, InvoiceLine, LineClass,  # noqa: E402
                                     TaxTreatment, Venue)


def _line(desc, cls=LineClass.STOCK):
    return InvoiceLine(description=desc, qty=Decimal("1"), line_total_incl=Decimal("10"),
                       unit_price_incl=Decimal("10"), pack_size=1, line_class=cls,
                       tax_treatment=TaxTreatment.GST, cost_basis=CostBasis.PER_UNIT)


def _inv(key, lines, name="", venue=Venue.STOWAWAY):
    return Invoice(supplier_key=key, supplier_name_raw=name or key, invoice_ref="1",
                   invoice_date=None, total_incl=Decimal("10"), lines=lines, venue=venue)


def test_known_food_supplier_codes_to_food():
    c = suggest_coding(_inv("select_fresh", [_line("CHILLI JALAPENO GREEN KG")]))
    assert c.lines[0].account_code == FOOD


def test_known_liquor_supplier_codes_to_beverages():
    c = suggest_coding(_inv("ilg", [_line("SAPPORO KEG 50LT")]))
    assert c.lines[0].account_code == BEVERAGE


def test_line_keyword_beats_supplier_default():
    c = suggest_coding(_inv("gulli", [_line("B Flute Lock Top 11\" Pizza Box"),
                                      _line("Fuel Levy", LineClass.EXTRA),
                                      _line("Dishwashing Liquid 20LT")]))
    assert [l.account_code for l in c.lines] == [PACKAGING, FREIGHT, CLEANING]


def test_produce_pack_units_are_not_packaging():
    # bare tray/bag/carton are the pack UNIT for produce, must stay food
    c = suggest_coding(_inv("select_fresh", [_line("LIMES TRAY"), _line("ONION BROWN BAG"),
                                             _line("MUSHROOM BUTTON CARTON")]))
    assert all(l.account_code == FOOD for l in c.lines)


def test_unknown_supplier_guessed_from_name():
    assert suggest_coding(_inv("", [_line("PALE ALE CTN")], name="Grifter Brewing Co")).lines[0].account_code == BEVERAGE
    assert suggest_coding(_inv("", [_line("CHICKEN BREAST")], name="M & J Chickens")).lines[0].account_code == FOOD


def test_unknown_supplier_no_signal_falls_back():
    assert suggest_coding(_inv("", [_line("MISC ITEM")], name="Something Obscure Co")).lines[0].account_code == OTHER_COGS


def test_gst_reconciliation_line_is_uncoded():
    assert suggest_coding(_inv("fresh_fruit_team", [_line("GST", LineClass.EXTRA)])).lines[0].account_code is None


def test_venue_maps_to_category_and_option():
    # Harry Gatos food -> Harry Gatos category / Kitchen (not Stowaway/Harry Gatos)
    hg = suggest_coding(_inv("select_fresh", [_line("X")], venue=Venue.HARRY_GATOS))
    assert (hg.tracking_category, hg.tracking_option) == ("Harry Gatos", "Kitchen")
    # Marilyna's -> Stowaway category / Marilyna's Pizza
    ma = suggest_coding(_inv("gulli", [_line("X")], venue=Venue.MARILYNAS))
    assert (ma.tracking_category, ma.tracking_option) == ("Stowaway", "Marilyna's Pizza")
    # Stowaway food -> Stowaway/Kitchen, Stowaway beverage -> Stowaway/Bar
    assert suggest_coding(_inv("select_fresh", [_line("X")], venue=Venue.STOWAWAY)).tracking_option == "Kitchen"
    assert suggest_coding(_inv("ilg", [_line("X")], venue=Venue.STOWAWAY)).tracking_option == "Bar"
