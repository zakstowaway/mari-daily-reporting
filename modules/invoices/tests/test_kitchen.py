"""
Kitchen suppliers — real invoices.

Kitchen is a comparable pile of money to liquor (Foodlink alone $819k vs ILG
$821k) and behaves differently in every way that matters: GST-free, per-line
UOM, fractional quantities, ordered-vs-shipped columns, multi-page.

Two invoices read, two silent traps found. Both locked here.
"""

import sys
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from modules.invoices.models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment, Venue
from modules.invoices.validator import Status, Validator


@pytest.fixture(scope="module")
def config():
    p = Path(__file__).resolve().parents[1] / "suppliers.yaml"
    return yaml.safe_load(p.read_text())


# ---------------------------------------------------------------------------
# Select Fresh Providores — inv 3084903, 15 Jul 2026, Harry Gatos, GST-free
# (code, description, order, supply, unit, price, total)
# ---------------------------------------------------------------------------
SELECT_FRESH = [
    ("CARK",    "CARROT KG",                    "0.50", "0.50", "KG",    "2.40",  "1.20"),
    ("ONIBK",   "ONION BROWN KG",               "1.00", "1.00", "KG",    "2.40",  "2.40"),
    ("CHOI",    "CHOI SUM BCH",                 "3.00", "3.00", "BUNCH", "2.20",  "6.60"),
    ("LEMK",    "LEMON KG",                     "1.00", "1.00", "KG",    "3.60",  "3.60"),
    ("HCHI",    "HERB CHIVES BCH",              "4.00", "4.00", "BUNCH", "2.30",  "9.20"),
    ("SHAL",    "SHALLOT BCH",                  "4.00", "4.00", "BUNCH", "3.00", "12.00"),
    ("TBUS100", "TOMATO BUSH POWDER 100GM",     "2.00", "2.00", "PKT",  "16.50", "33.00"),
    ("PGAR",    "PROCESS GARLIC PEELED KG",     "1.00", "1.00", "KG",    "5.80",  "5.80"),
    ("XSHRCAB", "CABBAGE GREEN SHREDDED",       "1.00", "1.00", "KG",    "4.20",  "4.20"),
    ("XCSIMIX", "CABBAGE RED/GRN SHRD MIX KG",  "1.00", "1.00", "KG",    "4.80",  "4.80"),
    ("XDCAR05", "CARROT DICED 5MM KG",          "2.00", "2.00", "KG",    "3.10",  "6.20"),
    ("XSHRCAR", "CARROT SHREDDED",              "1.00", "1.00", "KG",    "2.80",  "2.80"),
    ("PRPOTP",  "POTATO PEELED PROCESSING KG",  "1.00", "1.00", "KG",    "3.80",  "3.80"),
]
SF_TOTAL = D("95.60")


def test_select_fresh_lines_reconcile_exactly():
    got = sum(D(r[6]) for r in SELECT_FRESH)
    assert got == SF_TOTAL, f"{got} != {SF_TOTAL}"


@pytest.mark.parametrize("code,desc,order,supply,unit,price,total", SELECT_FRESH)
def test_select_fresh_line_arithmetic(code, desc, order, supply, unit, price, total):
    """supply x price = total. Holds for fractional quantities too."""
    assert (D(supply) * D(price)).quantize(D("0.01")) == D(total)


def test_select_fresh_has_fractional_quantities():
    """
    CARROT KG order 0.50 — half a kilo. Liquor never does this. Any integer
    assumption on qty breaks here. (Money is Decimal throughout; qty must be too.)
    """
    carrot = next(r for r in SELECT_FRESH if r[0] == "CARK")
    assert D(carrot[3]) == D("0.50")
    assert D(carrot[3]) != D(carrot[3]).to_integral_value()


