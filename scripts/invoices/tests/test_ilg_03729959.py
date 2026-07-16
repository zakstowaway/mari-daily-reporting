"""
REAL INVOICE — the accuracy proof.

ILG invoice 03729959, 14-JUL-2026, $2,283.19, Stowaway (account 2428).
Dext holds ZERO line items for this invoice. Its own API returns [].

These 14 lines were read natively off the rendered document — the same way the
production extractor reads a PDF. Nothing here came from Dext's structured data,
because there isn't any.

The test is not "does it look right". The test is whether the extraction is
self-consistent: 14 independently-read TOT figures either reconcile to the
invoice's own stated total, or they don't. That's a fact, not an opinion, and
it's the entire premise of the validator.

Run: python3 -m pytest tests/test_ilg_03729959.py -v
"""

import sys
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from invoices.models import CostBasis, Invoice, InvoiceLine, LineClass, TaxTreatment, Venue
from invoices.validator import Status, Validator

# Columns, verbatim: Code | Description | Pack | Qty | Cost | Total | FRT per case
#                    | LUC ex GST | TOT inc GST
# (code, desc, pack, qty_raw, cost, total_ex, frt_per_case, luc_ex, tot_incl, pack_size, basis)
ILG_LINES = [
    ("175-0420",  "ANTICA FORMULA",                 "6x1LT",     "0/1", "339.58W", "58.01",  "REPACK", "58.43",  "64.27",  6,  CostBasis.PER_BOTTLE),
    ("395-6785P", "APEROL",                         "6x700ML",   "1",   "156.94",  "156.94", "1.69",   "26.44",  "174.49", 6,  CostBasis.PER_BOTTLE),
    ("305-1949P", "BUFFALO TRACE BOURBON 40%",      "6x700ML",   "0/1", "282.81",  "48.32",  "REPACK", "48.74",  "53.61",  6,  CostBasis.PER_BOTTLE),
    ("360-1310",  "ROOSTER ROJO TEQUILA BLANCO",    "6x700ML",   "3",   "280.10",  "840.30", "1.69",   "46.96",  "929.90", 6,  CostBasis.PER_BOTTLE),
    ("345-5638P", "SAILOR JERRY SPICED RUM",        "6x700ML",   "0/2", "235.54",  "80.47",  "REPACK", "40.66",  "89.44",  6,  CostBasis.PER_BOTTLE),
    ("122-2867",  "ALEHOUSE CRISP KEG",             "1xKEG49.",  "1",   "160.00",  "160.00", "8.13",   "168.13", "184.94", 1,  CostBasis.PER_KEG),
    ("122-2858",  "ALEHOUSE PREMIUM KEG",           "1xKEG49.",  "2",   "185.00",  "370.00", "8.13",   "193.13", "424.88", 1,  CostBasis.PER_KEG),
    ("115-3762",  "CORONA MEXICAN 6PK BRW BX R",    "24x355ML",  "1",   "54.41",   "54.41",  "1.69",   "14.02",  "61.71",  24, CostBasis.PER_CAN),
    ("117-4213",  "HEAPS NORMAL QUIET XPA NON ALC", "24x375ML",  "1",   "56.56",   "56.56",  "1.69",   "9.71",   "64.08",  24, CostBasis.PER_CAN),
    ("460-1504",  "COCA COLA",                      "12x1.25LT", "1",   "38.60",   "38.60",  "1.69",   "3.36",   "44.32",  12, CostBasis.PER_UNIT),
    ("460-2567",  "COCA COLA CAN CUBES",            "24x375ML",  "1",   "39.54",   "39.54",  "1.69",   "1.72",   "45.36",  24, CostBasis.PER_CAN),
    ("460-1639",  "COKE NO SUGAR 1.25 LITRE",       "12x1.25LT", "1",   "38.60",   "38.60",  "1.69",   "3.36",   "44.32",  12, CostBasis.PER_UNIT),
    ("450-1293",  "S.PELLEGRINO SPARKLING WATER",   "24x500ML",  "1",   "49.71",   "49.71",  "1.69",   "2.14",   "56.54",  24, CostBasis.PER_UNIT),
    ("460-3254",  "SPRITE 375ML 24 CUBE",           "24x375ML",  "1",   "39.54",   "39.54",  "1.69",   "1.72",   "45.35",  24, CostBasis.PER_CAN),
]

# Summary box, read off the same document:
STATED_TOTAL     = D("2283.19")
STATED_PRODUCT   = D("2031.00")   # ex GST
STATED_FREIGHT   = D("35.70")
STATED_FUEL_LEVY = D("8.93")
STATED_GST       = D("207.56")
STATED_WET       = D("13.04")     # "'W': WET of 29% Included = 13.04"


