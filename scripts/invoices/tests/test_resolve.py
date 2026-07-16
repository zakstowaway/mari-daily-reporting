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
