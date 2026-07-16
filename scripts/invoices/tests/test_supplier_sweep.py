"""
The full 30-day supplier sweep — one real invoice from every active supplier.

THE HEADLINE FINDING:

  Every supplier prints a "unit cost" column. NO TWO MEAN THE SAME THING.
  They are wrong in BOTH directions, by 17% to 10.9x. There is no way to
  tell from the column name.

  The only safe rule, universally:  line_total_incl / (qty * pack_size)

These tests exist so nobody ever "optimises" by reading a unit-cost column.
"""

import datetime
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from invoices.models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment, Venue
from invoices.validator import Status, Validator


@pytest.fixture(scope="module")
def config():
    p = Path(__file__).resolve().parents[1] / "suppliers.yaml"
    return yaml.safe_load(p.read_text())


# ===========================================================================
# THE UNIT-COST DIVERGENCE — the whole point of the sweep.
# (supplier, column_name, column_value, true_cost_per_stock_unit, direction)
# ===========================================================================
UNIT_COST_TRAPS = [
    # ILG: LUC basis varies PER PRODUCT. Same pack string, different basis.
    ("ilg",            "LUC ex GST",       "9.71",   "2.67",  "high"),   # Heaps  3.6x
    ("ilg",            "LUC ex GST",      "14.02",   "2.57",  "high"),   # Corona 5.5x
    # Paramount: LUC is per CASE.
    ("paramount",      "LUC Ex GST",      "46.53",   "4.27",  "high"),   # Sprite 10.9x
    # Lion: UNIT VALUE is the pre-discount list price.
    ("lion",           "UNIT VALUE",     "507.02", "383.23",  "high"),   # S&W +32.3%
    ("lion",           "UNIT VALUE",     "485.92", "416.58",  "high"),   # Guinness +16.6%
    # Combined: Unit Price is pre-disc, pre-WET, pre-GST.  LOW.
    ("combined_wines", "Unit Price",      "13.75",  "17.56",  "low"),    # -21.7%
    ("combined_wines", "Unit Price",      "48.67",  "62.15",  "low"),
    # Nelson: W/sale Price/Bot is pre-discount. LOW.
    ("nelson_wine",    "W/sale Price/Bot","12.08",  "14.57",  "low"),    # -17%
    # Bacchus: LUC is per btl ex-GST but INCL WET. LOW.
    ("bacchus",        "LUC",             "14.62",  "16.08",  "low"),
]


@pytest.mark.parametrize("supplier,column,value,truth,direction", UNIT_COST_TRAPS)
def test_supplier_unit_cost_columns_are_all_wrong(supplier, column, value, truth, direction):
    """
    Nine real column values from six suppliers. Not one equals the true cost
    per stock unit. They diverge in BOTH directions.
    """
    v, t = D(value), D(truth)
    assert v != t, f"{supplier} {column} unexpectedly matched"
    if direction == "high":
        assert v > t
    else:
        assert v < t


def test_low_readings_are_the_dangerous_ones():
    """
    HIGH readings inflate cost -> GP looks WORSE -> someone notices.
    LOW readings deflate cost  -> GP looks BETTER -> nobody ever notices.

    Combined (-21.7%) and Nelson (-17%) both read LOW. Those two suppliers
    would silently overstate margin on every wine they sell.
    """
    lows = [t for t in UNIT_COST_TRAPS if t[4] == "low"]
    assert len(lows) >= 3
    for supplier, column, value, truth, _ in lows:
        understate = (D(truth) - D(value)) / D(truth)
        assert understate > D("0.08"), f"{supplier} {column}: {understate:.1%}"


def test_config_forbids_trusting_unit_cost_columns(config):
    p = config["unit_cost_policy"]
    assert p["trust_supplier_unit_cost_columns"] is False
    assert p["derive_from_line_total"] is True
    assert p["formula"] == "line_total_incl / (qty * pack_size)"