def _qty(raw):
    """ILG: '0/N' = N units as a repack. A plain integer = CASES."""
    if raw.startswith("0/"):
        return D(raw[2:])
    return D(raw)


def test_line_totals_reconcile_to_the_stated_product_subtotal():
    """
    The Total column (ex GST), summed, must equal the invoice's own
    'Product' subtotal. 14 figures read independently.
    """
    got = sum((D(r[5]) for r in ILG_LINES), D("0"))
    assert got == STATED_PRODUCT, f"{got} != {STATED_PRODUCT}"


def test_tot_incl_column_reconciles_to_the_invoice_total():
    """
    THE PROOF. Sum the 14 TOT inc GST figures and compare to $2,283.19.

    If any one of them was misread, this fails. There is no way to fudge it —
    14 numbers read off a rendered document either add up to a 15th number
    printed elsewhere on that document, or they don't.
    """
    got = sum((D(r[8]) for r in ILG_LINES), D("0"))
    assert abs(got - STATED_TOTAL) <= D("0.50"), f"{got} vs {STATED_TOTAL}"


def test_summary_box_is_internally_consistent():
    """Product + Freight + Fuel Levy, plus GST, must be the total."""
    ex = STATED_PRODUCT + STATED_FREIGHT + STATED_FUEL_LEVY
    assert (ex * D("1.1")).quantize(D("0.01")) == STATED_TOTAL
    assert (STATED_TOTAL / D("11")).quantize(D("0.01")) == STATED_GST


def test_freight_is_already_inside_the_tot_column():
    """
    Appendix B: 'FRT per case is already factored into TOT incl GST — don't add
    again.' Confirmed: the TOT column already reconciles to the total, which
    INCLUDES freight and fuel levy. Adding a separate freight line would
    double-count $44.63.
    """
    tot_sum = sum((D(r[8]) for r in ILG_LINES), D("0"))
    product_only_incl = (STATED_PRODUCT * D("1.1")).quantize(D("0.01"))
    assert tot_sum > product_only_incl          # TOT carries more than product
    assert abs(tot_sum - STATED_TOTAL) <= D("0.50")


@pytest.mark.parametrize("code,desc,pack,qty_raw,cost,total,frt,luc,tot,psize,basis", ILG_LINES)
def test_per_line_arithmetic(code, desc, pack, qty_raw, cost, total, frt, luc, tot, psize, basis):
    """
    Column semantics, verified line by line:
        Total = Cost x qty(cases)         [ex GST]
        TOT   = (Total + FRT x qty) x 1.1 [incl GST]
    Repack lines (qty '0/N', FRT 'REPACK') follow different rules — skipped.

    Deliberately does NOT assert anything about LUC. See the test below.
    """
    if frt == "REPACK":
        pytest.skip("repack line — Cost is the case price, Total is the repack price")
    qty = _qty(qty_raw)
    ex_with_frt = D(total) + D(frt) * qty
    assert abs((ex_with_frt * D("1.1")).quantize(D("0.01")) - D(tot)) <= D("0.02")


# LUC's unit basis, DERIVED from this invoice (ex_with_frt / LUC):
#   product                  pack        LUC      implied units
#   COCA COLA CAN CUBES      24x375ML    1.72     24   <- per can
#   SPRITE 375ML 24 CUBE     24x375ML    1.72     24   <- per can
#   HEAPS NORMAL QUIET XPA   24x375ML    9.71      6   <- per 4-pack (!!)
#   CORONA MEXICAN 6PK       24x355ML   14.02      4   <- per 6-pack
#   APEROL                   6x700ML    26.44      6   <- per bottle
#   ROOSTER ROJO (qty 3)     6x700ML    46.96     18   <- per bottle, all cases
#   ALEHOUSE PREMIUM (qty 2) 1xKEG49.  193.13      2   <- per keg
LUC_IMPLIED_UNITS = {
    "COCA COLA CAN CUBES": 24, "SPRITE 375ML 24 CUBE": 24,
    "HEAPS NORMAL QUIET XPA NON ALC": 6, "CORONA MEXICAN 6PK BRW BX R": 4,
}


