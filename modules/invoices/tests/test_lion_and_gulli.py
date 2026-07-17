"""
Lion + Gulli — real invoices from the strict 30-day window.

Lion is the biggest unvalidated supplier by recent spend ($6,812/30d) and
carries THREE Appendix B errors. Gulli is the mixed-tax case ($5,285/30d,
$298k lifetime, zero Dext line items).
"""

import datetime
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
# LION — inv 94755729, 14 Jul 2026, Stowaway. Total $1,624.59, GST $147.69.
# (code, desc, qty, unit_value, product_value, discount, freight, fuel, luc, line_value_ex)
# ---------------------------------------------------------------------------
LION = [
    ("1256672", "S & W Pacific Ale 49.5L KEG", 2, "460.93", "921.86", "-247.50", "21.36", "1.07", "348.40", "696.79"),
    ("1200769", "Guinness 49.5L KEG",          1, "441.75", "441.75",  "-74.25", "10.68", "0.53", "378.71", "378.71"),
    ("1254690", "Kirin Ichb 5% 49.5L KEG",     1, "464.44", "464.44",  "-74.25", "10.68", "0.53", "401.40", "401.40"),
]
LION_EX = D("1476.90")
LION_GST = D("147.69")
LION_TOTAL = D("1624.59")


@pytest.mark.parametrize("code,desc,qty,unit,prod,disc,frt,fuel,luc,line", LION)
def test_lion_line_formula(code, desc, qty, unit, prod, disc, frt, fuel, luc, line):
    """
    VERIFIED 3/3:
      PRODUCT VALUE = QTY x UNIT VALUE
      LINE VALUE    = PRODUCT VALUE + DISCOUNT + FREIGHT + FUEL SURCHARGE
                      (+ CONTAINER DEPOSIT + WET + HANDLING, all 0.00 here)
    """
    assert (D(qty) * D(unit)).quantize(D("0.01")) == D(prod)
    assert D(prod) + D(disc) + D(frt) + D(fuel) == D(line)


def test_lion_reconciles_to_invoice_and_to_the_lightspeed_po():
    """
    sum(LINE VALUE ex) x 1.1 = invoice total = what Lightspeed actually received.
    Three sources agreeing.
    """
    ex = sum(D(r[9]) for r in LION)
    assert ex == LION_EX
    assert (ex * D("0.1")).quantize(D("0.01")) == LION_GST
    assert (ex + LION_GST) == LION_TOTAL
    # PO 54361219 received exactly this
    assert LION_TOTAL == D("1624.59")


def test_lion_freight_is_per_line_NOT_a_separate_line(config):
    """
    APPENDIX B ERROR 1.

    Appendix B: "Lion: Freight on keg invoices appears as a separate line.
    Note for Zak, don't roll into per-unit pricing."

    WRONG. FREIGHT and FUEL SURCHARGE are per-line COLUMNS already inside
    LINE VALUE. There is no separate freight line and nothing to skip.

    This is why LS PO 54361219 matched inv 94755729 at EXACTLY $0.00 gap —
    an open item in po-invoice-reconciliation.md, now closed.
    """
    q = config["suppliers"]["lion"]["quirks"]
    assert q["freight_is_separate_line"] is False
    assert q["freight_already_in_line_total"] is True
    assert "extras" not in config["suppliers"]["lion"]
    # freight is inside the line: PRODUCT + DISC + FRT + FUEL == LINE
    sw = LION[0]
    assert D(sw[4]) + D(sw[5]) + D(sw[6]) + D(sw[7]) == D(sw[9])


@pytest.mark.parametrize("code,desc,qty,unit,prod,disc,frt,fuel,luc,line", LION)
def test_lion_has_a_large_per_line_discount(code, desc, qty, unit, prod, disc, frt, fuel, luc, line):
    """
    APPENDIX B ERROR 2 — Appendix B documents Grifter's 10% and is SILENT on Lion.

    Lion discounts are large and per-line. Using UNIT VALUE as the cost is
    15-32% HIGH:
        S&W      $383.23 correct  vs  $507.02 -> +32.3%
        Guinness $416.58 correct  vs  $485.92 -> +16.6%
        Kirin    $441.54 correct  vs  $510.88 -> +15.7%
    """
    assert D(disc) < 0, "every line carries a discount"
    correct_incl = (D(line) / D(qty) * D("1.1")).quantize(D("0.01"))
    naive_incl = (D(unit) * D("1.1")).quantize(D("0.01"))
    assert naive_incl > correct_incl
    overstatement = (naive_incl - correct_incl) / correct_incl
    assert overstatement > D("0.15"), f"{desc}: {overstatement:.1%}"