def test_select_fresh_is_gst_free_and_passes(config):
    """Total $95.60, GST $0.00. Must NOT trip the GST checks."""
    lines = [
        InvoiceLine(description=r[1], qty=D(r[3]), unit_price_incl=D(r[5]),
                    line_total_incl=D(r[6]), line_class=LineClass.STOCK,
                    tax_treatment=TaxTreatment.GST_FREE, supplier_code=r[0],
                    raw_uom=r[4])
        for r in SELECT_FRESH
    ]
    inv = Invoice(supplier_key="select_fresh", supplier_name_raw="Select Fresh Providores",
                  invoice_ref="3084903", invoice_date=__import__("datetime").date(2026, 7, 15),
                  total_incl=SF_TOTAL, venue=Venue.HARRY_GATOS, gst_total=D("0.00"),
                  account_code="HARGAT", lines=lines)
    r = Validator(config).validate(inv)
    assert r.status == Status.PASS, r.report()
    assert "GST_MISMATCH" not in {f.code for f in r.findings}


# ---------------------------------------------------------------------------
# B&E Foods — inv 6969915, 16 Jul 2026, Stowaway, GST-free, page 1 of 2
# (code, description, ordered, shipped, uom, ship_doc, price, total)
# ---------------------------------------------------------------------------
BE_FOODS = [
    ("18484", "CANNED - ANCHOVY FILLETS IN OIL 690G(12) SELESTA",        "1.00", "1.00", "UNIT", "0.08 CTN", "18.00", "18.00"),
    ("12776", "CHICKEN BREAST (F) SLICE (STIR FRY) 5MM PREMIUM 5KG BAG", "5.00", "5.00", "KG",   "1.00 BAG", "12.20", "61.00"),
    ("19626", "SAUSAGE - MILD SPANISH CHORIZO 1KG (15) PENDLE",          "1.00", "1.00", "KG",   "0.07 CTN", "13.70", "13.70"),
    ("28087", "CHILLI - FLAKE / CRUSHED 1KG CSI",                        "1.00", "1.00", "BAG",  "0.10 CTN", "13.70", "13.70"),
    ("17723", "YOGHURT - GREEK 2KG PROCAL",                              "1.00", "1.00", "TUB",  "1.00",     "14.50", "14.50"),
    ("11605", "ANTIPASTO - CHARGRILLED EGGPLANT 2KG TUB (4) MEZZAT",     "1.00", "1.00", "UNIT", "0.25 CTN", "21.90", "21.90"),
]


@pytest.mark.parametrize("code,desc,ordered,shipped,uom,shipdoc,price,total", BE_FOODS)
def test_be_line_arithmetic(code, desc, ordered, shipped, uom, shipdoc, price, total):
    """shipped x price = total. Confirms LINE_ARITHMETIC works on B&E — 6/6."""
    assert (D(shipped) * D(price)).quantize(D("0.01")) == D(total)


def test_be_qty_is_weight_not_count_THE_TRAP():
    """
    THE B&E TRAP.

        CHICKEN BREAST ... 5KG BAG | Shipped 5.00 | UOM KG | Ship Doc 1.00 BAG
                                                             Line Total $61.00

    That is ONE 5kg bag. The 5.00 is KILOGRAMS, because the line's UOM is KG.

    Read it as a count -> 5 bags -> 25kg of chicken that was never delivered.
    5x stock, 5x COGS, silent, and it looks entirely normal on screen.

    The physical pack count lives in `Ship Doc + Unit UOM` ("1.00 BAG").
    """
    chicken = next(r for r in BE_FOODS if r[0] == "12776")
    _, desc, _, shipped, uom, shipdoc, price, total = chicken
    assert uom == "KG"
    assert D(shipped) == D("5")
    assert "5KG BAG" in desc
    assert shipdoc.startswith("1.00 BAG")     # ONE bag, not five
    # $12.20/kg x 5kg = $61.00 -> the invoice already did the multiplication
    assert (D(price) * D(shipped)).quantize(D("0.01")) == D(total)
    # the naive count reading would be 5x out
    naive = D(total) * D(shipped)
    assert naive == D("305.00")


def test_be_uom_varies_within_one_invoice(config):
    """UNIT, KG, BAG, TUB on a single document — no single qty reading exists."""
    uoms = {r[4] for r in BE_FOODS}
    assert uoms == {"UNIT", "KG", "BAG", "TUB"}
    assert config["suppliers"]["be_foods"]["quirks"]["uom_varies_per_line"] is True
    assert config["suppliers"]["be_foods"]["quirks"]["qty_is_in_uom_units"] is True