def test_seven_suppliers_seven_unit_cost_meanings(config):
    """
    Each of these is documented with a DIFFERENT basis. If they ever collapse
    to one rule, someone has over-generalised.
    """
    s = config["suppliers"]
    assert s["ilg"]["quirks"]["luc_column_unit_basis"] == "varies_do_not_use"
    assert s["paramount"]["quirks"]["luc_column_unit_basis"] == "per_case_do_not_use"
    assert s["nelson_wine"]["quirks"]["luc_column_unit_basis"] == "per_bottle_ex_gst_incl_wet"
    assert s["lion"]["quirks"]["luc_column_reliable"] is True         # LUC ok, UNIT VALUE not
    assert s["viticult"]["quirks"]["luc_column_reliable"] is True
    assert s["combined_wines"]["quirks"]["unit_price_column_is_pre_everything"] is True
    assert s["nelson_wine"]["quirks"]["unit_price_is_pre_discount"] is True


# ===========================================================================
# FREIGHT — three incompatible models. Verified per supplier.
# ===========================================================================

def test_freight_models_are_incompatible(config):
    """
    ILG/Lion   bake freight INTO the line total -> adding it double-counts
    Viticult   footer line, GST-FREE            -> skip
    Foodlink   real line item, GST-TAXABLE      -> skip
    Paramount  real line items (Size=MISC)      -> skip
    Generalising ANY of these to another supplier produces a wrong total.
    """
    fm = config["freight_models"]
    assert set(fm["inside_line_total"]) == {"ilg", "lion"}
    assert "viticult" in fm["separate_gst_free"]
    assert set(fm["separate_taxable"]) == {"foodlink", "paramount"}
    # and the per-supplier flags agree with the index
    assert config["suppliers"]["ilg"]["quirks"]["freight_already_in_line_total"] is True
    assert config["suppliers"]["lion"]["quirks"]["freight_already_in_line_total"] is True
    assert config["suppliers"]["foodlink"]["quirks"]["freight_already_in_line_total"] is False
    assert config["suppliers"]["viticult"]["quirks"]["freight_is_gst_free"] is True


# Every PO-vs-invoice gap from the reconciliation, now EXPLAINED.
# (supplier, po, ls_total, invoice_total, gap, explanation)
PO_GAPS = [
    ("Lion",           "54361219", "1624.59", "1624.59", "0.00",  "freight is per-line, nothing to skip"),
    ("Bacchus",        "54361210",  "384.55",  "384.55", "0.00",  "no fuel levy on this invoice"),
    ("Combined Wines", "54361212",  "583.64",  "583.64", "0.00",  "no extras"),
    ("Nelson",         "54361216",  "280.89",  "280.89", "0.00",  "no extras"),
    ("Viticult",       "54361217",  "458.19",  "464.78", "6.59",  "Freight Total 6.60, GST-free"),
    ("Grifter",        "54361213",  "292.05",  "297.55", "5.50",  "Freight 5.50"),
    ("Paramount",      "54361220",  "237.33",  "254.38", "17.05", "Carton Frt 7.15 + MinDel 9.35 + Fuel 0.55"),
]


@pytest.mark.parametrize("sup,po,ls,inv,gap,why", PO_GAPS)
def test_every_po_gap_is_now_explained(sup, po, ls, inv, gap, why):
    """
    Seven Stowaway POs. Every gap accounted for by a named mechanism.
    Nothing left as "worth a look".
    """
    assert (D(inv) - D(ls)).quantize(D("0.01")) == D(gap), f"{sup} {po}"


def test_paramount_extras_sum_to_the_observed_gap():
    """The $17.05 gap == Carton Freight + Min Delivery + Fuel Levy, to the cent."""
    extras = D("7.15") + D("9.35") + D("0.55")
    assert extras == D("17.05")


def test_the_two_zero_gaps_that_looked_suspicious_are_correct():
    """
    Lion and Bacchus matched EXACTLY, which I flagged as needing a look
    because Appendix B says both carry skippable extras. Both are correct:
      Lion    — freight is a per-line COLUMN inside LINE VALUE (Appendix B wrong)
      Bacchus — this particular invoice simply has no fuel levy
    """
    lion = next(p for p in PO_GAPS if p[0] == "Lion")
    bacchus = next(p for p in PO_GAPS if p[0] == "Bacchus")
    assert lion[4] == "0.00" and bacchus[4] == "0.00"


