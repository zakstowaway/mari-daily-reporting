"""
Resolver tests. The adversarial ones are the point.

The Alehouse pair is the whole reason this module exists: two real kegs,
$27.50 apart, whose names match the same pattern and whose sensible reading
is BACKWARDS. If the resolver ever silently picks one, this suite fails.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from invoices.resolve import MAP_CSV, Resolution, Resolver, Unresolved  # noqa: E402

pytestmark = pytest.mark.skipif(not MAP_CSV.exists(), reason="product_map.csv not built")


@pytest.fixture(scope="module")
def R():
    return Resolver()


def test_map_is_not_empty(R):
    assert len(R) > 0


def test_resolves_a_known_ilg_code(R):
    r = R.resolve("ILG", "395-6785P")            # APEROL, invoice 03729959
    assert r.product_id.isdigit()
    assert "aperol" in r.product_name.lower()


def test_unknown_code_raises_rather_than_guessing(R):
    with pytest.raises(Unresolved):
        R.resolve("ILG", "999-0000-NOPE")


def test_unknown_supplier_raises(R):
    with pytest.raises(Unresolved):
        R.resolve("Nonexistent Liquor Co", "395-6785P")


def test_blank_code_raises(R):
    with pytest.raises(Unresolved):
        R.resolve("ILG", "")


def test_cost_guard_refuses_a_wrong_product(R):
    """
    THE ONE THAT MATTERS.

    Resolve a real code but hand it a cost from a DIFFERENT product. That is
    what a mis-mapping looks like from the outside, and it must be refused,
    not warned about.
    """
    r = R.resolve("ILG", "395-6785P")            # Aperol, ~$29
    with pytest.raises(Unresolved, match="cost guard FAILED"):
        R.resolve("ILG", "395-6785P", invoice_cost=Decimal("212.44"))  # a keg's cost


def test_cost_guard_tolerates_reference_price_drift(R):
    """
    Back Office cost is a manually-set reference and drifts. Measured drift on
    the real export is 0.04-3.13. That must NOT be refused, or every invoice
    lands in review and the system is useless.
    """
    r0 = R.resolve("ILG", "395-6785P")
    assert r0.bo_cost is not None
    ok = R.resolve("ILG", "395-6785P", invoice_cost=r0.bo_cost + Decimal("0.50"))
    assert ok.product_id == r0.product_id


def test_guard_bands_overlap_in_percentage_space():
    """
    THE BUG THAT SHIPPED, 2026-07-16 -> caught 2026-07-17.

    Real measured pairs. A percentage-only rule CANNOT separate them, and the
    original max($5, 10%) rule -- max being OR -- passed everything cheap
    because the $5 floor exceeded the 10% band. Every mapping in the first cut
    was liquor, so nothing caught it.
    """
    from invoices.resolve import is_suspect

    # real drift, must PASS despite being huge in percentage terms
    assert not is_suspect(Decimal("3.09"), Decimal("3.6933")), "Sprite/Coke 16-22% drift is real"
    assert not is_suspect(Decimal("1.47"), Decimal("1.8896")), "22.2% on 42c is drift, not error"
    assert not is_suspect(Decimal("444.67"), Decimal("441.54")), "$3.13 on a $441 keg is drift"

    # real error, must FAIL despite being SMALLER in percentage terms than Sprite
    assert is_suspect(Decimal("212.44"), Decimal("184.94")), "Alehouse swap: $27.50 = 14.9%"

    # the old max($5,10%) rule would have waved this through: $3.60 < $5 floor
    assert is_suspect(Decimal("60.00"), Decimal("30.00")), "100% and $30 is not drift"


def test_guard_needs_BOTH_percent_and_dollars():
    from invoices.resolve import is_suspect
    assert not is_suspect(Decimal("100.00"), Decimal("96.00"))   # 4.2%, $4 -> neither
    assert not is_suspect(Decimal("2.00"), Decimal("1.50"))      # 33% but only 50c
    assert not is_suspect(Decimal("400.00"), Decimal("394.00"))  # $6 but only 1.5%
    assert is_suspect(Decimal("400.00"), Decimal("300.00"))      # 33% AND $100


def test_cost_is_blind_to_similarly_priced_products():
    """
    HONEST LIMIT, not a wish. Tomato powder $16.50 vs chilli powder $16.00 --
    a cost-led matcher resolved one to the other and the guard passed it,
    correctly by its own logic. This asserts the blindness so nobody later
    mistakes the guard for a matcher.
    """
    from invoices.resolve import is_suspect
    assert not is_suspect(Decimal("16.00"), Decimal("16.50")), (
        "cost CANNOT distinguish tomato powder from chilli powder -- "
        "which is exactly why cost must never SELECT a product, only check one"
    )


def test_every_mapping_passes_its_own_guard():
    """No row in the shipped table may contradict the cost guard."""
    import csv
    R = Resolver()
    for row in csv.DictReader(MAP_CSV.open(encoding="utf-8-sig")):
        if row["venue"] != "stowaway" or not row.get("invoice_cost"):
            continue
        # must not raise
        R.resolve(row["supplier"], row["supplier_code"],
                  invoice_cost=Decimal(row["invoice_cost"]))


def test_no_row_is_marked_suspect():
    import csv
    bad = [r for r in csv.DictReader(MAP_CSV.open(encoding="utf-8-sig"))
           if r.get("confidence") == "SUSPECT"]
    assert not bad, f"SUSPECT rows must not ship: {[(r['supplier_code'], r['product_name']) for r in bad]}"


def test_venue_isolation():
    """
    HG ProductIDs differ from Stowaway's. A Stowaway resolver must never
    hand back an ID for the wrong venue -- that silently writes cost against
    a product in the other business.
    """
    hg = Resolver(venue="harry_gatos")
    stow = Resolver(venue="stowaway")
    overlap = set(hg._by_code) & set(stow._by_code)
    for key in overlap:
        assert hg._by_code[key]["product_id"] != stow._by_code[key]["product_id"], (
            f"{key} has the same ProductID in both venues -- impossible, "
            f"the product databases are independent"
        )