@pytest.mark.parametrize("desc,shipdoc,expected_per_ctn", [
    ("CANNED - ANCHOVY FILLETS IN OIL 690G(12) SELESTA", "0.08", 12),
    ("SAUSAGE - MILD SPANISH CHORIZO 1KG (15) PENDLE",   "0.07", 15),
    ("ANTIPASTO - CHARGRILLED EGGPLANT 2KG TUB (4) MEZZAT", "0.25", 4),
])
def test_ctn_count_pattern_matches_the_ship_doc_column(config, desc, shipdoc, expected_per_ctn):
    """
    CONFIRMS Appendix B: the (N) in the description IS units-per-carton.
    Cross-checked against B&E's own Ship Doc fraction.
    """
    import re
    pats = config["suppliers"]["be_foods"]["quirks"]["ctn_count_patterns"]
    found = None
    for p in pats:
        m = re.search(p, desc)
        if m:
            found = int(m.group(1)); break
    assert found == expected_per_ctn, f"{desc}: got {found}"
    # 1/N should round to the printed Ship Doc fraction
    assert round(1 / found, 2) == float(shipdoc)


def test_be_ordered_and_shipped_are_separate(config):
    """Receive SHIPPED, never ORDERED. Short-ships are explicit in kitchen."""
    q = config["suppliers"]["be_foods"]["quirks"]
    assert q["has_ordered_and_shipped_columns"] is True
    assert q["receive_column"] == "Shipped"


def test_be_line_total_is_authoritative_not_the_kg_multiply(config):
    """
    Guard against double-applying Appendix B's `KG_price x pack_weight`.
    The invoice already did it — $12.20/KG x 5kg = $61.00 = stated Line Total.
    """
    assert config["suppliers"]["be_foods"]["quirks"]["line_total_is_authoritative"] is True


# ---------------------------------------------------------------------------
# Foodlink — inv SI4467596, 16 Jul 2026, Stowaway. LARGEST kitchen supplier.
# (code, description, qty, uom, price_ex, taxable, amount_ex)
# ---------------------------------------------------------------------------
FOODLINK = [
    ("103272", "BARRAMUNDI FILLETS IMP 200/300 S/OFF 5KG (I) Trading", "1", "CTN",    "83.00", False, "83.00"),
    ("102689", "SQUID PINEAPPLE CUT IMP U5 5KG (I) Trading",           "1", "CTN",    "57.00", False, "57.00"),
    ("101112", "FLOUR TORTILLAS 12X63GM 10INCH Simson's Pantry",       "1", "CTN-6",  "33.00", False, "33.00"),
    ("100487", "CHEESE CAMEMBERT 125GM Rosenberg",                     "1", "CTN-12", "45.60", False, "45.60"),
    ("100831", "CORN CHIPS ROSITA TRI 6X500GM Mission",                "1", "CTN",    "43.00", True,  "43.00"),
    (None,     "Fuel Levy",                                            "1", None,      "3.00", True,   "3.00"),
]
FL_TOTAL_EX = D("264.60")
FL_GST = D("4.60")
FL_TOTAL_INCL = D("269.20")
# What Dext's GraphQL API actually returns for this invoice:
FL_DEXT_LINE_SUM = D("264.60")     # lineItems -> EX-GST
FL_DEXT_HEADER = D("269.20")       # totalAmount -> INCL-GST


def test_foodlink_footer_is_internally_consistent():
    ex = sum(D(r[6]) for r in FOODLINK)
    assert ex == FL_TOTAL_EX
    assert (ex + FL_GST) == FL_TOTAL_INCL


def test_foodlink_only_two_lines_are_taxable():
    """
    Mixed tax. Corn chips + Fuel Levy = 46.00 ex, x10% = $4.60 = stated GST.
    The other four are GST-free food. 4 lines where ex == incl, 1 where it
    doesn't — selective errors are harder to spot than total ones.
    """
    taxable = sum(D(r[6]) for r in FOODLINK if r[5])
    assert taxable == D("46.00")
    assert (taxable * D("0.10")).quantize(D("0.01")) == FL_GST
    assert len([r for r in FOODLINK if r[5]]) == 2