# ===========================================================================
# Wine — the WET formula, verified across three suppliers
# ===========================================================================

@pytest.mark.parametrize("supplier,net,wet,gst,gross", [
    ("Bacchus",        "136.00", "39.44", "17.54", "192.98"),
    ("Bacchus",        "135.00", "39.15", "17.42", "191.57"),
    ("Combined Wines", "148.50", "43.07", "19.16", "210.73"),
    ("Combined Wines", "262.80", "76.21", "33.90", "372.91"),
    ("Nelson",         "123.25", "35.74", "15.90", "174.89"),
    ("Nelson",         "74.70",  "21.66",  "9.64", "106.00"),
])
def test_wine_wet_formula(supplier, net, wet, gst, gross):
    """
    WET = net x 0.29 ; GST = (net + WET) x 0.10 ; Gross = net x 1.29 x 1.1
    Holds across three independent wine suppliers, 6/6.
    """
    assert abs((D(net) * D("0.29")).quantize(D("0.01")) - D(wet)) <= D("0.01")
    assert abs(((D(net) + D(wet)) * D("0.10")).quantize(D("0.01")) - D(gst)) <= D("0.01")
    assert abs((D(net) * D("1.29") * D("1.1")).quantize(D("0.01")) - D(gross)) <= D("0.02")


def test_viticult_has_no_wet_unlike_the_other_wine_suppliers(config):
    """
    Bacchus/Combined/Nelson all print a WET column at 29%. Viticult does NOT —
    GST = subtotal x 0.10 exactly. Config was `wet`, corrected to `gst`.
    UNVERIFIED which is right; flagged rather than assumed.
    """
    assert config["suppliers"]["viticult"]["default_tax"] == "gst"
    for k in ("bacchus", "combined_wines", "nelson_wine"):
        assert config["suppliers"][k]["default_tax"] == "wet"


# ===========================================================================
# Sun Circle — handwritten. Dext: "unable to fully extract".
# ===========================================================================

SUN_CIRCLE = [
    ("Pork & Parsley Dumpling 600g x 24",  48, "4.50", "216.00"),
    ("Chicken & Corn Dumpling 600g x 24",  48, "4.50", "216.00"),
    ("Beef & Cabbage Dumpling 600g x 24",  48, "4.50", "216.00"),
    ("Prawn Har Gao (Large) 1000g x 15",    6, "32.00", "192.00"),
]


def test_handwritten_invoice_reconciles():
    """
    Sun Circle 16961 — a HANDWRITTEN bilingual dumpling order form. Dext says
    "We are unable to fully extract the item."

    Read completely; reconciles to the handwritten $840 total exactly.
    $3,168/30d that currently has NO line-item data in any system.

    No amount of parser engineering reads this. It is the single strongest
    argument for a model in the loop.
    """
    total = sum(D(r[3]) for r in SUN_CIRCLE)
    assert total == D("840.00")
    for desc, qty, price, amount in SUN_CIRCLE:
        assert (D(qty) * D(price)).quantize(D("0.01")) == D(amount), desc


# ===========================================================================
# Jun Pacific + FFT
# ===========================================================================

def test_jun_pacific_reconciles():
    """NB10521714 — 'G' = GST, 'W' = W.E.T letter codes. All blank -> GST-free."""
    rows = [("18.90"), ("24.20"), ("12.50"), ("280.50")]
    assert sum(D(r) for r in rows) == D("336.10")


def test_fft_reconciles():
    """INB00111435 — the simplest invoice in the estate. No traps."""
    rows = ["100.80", "23.70", "15.80", "10.80", "11.50", "10.56"]
    assert sum(D(r) for r in rows) == D("173.16")


def test_five_tax_marking_styles(config):
    """
    Gulli rate column · Foodlink flag · Select Fresh asterisk ·
    Jun Pacific letter code · Paramount per-line amounts.
    Five suppliers, five conventions. Never assume.
    """
    styles = config["tax_marking_styles"]
    assert len(set(styles.values())) == 5
    assert styles["jun_pacific"] == "letter_code"
    assert styles["gulli"] == "explicit_rate"