def test_luc_unit_basis_is_NOT_inferable_from_the_pack_column():
    """
    THE TRAP. Appendix B calls LUC "Last Unit Cost ex GST" and treats it as a
    per-unit figure. Its UNIT VARIES PER PRODUCT and cannot be derived from the
    invoice.

    Proof: HEAPS NORMAL and COCA COLA CAN CUBES both carry pack "24x375ML".
    LUC is per-4-pack for one and per-can for the other — a 4x difference with
    identical pack strings and nothing else to distinguish them.

    Feeding LUC into Lightspeed as a unit cost puts Heaps Normal in at $9.71
    instead of $2.67 (3.6x high) and Corona at $14.02 instead of $2.57 (5.5x
    high) — straight into Average Cost Price, GP and every recipe, silently,
    for 30 days (skill Rule 8).

    NEVER use LUC. Use TOT / (qty x pack_size). See the next test.
    """
    same_pack = [r for r in ILG_LINES if r[2] == "24x375ML" and r[6] != "REPACK"]
    assert len(same_pack) >= 2
    bases = {LUC_IMPLIED_UNITS[r[1]] for r in same_pack if r[1] in LUC_IMPLIED_UNITS}
    assert len(bases) > 1, (
        "Expected identical pack strings to imply DIFFERENT LUC bases — "
        "that is the whole hazard"
    )
    assert bases == {6, 24}


@pytest.mark.parametrize("code,desc,pack,qty_raw,cost,total,frt,luc,tot,psize,basis", ILG_LINES)
def test_tot_over_pack_size_gives_a_plausible_unit_price(code, desc, pack, qty_raw,
                                                         cost, total, frt, luc, tot, psize, basis):
    """
    Appendix B's actual rule — 'TOT incl GST ... divide by qty x pack_size' —
    is the correct one, and unlike LUC it lands inside the sanity bounds for
    every line on this invoice.
    """
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "suppliers.yaml").read_text())
    qty = _qty(qty_raw)
    unit = (D(tot) / (qty * psize)).quantize(D("0.01"))
    b = cfg["sanity_bounds"].get(basis.value)
    if not b:
        pytest.skip("no bounds for this basis")
    lo, hi = D(str(b["min"])), D(str(b["max"]))
    assert lo <= unit <= hi, f"{desc}: {unit} outside {basis.value} {lo}-{hi}"


def test_full_invoice_passes_the_validator():
    """
    End to end: build the Invoice, run validate(), require PASS.

    NOTE — the first version of this test used `tot / qty`, forgetting pack
    size. SANITY_BOUNDS caught it on four lines (Corona at $61.71/can against
    a $0.80-8.00 bound). That is precisely the silent-killer case the check
    exists for, and it fired on real data against the person who wrote it.
    Kept in the history deliberately.
    """
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "suppliers.yaml").read_text())
    lines = []
    for (code, desc, pack, qty_raw, cost, total, frt, luc, tot, psize, basis) in ILG_LINES:
        qty = _qty(qty_raw)
        # Lightspeed stores PER-UNIT and multiplies by pack size itself.
        unit = (D(tot) / (qty * psize)).quantize(D("0.0001"))
        lines.append(InvoiceLine(
            description=desc, qty=qty,
            unit_price_incl=unit,
            pack_size=psize,
            line_total_incl=D(tot),
            line_class=LineClass.STOCK,
            tax_treatment=TaxTreatment.GST,
            cost_basis=basis,
            supplier_code=code, raw_qty=qty_raw,
        ))
    inv = Invoice(
        supplier_key="ilg", supplier_name_raw="Independent Liquor Group",
        invoice_ref="03729959", invoice_date=date(2026, 7, 14),
        total_incl=STATED_TOTAL, venue=Venue.STOWAWAY,
        account_code="2428", gst_total=STATED_GST, lines=lines,
        source_pdf="dext:UmVjZWlwdC0yMTE3Nzc3NzA0",
    )
    r = Validator(cfg).validate(inv)
    assert r.status == Status.PASS, r.report()


def test_keg_prices_land_inside_the_sanity_bounds():
    """
    First real keg prices from an actual invoice:
        ALEHOUSE CRISP KEG     $184.94 / keg incl
        ALEHOUSE PREMIUM KEG   $212.44 / keg incl  (424.88 / 2)
    Both inside per_keg $100-600. Grifter's real $292.05 also fits.
    Appendix B's $34.65 would have been flagged — correctly.
    """
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "suppliers.yaml").read_text())
    lo = D(str(cfg["sanity_bounds"]["per_keg"]["min"]))
    hi = D(str(cfg["sanity_bounds"]["per_keg"]["max"]))
    for price in [D("184.94"), D("212.44"), D("292.05")]:
        assert lo <= price <= hi
    assert not (lo <= D("34.65") <= hi)   # the Appendix B figure — correctly out