def test_dext_lineitems_do_NOT_reconcile_to_dext_header_THE_TRAP():
    """
    THE FOODLINK TRAP — and it's on Dext's BEST kitchen supplier (6/6 coverage).

    Dext's lineItems are the EX-GST figures. Dext's header totalAmount is the
    INCL-GST figure. Different tax bases. They can NEVER reconcile, and the
    "missing" $4.60 is simply the GST.

    Anyone diffing Dext's lines against Dext's header sees a $4.60 hole and
    goes looking for a dropped line that doesn't exist.
    """
    assert FL_DEXT_LINE_SUM == FL_TOTAL_EX          # lines are EX
    assert FL_DEXT_HEADER == FL_TOTAL_INCL          # header is INCL
    assert FL_DEXT_HEADER - FL_DEXT_LINE_SUM == FL_GST


def test_using_dext_amounts_directly_understates_taxable_lines_by_10pc():
    """
    Lightspeed needs GST-INCLUSIVE costs (Rule 2). Dext hands you EX-GST.
    Corn chips: Dext says $43.00; Lightspeed needs $47.30.
    GST-free lines are unaffected — which is exactly what makes it sneaky.
    """
    chips = next(r for r in FOODLINK if r[0] == "100831")
    dext_value = D(chips[6])
    correct_incl = (dext_value * D("1.1")).quantize(D("0.01"))
    assert dext_value == D("43.00")
    assert correct_incl == D("47.30")
    # a GST-free line is identical either way
    barra = next(r for r in FOODLINK if r[0] == "103272")
    assert D(barra[6]) == D("83.00")


def test_foodlink_config_flags_the_ex_gst_basis(config):
    q = config["suppliers"]["foodlink"]["quirks"]
    assert q["line_amounts_are_ex_gst"] is True
    assert q["dext_lineitems_are_ex_gst"] is True
    assert q["dext_header_is_incl_gst"] is True
    assert q["tax_is_per_line"] is True


def test_foodlink_fuel_levy_is_separate_unlike_ilg(config):
    """
    ILG rolls freight INTO the per-line TOT (adding it double-counts $44.63).
    Foodlink does NOT — its Fuel Levy is a real, separate, TAXABLE line.
    Opposite handling; do not generalise one supplier's freight behaviour.
    """
    fl = config["suppliers"]["foodlink"]["quirks"]
    ilg = config["suppliers"]["ilg"]["quirks"]
    assert fl["freight_already_in_line_total"] is False
    assert ilg["freight_already_in_line_total"] is True
    assert config["suppliers"]["foodlink"]["extras"][0]["taxable"] is True


def test_foodlink_converted_to_incl_gst_passes_the_validator(config):
    """End to end, once ex-GST is correctly converted."""
    import datetime
    lines = []
    for code, desc, qty, uom, price_ex, taxable, amount_ex in FOODLINK:
        incl = (D(amount_ex) * (D("1.1") if taxable else D("1"))).quantize(D("0.01"))
        lines.append(InvoiceLine(
            description=desc, qty=D(qty), unit_price_incl=incl, line_total_incl=incl,
            line_class=LineClass.EXTRA if desc == "Fuel Levy" else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if taxable else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.PER_UNIT, supplier_code=code, raw_uom=uom))
    inv = Invoice(supplier_key="foodlink", supplier_name_raw="Foodlink Australia",
                  invoice_ref="SI4467596", invoice_date=datetime.date(2026, 7, 16),
                  total_incl=FL_TOTAL_INCL, venue=Venue.STOWAWAY,
                  gst_total=FL_GST, lines=lines)
    r = Validator(config).validate(inv)
    assert r.status == Status.PASS, r.report()
    # the Fuel Levy is excluded from the receive, so LS should be $3.30 under
    assert r.extras_total == D("3.30")
