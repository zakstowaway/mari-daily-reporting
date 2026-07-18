"""
Tests for the domain core — identity and time.

The important one is test_recomputing_a_past_day_is_stable. That single property
is why this design exists: it is what Average Cost Price cannot offer and what
makes app.stowawaybar.com trustworthy.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root

from core.domain import (  # noqa: E402
    CostObservation, CostSeries, ingredient_id, load_cost_observations, purchasable_id,
)


# ---------------------------------------------------------------- identity ---

def test_purchasable_key_is_the_invoices_key():
    assert purchasable_id("Foodlink", "102689") == "foodlink:102689"
    assert purchasable_id("ILG", "395-6785p") == "ilg:395-6785P"      # code upper, supplier slug


def test_purchasable_without_a_code_refuses():
    """
    No supplier code = no natural key = no identity. The tempting fallback is
    the description — which is precisely how ALEHOUSE CRISP KEG resolves to the
    wrong keg, $27.50 away. Refuse instead.
    """
    with pytest.raises(ValueError, match="no natural key"):
        purchasable_id("Sun Circle", "")


def test_two_suppliers_can_feed_one_ingredient():
    """
    THE POINT OF DECISION 1, and the answer to "we have new suppliers since the
    food menu was updated".

    Two purchasables, one ingredient. A recipe references the ingredient, so
    switching supplier does not touch the recipe — and the cost history stays
    continuous across the switch.
    """
    old = purchasable_id("Select Fresh", "ONIBK")
    new = purchasable_id("B&E", "ONION-BROWN-10KG")
    assert old != new
    m = {old: ingredient_id("Onion Brown"), new: ingredient_id("Onion Brown")}
    assert m[old] == m[new] == "onion-brown"

    series = CostSeries([
        CostObservation("onion-brown", date(2026, 5, 1), Decimal("2.40"), "kg", "stowaway", "A", old),
        CostObservation("onion-brown", date(2026, 7, 1), Decimal("2.90"), "kg", "stowaway", "B", new),
    ])
    # continuous across a supplier change — the recipe never noticed
    assert series.as_of("onion-brown", date(2026, 6, 1)).cost_per_unit == Decimal("2.40")
    assert series.as_of("onion-brown", date(2026, 8, 1)).cost_per_unit == Decimal("2.90")


# -------------------------------------------------------------------- time ---

@pytest.fixture
def series():
    return CostSeries([
        CostObservation("squid-tube", date(2026, 5, 10), Decimal("0.0100"), "g", "stowaway", "INV-1"),
        CostObservation("squid-tube", date(2026, 7, 16), Decimal("0.0114"), "g", "stowaway", "INV-2"),
        CostObservation("squid-tube", date(2026, 9, 1),  Decimal("0.0150"), "g", "stowaway", "INV-3"),
    ])


def test_as_of_returns_the_price_on_that_day(series):
    assert series.as_of("squid-tube", date(2026, 7, 20)).cost_per_unit == Decimal("0.0114")


def test_as_of_is_the_most_recent_on_or_before(series):
    assert series.as_of("squid-tube", date(2026, 7, 16)).cost_per_unit == Decimal("0.0114")  # boundary
    assert series.as_of("squid-tube", date(2026, 7, 15)).cost_per_unit == Decimal("0.0100")


def test_a_later_price_does_not_rewrite_an_earlier_day(series):
    """
    THE BUG THIS PREVENTS.

    September's $0.0150 must not touch July. If it did, recomputing July in
    September would change a number that has already been reported — which is
    exactly what Average Cost Price does, and why we are leaving it.
    """
    assert series.as_of("squid-tube", date(2026, 7, 20)).cost_per_unit == Decimal("0.0114")


def test_recomputing_a_past_day_is_stable():
    """
    THE INVARIANT. The reason the whole design is shaped this way.

    Same day, costed against a series that has since grown three more
    observations, must give the identical answer. Forever.
    """
    july = date(2026, 7, 16)
    base = [
        CostObservation("squid-tube", date(2026, 5, 10), Decimal("0.0100"), "g", "stowaway", "INV-1"),
        CostObservation("squid-tube", date(2026, 7, 16), Decimal("0.0114"), "g", "stowaway", "INV-2"),
    ]
    then = CostSeries(base).as_of("squid-tube", july).cost_per_unit

    later = CostSeries(base + [
        CostObservation("squid-tube", date(2026, 9, 1),  Decimal("0.0150"), "g", "stowaway", "INV-3"),
        CostObservation("squid-tube", date(2026, 11, 2), Decimal("0.0210"), "g", "stowaway", "INV-4"),
        CostObservation("squid-tube", date(2027, 1, 9),  Decimal("0.0090"), "g", "stowaway", "INV-5"),
    ]).as_of("squid-tube", july).cost_per_unit

    assert then == later == Decimal("0.0114")


def test_no_observation_before_the_day_refuses(series):
    """
    Never substitute a current price for a missing historical one. Inventing a
    cost is how history starts lying. Fail toward review.
    """
    with pytest.raises(LookupError, match="rewrites history"):
        series.as_of("squid-tube", date(2026, 1, 1))


def test_unknown_ingredient_refuses(series):
    with pytest.raises(LookupError):
        series.as_of("unicorn-tears", date(2026, 7, 20))


def test_money_must_be_decimal():
    """float money in a COGS subtraction is a silent wrongness generator."""
    with pytest.raises(TypeError, match="must be Decimal"):
        CostObservation("x", date(2026, 7, 1), 1.23, "g")            # type: ignore[arg-type]


def test_venue_preferred_then_fallback():
    s = CostSeries([
        CostObservation("aperol", date(2026, 7, 1), Decimal("29.08"), "bottle", "stowaway", "A"),
        CostObservation("aperol", date(2026, 7, 2), Decimal("31.00"), "bottle", "harry_gatos", "B"),
    ])
    assert s.as_of("aperol", date(2026, 7, 3), venue="stowaway").cost_per_unit == Decimal("29.08")
    assert s.as_of("aperol", date(2026, 7, 3), venue="harry_gatos").cost_per_unit == Decimal("31.00")
    # no observation for this venue -> fall back to any rather than fail
    assert s.as_of("aperol", date(2026, 7, 3), venue="marilynas").cost_per_unit is not None


# ------------------------------------------------------- the real fact log ---

def test_the_real_cogs_list_loads_as_an_observation_log():
    """
    data/cogs_list.csv already IS the fact table -- it has been read as a
    snapshot. Prove it loads as dated observations.
    """
    obs = load_cost_observations()
    assert len(obs) > 20
    assert all(isinstance(o.cost_per_unit, Decimal) for o in obs)
    assert all(o.observed_on.year >= 2024 for o in obs)
    s = CostSeries(obs)
    a = s.as_of("ilg:395-6785P", date(2026, 7, 14))          # Aperol, invoice 03729959
    assert a.cost_per_unit == Decimal("29.0817")
    assert a.source_invoice == "03729959"


# ---- ingredient identity map (Decision 1) ---------------------------------

def test_ingredient_map_empty_by_default_and_maps_to_self():
    """
    No cross-supplier duplicates in the data yet, so the map is empty and every
    purchasable is its own ingredient. Squid must still resolve.
    """
    from core.domain import load_cost_observations, load_ingredient_map
    assert load_ingredient_map() == {}
    obs = load_cost_observations()
    assert any(o.ingredient == "foodlink:102689" for o in obs)


def test_ingredient_map_merges_two_suppliers_when_confirmed(tmp_path):
    """
    THE POINT OF DECISION 1. Declare Select Fresh onion and B&E onion the same
    ingredient; both suppliers' prices then land on one continuous series.
    """
    from datetime import date
    from decimal import Decimal
    from core.domain import CostSeries, load_cost_observations

    costs = tmp_path / "costs.csv"
    costs.write_text(
        "ingredient,observed_on,cost_per_unit,unit,venue,source_invoice,pack,description\n"
        "select-fresh:ONIBK,2026-05-01,0.0024,g,stowaway,A,per kg,ONION BROWN KG\n"
        "b-e:ONION-10KG,2026-07-01,0.0028,g,stowaway,B,10kg,ONION BROWN 10KG\n"
    )
    merged = load_cost_observations(path=costs,
                                    purchasable_to_ingredient={
                                        "select-fresh:ONIBK": "onion-brown",
                                        "b-e:ONION-10KG": "onion-brown",
                                    })
    s = CostSeries(merged)
    # one ingredient, continuous across the supplier change
    assert s.as_of("onion-brown", date(2026, 6, 1)).cost_per_unit == Decimal("0.0024")
    assert s.as_of("onion-brown", date(2026, 8, 1)).cost_per_unit == Decimal("0.0028")