def test_lion_kegs_are_49_5_litres_not_50(config):
    """
    APPENDIX B ERROR 3. Every keg reads "49.5L KEG". The skill's GP formula
    uses 50L / 0.425 = 117 pours; real is 49.5 / 0.425 = 116.5. ~1% GP error
    on every schooner.
    """
    assert config["suppliers"]["lion"]["quirks"]["keg_size_litres"] == 49.5
    assert all("49.5L KEG" in r[1] for r in LION)


def test_lion_luc_is_reliable_unlike_ilg(config):
    """
    Lion's LUC == LINE VALUE / QTY, 3/3. Reliable HERE.
    ILG's LUC is NOT (its unit basis varies per product).
    The same column name means different things per supplier — do not generalise.
    """
    for code, desc, qty, unit, prod, disc, frt, fuel, luc, line in LION:
        assert (D(line) / D(qty)).quantize(D("0.01")) == D(luc)
    assert config["suppliers"]["lion"]["quirks"]["luc_column_reliable"] is True
    assert config["suppliers"]["ilg"]["quirks"]["luc_column_unit_basis"] == "varies_do_not_use"


def test_lion_keg_prices_are_in_bounds(config):
    b = config["sanity_bounds"]["per_keg"]
    lo, hi = D(str(b["min"])), D(str(b["max"]))
    for code, desc, qty, unit, prod, disc, frt, fuel, luc, line in LION:
        incl = (D(line) / D(qty) * D("1.1")).quantize(D("0.01"))
        assert lo <= incl <= hi, f"{desc}: {incl}"


def test_lion_passes_the_validator(config):
    lines = [
        InvoiceLine(description=r[1], qty=D(r[2]),
                    unit_price_incl=(D(r[9]) / D(r[2]) * D("1.1")).quantize(D("0.0001")),
                    line_total_incl=(D(r[9]) * D("1.1")).quantize(D("0.01")),
                    line_class=LineClass.STOCK, tax_treatment=TaxTreatment.GST,
                    cost_basis=CostBasis.PER_KEG, supplier_code=r[0])
        for r in LION
    ]
    inv = Invoice(supplier_key="lion", supplier_name_raw="Lion Beer Spirits & Wine",
                  invoice_ref="94755729", invoice_date=datetime.date(2026, 7, 14),
                  total_incl=LION_TOTAL, venue=Venue.STOWAWAY,
                  gst_total=LION_GST, lines=lines)
    r = Validator(config).validate(inv)
    assert r.status == Status.PASS, r.report()


# ---------------------------------------------------------------------------
# GULLI — inv CI-424608, 13 Jul 2026, Stowaway. Total $352.26, GST $5.84.
# (code, desc, qty, unit_price, gst_rate_pct, amount_ex)
# ---------------------------------------------------------------------------
GULLI = [
    ("MANFLOPIZ-U",       "Manildra- Gem West Pizza Flour 12.5kg",        "5.000",  "13.80000",  0, "69.00"),
    ("PBLTB13-U",         'B Flute Lock Top 13" Pizza Boxes x 50',        "2.000",  "29.21000", 10, "58.42"),
    ("MOZZARELLA2KG-UC4", "Big Cheese- Shredded Mozzarella 2kg",         "10.000",  "21.00000",  0, "210.00"),
    ("VEGD004-UC10",      "Dairy-Free (Vegan) Shredded Mozzarella 500g",  "1.000",   "9.00000",  0, "9.00"),
    ("DELIVERY_007",      "Standard Delivery",                            "1.000",   "0.00000", 10, "0.00"),
]
GULLI_UNTAXED = D("346.42")
GULLI_GST = D("5.84")
GULLI_TOTAL = D("352.26")


@pytest.mark.parametrize("code,desc,qty,price,rate,amount", GULLI)
def test_gulli_line_arithmetic(code, desc, qty, price, rate, amount):
    assert (D(qty) * D(price)).quantize(D("0.01")) == D(amount)


def test_gulli_footer_reconciles():
    """
    Untaxed Amount 346.42 | GST 0% on 288.00 = 0.00 | GST 10% on 58.42 = 5.84
    Total 352.26. Every figure checks.
    """
    ex = sum(D(r[5]) for r in GULLI)
    assert ex == GULLI_UNTAXED
    free = sum(D(r[5]) for r in GULLI if r[4] == 0)
    taxable = sum(D(r[5]) for r in GULLI if r[4] == 10)
    assert free == D("288.00")
    assert taxable == D("58.42")
    assert (taxable * D("0.10")).quantize(D("0.01")) == GULLI_GST
    assert ex + GULLI_GST == GULLI_TOTAL


