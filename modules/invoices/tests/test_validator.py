"""
Regression suite — the seed of the golden dataset.

Every case here is built from a REAL worked example in the skill's Appendix B,
or from a real failure mode the skill documents having been burned by.

The negative tests matter more than the positive ones. A validator that passes
good invoices is easy. A validator that CATCHES a case total in a per-unit field
is the only reason this pipeline is safer than Dext.

Run: python3 -m pytest tests/ -v
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from modules.invoices.models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment, Venue
from modules.invoices.validator import Severity, Status, Validator

D = Decimal


@pytest.fixture(scope="module")
def config():
    p = Path(__file__).resolve().parents[1] / "suppliers.yaml"
    return yaml.safe_load(p.read_text())


@pytest.fixture(scope="module")
def v(config):
    return Validator(config)


def codes(result):
    return {f.code for f in result.findings}


# ---------------------------------------------------------------------------
# Positive: clean invoices must pass
# ---------------------------------------------------------------------------

def test_bacchus_with_fuel_levy_passes_and_reports_expected_gap(v):
    """
    Appendix B: Bacchus invoices carry a ~$4.95 Fuel Levy that is NEVER entered
    on the LS receive. The receive total is therefore expected to be $4.95 under
    the invoice total. That gap is the green light, not a problem.
    """
    inv = Invoice(
        supplier_key="bacchus",
        supplier_name_raw="Bacchus Wine Merchants (SB)",
        invoice_ref="INV-88213",
        invoice_date=date(2026, 7, 10),
        total_incl=D("268.95"),
        venue=Venue.STOWAWAY,
        gst_total=D("24.45"),
        lines=[
            InvoiceLine(
                description="Mother's Milk Shiraz - Bottle",
                qty=D("12"), unit_price_incl=D("22.00"),
                line_total_incl=D("264.00"),
                line_class=LineClass.STOCK,
                tax_treatment=TaxTreatment.WET,
                cost_basis=CostBasis.PER_BOTTLE,
            ),
            InvoiceLine(
                description="Fuel Levy",
                qty=D("1"), unit_price_incl=D("4.95"),
                line_total_incl=D("4.95"),
                line_class=LineClass.EXTRA,
            ),
        ],
    )
    r = v.validate(inv)
    assert r.status == Status.PASS, r.report()
    assert "EXTRAS_EXCLUDED" in codes(r)
    # The receive must target stock only.
    assert r.expected_ls_receive_total == D("264.00")
    assert r.extras_total == D("4.95")


def test_be_foods_gst_free_passes(v):
    """
    B&E is basic food — GST-free. GST of $0 against an $81.33 total is correct
    and must NOT trip the GST check.

    Both lines use the documented per-KG / per-CTN conversions:
      Beef Brisket Diced [5kg]  $13.50/KG x 5kg     = $67.50
      Sweet Potato Chips [1.5KG] $83.00/CTN "1.5KGx6" = $13.83
    """
    inv = Invoice(
        supplier_key="be_foods",
        supplier_name_raw="B&E Foods",
        invoice_ref="BE-40021",
        invoice_date=date(2026, 7, 12),
        total_incl=D("81.33"),
        venue=Venue.HARRY_GATOS,
        gst_total=D("0.00"),
        lines=[
            InvoiceLine(
                description="Beef Brisket Diced [5kg]",
                qty=D("1"), unit_price_incl=D("67.50"),
                line_total_incl=D("67.50"),
                line_class=LineClass.STOCK,
                tax_treatment=TaxTreatment.GST_FREE,
                cost_basis=CostBasis.PER_UNIT,
                raw_uom="KG",
            ),
            InvoiceLine(
                description="Sweet Potato Chips [1.5KG]",
                qty=D("1"), unit_price_incl=D("13.83"),
                line_total_incl=D("13.83"),
                line_class=LineClass.STOCK,
                tax_treatment=TaxTreatment.GST_FREE,
                cost_basis=CostBasis.PER_UNIT,
                raw_uom="CTN",
            ),
        ],
    )
    r = v.validate(inv)
    assert r.status == Status.PASS, r.report()
    assert "GST_MISMATCH" not in codes(r)


def test_crate_line_uses_per_unit_price_times_pack_size(v):
    """
    Appendix B / Rule 4: a "Crates of 24" product expects the PER-CAN price.
    Lightspeed multiplies by pack size itself.
    Heaps Normal: 1 case, 24 tins, $2.67/tin -> $64.08 line total.
    """
    inv = _lion_invoice(unit=D("2.67"), pack=24, total=D("64.08"))
    r = v.validate(inv)
    assert r.status == Status.PASS, r.report()


# ---------------------------------------------------------------------------
# Negative: the errors that actually cost money
# ---------------------------------------------------------------------------

def test_case_total_in_per_unit_field_is_caught_by_sanity_bounds(v):
    """
    THE test. This is the silent killer.

    If the extractor puts the CASE total ($64.08) in the per-can field and drops
    the pack size, the arithmetic reconciles perfectly (1 x 64.08 = 64.08) and
    the invoice total is right. Nothing about the maths is wrong. The number is
    still 24x too high for Lightspeed and would corrupt Average Cost Price, GP,
    and every recipe using it — for 30 days, silently.

    Only plausibility bounds catch this. Without this check the whole pipeline
    is no safer than Dext.
    """
    inv = _lion_invoice(unit=D("64.08"), pack=None, total=D("64.08"))
    r = v.validate(inv)
    assert r.status == Status.REVIEW, r.report()
    assert "SANITY_BOUNDS" in codes(r)


def test_dropped_line_fails_reconciliation(v):
    """
    Extractor misses a line -> sum(lines) != invoice total.
    This is the check Dext does not do for us, and the reason Rule 0 exists.
    """
    inv = Invoice(
        supplier_key="bacchus",
        supplier_name_raw="Bacchus Wine Merchants (SB)",
        invoice_ref="INV-88213",
        invoice_date=date(2026, 7, 10),
        total_incl=D("268.95"),   # total says 268.95...
        venue=Venue.STOWAWAY,
        lines=[
            InvoiceLine(  # ...but only 264.00 of lines survived extraction
                description="Mother's Milk Shiraz - Bottle",
                qty=D("12"), unit_price_incl=D("22.00"),
                line_total_incl=D("264.00"),
                line_class=LineClass.STOCK,
                cost_basis=CostBasis.PER_BOTTLE,
            ),
        ],
    )
    r = v.validate(inv)
    assert r.status == Status.REVIEW, r.report()
    assert "INVOICE_RECONCILE" in codes(r)


def test_line_arithmetic_mismatch_is_caught(v):
    """qty x unit != stated total. A misread digit."""
    inv = _lion_invoice(unit=D("2.67"), pack=24, total=D("54.08"))  # should be 64.08
    r = v.validate(inv)
    assert r.status == Status.REVIEW, r.report()
    assert "LINE_ARITHMETIC" in codes(r)


def test_impossible_gst_is_caught(v):
    """GST can never exceed 1/11 of a GST-inclusive total."""
    inv = _lion_invoice(unit=D("2.67"), pack=24, total=D("64.08"))
    inv.gst_total = D("12.00")  # ceiling is 5.83
    r = v.validate(inv)
    assert r.status == Status.REVIEW, r.report()
    assert "GST_IMPOSSIBLE" in codes(r)


def test_unclassified_line_forces_review_rather_than_assuming_stock(v):
    """
    Appendix B, HG cash receipts: "MISC SPECIAL" with no description.
    The skill says flag and ask. The validator must not quietly call it stock.
    """
    inv = _lion_invoice(unit=D("2.67"), pack=24, total=D("64.08"))
    inv.lines.append(InvoiceLine(
        description="MISC SPECIAL",
        qty=D("1"), unit_price_incl=D("15.00"),
        line_total_incl=D("15.00"),
        line_class=LineClass.UNKNOWN,
    ))
    inv.total_incl = D("79.08")
    r = v.validate(inv)
    assert r.status == Status.REVIEW, r.report()
    assert "LINE_UNCLASSIFIED" in codes(r)


def test_unknown_venue_blocks_the_write(v):
    """
    Appendix A: ProductIDs are venue-specific. A cost update built from
    Stowaway's export silently does nothing in Harry Gatos. Writing to the
    wrong namespace is worse than not writing.
    """
    inv = _lion_invoice(unit=D("2.67"), pack=24, total=D("64.08"))
    inv.venue = Venue.UNKNOWN
    r = v.validate(inv)
    assert r.status == Status.REVIEW, r.report()
    assert "NO_VENUE" in codes(r)


# ---------------------------------------------------------------------------
# Auto-classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("desc", [
    "Fuel Levy",
    "SYD Metro Delivery Fee",
    "Temporary Frt Surcharge Metro",
    "FRT TEMP MET",
    "FREIGHT",
    "Freight Total",
    "Carton Freight",
    "Minimum Delivery Top-Up Charge",
    "Temporary Fuel Levy Charge",
    "VISA/Mastercard surcharge",
])
def test_every_documented_extras_line_is_recognised(v, desc):
    """
    The full extras table from Appendix B, across Bacchus / Philter / Y&R /
    Lion / Grifter / Paramount / Viticult. Each must classify as EXTRA so it is
    skipped on the receive and logged as delivery_fee_rolled_in.
    """
    line = InvoiceLine(description=desc, qty=D("1"),
                       line_total_incl=D("5.00"), line_class=LineClass.UNKNOWN)
    assert v.classify_line(line) == LineClass.EXTRA


@pytest.mark.parametrize("desc", ["WOS", "Waiting on Stock", "Back Order"])
def test_wos_lines_are_recognised(v, desc):
    line = InvoiceLine(description=desc, qty=D("0"),
                       line_total_incl=D("0.00"), line_class=LineClass.UNKNOWN)
    assert v.classify_line(line) == LineClass.WOS


@pytest.mark.parametrize("desc", [
    "Frenchman's Freight Pale Ale [Keg]",   # contains "Freight"
    "Delivery Dave's Pale Ale",             # starts with "Delivery"
    "Special Delivery Stout 4pk",           # contains "Delivery"
    "Fuel Levy Brewing Co IPA",             # contains "Fuel Levy"
    "Surcharge Session Ale",                # contains "Surcharge"
    "Freight Train Porter 50L KEG",         # contains "Freight"
])
def test_real_products_are_NOT_swallowed_by_extras_patterns(v, desc):
    """
    THE GREEDINESS GUARD — and a lesson in vacuous tests.

    The original version of this test passed `line_class=STOCK`. But
    classify_line() returns early on anything already classified, so it never
    exercised the patterns at all. It passed while testing NOTHING.

    Meanwhile the real bug was live: `(?i)freight` is an unanchored substring
    match, so "Frenchman's Freight Pale Ale [Keg]" classified as EXTRA — a real
    product SILENTLY DROPPED from the receive.

    This version passes UNKNOWN, which is what the extractor actually emits,
    so the patterns are genuinely exercised.

    These must resolve to UNKNOWN (-> validator ERROR -> human review), never
    EXTRA. Fail toward review, never toward deleting stock.
    """
    line = InvoiceLine(description=desc, qty=D("1"),
                       line_total_incl=D("320.00"),
                       line_class=LineClass.UNKNOWN)
    got = v.classify_line(line)
    assert got != LineClass.EXTRA, f"{desc!r} was swallowed as an extras line"
    assert got == LineClass.UNKNOWN


def test_classify_line_returns_early_on_already_classified_lines(v):
    """
    Documents the early-return that made the old guard test vacuous. Kept so
    the behaviour is explicit rather than a trap for the next person.
    """
    line = InvoiceLine(description="Fuel Levy", qty=D("1"),
                       line_total_incl=D("4.95"), line_class=LineClass.STOCK)
    # Extractor said STOCK, so classify_line does NOT re-examine it.
    assert v.classify_line(line) == LineClass.STOCK


# ---------------------------------------------------------------------------
# Supplier rule arithmetic (formula-level, no invoice needed)
# ---------------------------------------------------------------------------

def test_grifter_discount_formula(config):
    """
    Appendix B worked example: $35.00/keg, 10% discount
      -> $31.50 ex GST -> $34.65 incl GST.
    """
    unit = D("35.00")
    incl = (unit * D("0.9") * D("1.1")).quantize(D("0.01"))
    assert incl == D("34.65")


def test_be_per_kg_conversion(config):
    """cost = invoice_KG_price x pack_weight_kg"""
    assert (D("13.50") * D("5")).quantize(D("0.01")) == D("67.50")   # Beef Brisket [5kg]
    assert (D("9.90") * D("5")).quantize(D("0.01")) == D("49.50")    # Chicken Breast [5KG]
    # Bracket "[kg]" with no number = tracked per kg, no multiplication.
    assert D("22.70") == D("22.70")                                   # Spanish Chorizo [1kg]


def test_be_per_ctn_conversion(config):
    """Sweet Potato Chips [1.5KG] $83.00/CTN, "1.5KGx6" -> /6 -> $13.83"""
    assert (D("83.00") / D("6")).quantize(D("0.01")) == D("13.83")


def test_combined_wines_per_bottle_from_total(config):
    """TOTAL already nets discount + WET + GST. Per bottle = TOTAL / 12."""
    assert (D("264.00") / D("12")).quantize(D("0.01")) == D("22.00")


def test_ilg_repack_notation_is_configured(config):
    """
    ILG qty "0/N" means N units as a repack, not zero.

    CORRECTED against real invoice 03729959, which carries BOTH "0/1"
    (Antica Formula, Buffalo Trace) and "0/2" (Sailor Jerry). Appendix B only
    ever documents "0/1", which reads as "a repack is always one bottle" —
    it isn't. This test previously asserted repack_qty == 1 and was wrong.
    """
    q = config["suppliers"]["ilg"]["quirks"]
    assert q["repack_notation"] == "0/N"


def test_ilg_luc_column_is_marked_as_unusable(config):
    """
    Guard against anyone "helpfully" wiring LUC back in as the unit price.
    Its unit basis varies per product and is not derivable from the invoice.
    See tests/test_ilg_03729959.py::test_luc_unit_basis_is_NOT_inferable_...
    """
    q = config["suppliers"]["ilg"]["quirks"]
    assert q["luc_column_unit_basis"] == "varies_do_not_use"
    assert q["unit_price_source"] == "TOT incl GST / (qty * pack_size)"


def test_iwi_is_findable_under_its_dext_parent_name(config):
    """
    Dext extracts IWI invoices under the parent entity "Inalca F&B Australia".
    Searching "Italian Wine Importers" in Dext finds nothing.
    """
    assert "Inalca F&B Australia" in config["suppliers"]["italian_wine_importers"]["aliases"]


def test_lo_fi_hyphenated_alias_present(config):
    """
    The hyphen matters — ~43 invoices exist under "Lo-Fi Wines". A previous
    skill version wrongly recorded this supplier as sparse.
    """
    assert "Lo-Fi Wines" in config["suppliers"]["lo_fi"]["aliases"]


def test_philter_keg_is_30l_not_50l(config):
    """Pours per keg drive GP. 30L vs 50L is a 40% error in cost per schooner."""
    assert config["suppliers"]["philter"]["quirks"]["keg_size_litres"] == 30


# ---------------------------------------------------------------------------

def _lion_invoice(unit, pack, total):
    return Invoice(
        supplier_key="lion",
        supplier_name_raw="Lion Beer Spirits & Wine",
        invoice_ref="LION-7741",
        invoice_date=date(2026, 7, 14),
        total_incl=total,
        venue=Venue.STOWAWAY,
        lines=[
            InvoiceLine(
                description="Heaps Normal Another Lager 24x355mL",
                qty=D("1"), unit_price_incl=unit, pack_size=pack,
                line_total_incl=total,
                line_class=LineClass.STOCK,
                tax_treatment=TaxTreatment.GST,
                cost_basis=CostBasis.PER_CAN,
            ),
        ],
    )