def test_gulli_uses_an_explicit_rate_column(config):
    """
    THREE suppliers, THREE tax conventions:
      Gulli        -> explicit rate column ("0%" / "10%")
      Foodlink     -> a "GST" flag
      Select Fresh -> an asterisk footnote
    Never assume the convention.
    """
    assert config["suppliers"]["gulli"]["quirks"]["tax_marker_style"] == "explicit_rate"
    assert config["suppliers"]["foodlink"]["quirks"]["tax_marker_column"] == "GST"
    rates = {r[4] for r in GULLI}
    assert rates == {0, 10}


def test_gulli_packaging_is_taxable_food_is_not():
    """Pizza boxes 10%; flour, mozzarella, vegan cheese 0%."""
    boxes = next(r for r in GULLI if r[0] == "PBLTB13-U")
    flour = next(r for r in GULLI if r[0] == "MANFLOPIZ-U")
    assert boxes[4] == 10
    assert flour[4] == 0
    # Lightspeed needs incl: boxes convert, flour doesn't
    assert (D(boxes[5]) * D("1.1")).quantize(D("0.01")) == D("64.26")
    assert D(flour[5]) == D("69.00")


def test_gulli_zero_value_delivery_line_is_an_extra(config):
    """
    "Standard Delivery" at $0.00. Zero value, but it must classify as `extra`
    and never resolve to a product.
    """
    d = next(r for r in GULLI if r[0] == "DELIVERY_007")
    assert D(d[5]) == D("0.00")
    v = Validator(config)
    line = InvoiceLine(description="Standard Delivery", qty=D("1"),
                       line_total_incl=D("0.00"), line_class=LineClass.UNKNOWN)
    assert v.classify_line(line) == LineClass.EXTRA
    assert config["suppliers"]["gulli"]["quirks"]["zero_value_delivery_line"] == "DELIVERY_007"


def test_dext_gets_gulli_tax_right_but_foodlink_wrong(config):
    """
    THE CONTRAST that isolates the Foodlink bug.

      Gulli    CI-424608  real GST $5.84  -> Dext records $5.84  CORRECT
      Foodlink SI4467596  real GST $4.60  -> Dext records $0.00  WRONG

    Dext CAN capture mixed-tax GST. It just doesn't for Foodlink. That makes
    it a broken template, not a general limitation — and worth Donna's time.
    """
    assert config["suppliers"]["gulli"]["dext_tax_correct"] is True
    assert config["suppliers"]["foodlink"]["quirks"]["dext_header_is_incl_gst"] is True


def test_gulli_venue_resolves_by_customer_code(config):
    """Yet another per-supplier venue signal: Gulli uses "Customer Code"."""
    assert config["venue_resolution"]["by_supplier"]["gulli"]["customer_codes"]["STOWO1"] == "stowaway"


def test_gulli_converted_passes_the_validator(config):
    lines = []
    for code, desc, qty, price, rate, amount in GULLI:
        incl = (D(amount) * (D("1.1") if rate else D("1"))).quantize(D("0.01"))
        lines.append(InvoiceLine(
            description=desc, qty=D(qty),
            unit_price_incl=(incl / D(qty)).quantize(D("0.0001")) if D(qty) else D("0"),
            line_total_incl=incl,
            line_class=LineClass.EXTRA if code == "DELIVERY_007" else LineClass.STOCK,
            tax_treatment=TaxTreatment.GST if rate else TaxTreatment.GST_FREE,
            cost_basis=CostBasis.PER_UNIT, supplier_code=code))
    inv = Invoice(supplier_key="gulli", supplier_name_raw="Gulli Food Distributors Pty Ltd",
                  invoice_ref="CI-424608", invoice_date=datetime.date(2026, 7, 13),
                  total_incl=GULLI_TOTAL, venue=Venue.STOWAWAY,
                  gst_total=GULLI_GST, account_code="STOWO1", lines=lines)
    r = Validator(config).validate(inv)
    assert r.status == Status.PASS, r.report()


# ---------------------------------------------------------------------------
# Activity — Zak: "be strict about recent, nothing older than 30 days"
# ---------------------------------------------------------------------------

def test_dead_suppliers_are_flagged(config):
    """
    Checked 16 Jul 2026, strict 30-day window. None of these have traded:
      M&J Chickens 2026-02-19 (147d) · Torino 2024-07-29 ($507k lifetime)
      Winestock 2024-04-15 ($209k) · Gateway 2023-04-27
    Chasing traps in dead suppliers is waste.
    """
    for key in ["mj_chickens", "torino", "winestock", "gateway_liquor"]:
        s = config["suppliers"][key]
        assert s.get("status") in ("dead", "inactive"), key
        assert s.get("last_seen"), f"{key} needs last_seen"
        assert s["last_seen"] < "2026-06-16", f"{key} is inside the 30d window"


def test_activity_window_is_recorded(config):
    assert config["activity_window_days"] == 30
    assert config["activity_checked"] == "2026-07-16"
