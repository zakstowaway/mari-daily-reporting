"""
Tests for the platform additions (2026-07-19):
  * rolling 30-day average as the live cost, as_of untouched for history
  * sub-recipes (a recipe used as an ingredient) with a cycle guard
  * labour cost from prep minutes, and GP shown two ways
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.domain import CostObservation, CostSeries  # noqa: E402
from modules.recipes.cost import (  # noqa: E402
    CircularRecipe, MissingCost, Recipe, RecipeLine, cost_breakdown, cost_on,
    gp_percent,
)

OIL = "oil"


# ---- rolling average -------------------------------------------------------

def test_rolling_is_the_mean_of_the_window():
    """Three July prices for oil -> the live cost is their average, not the last."""
    s = CostSeries([
        CostObservation(OIL, date(2026, 7, 1),  Decimal("0.0100"), "ml", "stowaway"),
        CostObservation(OIL, date(2026, 7, 10), Decimal("0.0200"), "ml", "stowaway"),
        CostObservation(OIL, date(2026, 7, 20), Decimal("0.0300"), "ml", "stowaway"),
    ])
    obs = s.rolling(OIL, date(2026, 7, 25), venue="stowaway")
    assert obs.cost_per_unit == Decimal("0.02")          # (0.01+0.02+0.03)/3


def test_rolling_only_counts_the_last_30_days():
    """A price from before the window is not in the average."""
    s = CostSeries([
        CostObservation(OIL, date(2026, 5, 1),  Decimal("0.0100"), "ml", "stowaway"),  # old
        CostObservation(OIL, date(2026, 7, 10), Decimal("0.0200"), "ml", "stowaway"),
        CostObservation(OIL, date(2026, 7, 20), Decimal("0.0300"), "ml", "stowaway"),
    ])
    obs = s.rolling(OIL, date(2026, 7, 25), venue="stowaway")
    assert obs.cost_per_unit == Decimal("0.025")         # (0.02+0.03)/2, May excluded


def test_rolling_with_one_price_returns_that_price():
    """Today's real data: one observation per item. Average degrades to it."""
    s = CostSeries([CostObservation(OIL, date(2026, 7, 16), Decimal("0.0114"), "ml", "stowaway")])
    assert s.rolling(OIL, date(2026, 7, 20), venue="stowaway").cost_per_unit == Decimal("0.0114")


def test_rolling_is_volume_weighted_when_quantities_known():
    """20L at $0.01 and 1L at $0.08 -> weighted mean sits near the bulk price."""
    s = CostSeries([
        CostObservation(OIL, date(2026, 7, 10), Decimal("0.01"), "ml", "stowaway", qty=Decimal("20000")),
        CostObservation(OIL, date(2026, 7, 18), Decimal("0.08"), "ml", "stowaway", qty=Decimal("1000")),
    ])
    obs = s.rolling(OIL, date(2026, 7, 20), venue="stowaway")
    # (0.01*20000 + 0.08*1000) / 21000 = 280/21000
    assert obs.cost_per_unit == (Decimal("280") / Decimal("21000"))
    assert obs.cost_per_unit < Decimal("0.02")           # bulk buy dominates


def test_as_of_is_unchanged_by_rolling():
    """The history invariant still holds: as_of gives the price on that day."""
    s = CostSeries([
        CostObservation(OIL, date(2026, 7, 1),  Decimal("0.0100"), "ml", "stowaway"),
        CostObservation(OIL, date(2026, 7, 20), Decimal("0.0300"), "ml", "stowaway"),
    ])
    assert s.as_of(OIL, date(2026, 7, 5), venue="stowaway").cost_per_unit == Decimal("0.0100")


def test_recipe_rolling_vs_asof_differ_as_expected():
    s = CostSeries([
        CostObservation(OIL, date(2026, 7, 1),  Decimal("0.0100"), "ml", "stowaway"),
        CostObservation(OIL, date(2026, 7, 20), Decimal("0.0300"), "ml", "stowaway"),
    ])
    r = Recipe("Fryer Test", "stowaway", lines=(RecipeLine(OIL, Decimal("100"), "ml"),))
    # as_of on the 25th -> latest single price 0.03 -> $3.00
    assert cost_on(r, s, date(2026, 7, 25)) == Decimal("3.00")
    # rolling -> mean(0.01, 0.03)=0.02 -> $2.00
    assert cost_on(r, s, date(2026, 7, 25), price_mode="rolling") == Decimal("2.00")


# ---- sub-recipes -----------------------------------------------------------

@pytest.fixture
def costs():
    return CostSeries([
        CostObservation("chilli", date(2026, 7, 1), Decimal("0.02"), "g", "stowaway"),
        CostObservation("oil",    date(2026, 7, 1), Decimal("0.01"), "ml", "stowaway"),
        CostObservation("squid",  date(2026, 7, 1), Decimal("0.0114"), "g", "stowaway"),
    ])


def test_subrecipe_costs_by_yield(costs):
    """
    Chilli sauce: 1000g chilli + 1000ml oil = $30, yields 2000g -> $0.015/g.
    A dish using 50g of it carries $0.75.
    """
    sauce = Recipe(
        "Chilli Sauce", "stowaway", yield_qty=Decimal("2000"), yield_unit="g",
        lines=(RecipeLine("chilli", Decimal("1000"), "g"),
               RecipeLine("oil", Decimal("1000"), "ml")),
    )
    dish = Recipe(
        "Salt & Pepper Squid", "stowaway", sell_incl_gst=Decimal("24.00"),
        lines=(RecipeLine("squid", Decimal("200"), "g"),
               RecipeLine("", Decimal("50"), "g", subrecipe="Chilli Sauce")),
    )
    # squid 200*0.0114=2.28 ; sauce 50 * (30/2000=0.015) = 0.75 ; total 3.03
    assert cost_on(dish, costs, date(2026, 7, 20), recipes=[sauce, dish]) == Decimal("3.03")


def test_subrecipe_without_yield_refuses(costs):
    sauce = Recipe("Aioli", "stowaway",
                   lines=(RecipeLine("oil", Decimal("500"), "ml"),))   # no yield
    dish = Recipe("Chips", "stowaway",
                  lines=(RecipeLine("", Decimal("30"), "g", subrecipe="Aioli"),))
    with pytest.raises(MissingCost, match="no yield"):
        cost_on(dish, costs, date(2026, 7, 20), recipes=[sauce, dish])


def test_subrecipe_missing_refuses(costs):
    dish = Recipe("Chips", "stowaway",
                  lines=(RecipeLine("", Decimal("30"), "g", subrecipe="Nonexistent"),))
    with pytest.raises(MissingCost, match="no version in force"):
        cost_on(dish, costs, date(2026, 7, 20), recipes=[dish])


def test_circular_subrecipe_refuses(costs):
    a = Recipe("A", "stowaway", yield_qty=Decimal("100"), yield_unit="g",
               lines=(RecipeLine("", Decimal("10"), "g", subrecipe="B"),))
    b = Recipe("B", "stowaway", yield_qty=Decimal("100"), yield_unit="g",
               lines=(RecipeLine("", Decimal("10"), "g", subrecipe="A"),))
    with pytest.raises(CircularRecipe):
        cost_on(a, costs, date(2026, 7, 20), recipes=[a, b])


def test_subrecipe_unit_mismatch_refuses(costs):
    sauce = Recipe("Stock", "stowaway", yield_qty=Decimal("2000"), yield_unit="ml",
                   lines=(RecipeLine("oil", Decimal("100"), "ml"),))
    dish = Recipe("Soup", "stowaway",
                  lines=(RecipeLine("", Decimal("50"), "g", subrecipe="Stock"),))  # g vs ml
    with pytest.raises(MissingCost, match="unit mismatch"):
        cost_on(dish, costs, date(2026, 7, 20), recipes=[sauce, dish])


# ---- labour: per-user rate from real wages ---------------------------------

def test_rate_is_the_persons_own_rate_loaded_with_oncosts():
    """
    A named cook resolves to their exact rate, uplifted by super + on-costs.
    Miller Manson: $25.4421/hr base -> effective/60 per minute.
    """
    from modules.recipes.labour import rate_per_minute_for, SUPER_RATE, _oncost_rate
    rpm = rate_per_minute_for("Miller Manson")
    assert rpm is not None
    expected = (Decimal("25.4421") * (Decimal("1") + SUPER_RATE + _oncost_rate()) / 60)
    assert rpm == expected.quantize(Decimal("0.0001"))


def test_rate_by_employee_id_matches_by_name():
    from modules.recipes.labour import rate_per_minute_for
    assert rate_per_minute_for("83") == rate_per_minute_for("Miller Manson")  # id 83


def test_unknown_person_has_no_rate_not_a_zero():
    from modules.recipes.labour import rate_per_minute_for
    assert rate_per_minute_for("Nobody McGhost") is None


def test_session_cost_uses_the_recorders_rate():
    """Marssheel's minutes at Marssheel's rate — different hands, different money."""
    from modules.recipes.labour import PrepSession, session_cost, rate_per_minute_for
    s = PrepSession("Chilli Sauce", "Miller Manson", Decimal("30"), date(2026, 7, 18), "stowaway")
    assert session_cost(s) == Decimal("30") * rate_per_minute_for("Miller Manson")


def test_product_labour_averages_recent_sessions_by_dollar():
    """Two cooks prep the same dish; the labour figure is the mean of real costs."""
    from modules.recipes.labour import PrepSession, product_labour, session_cost
    ss = [
        PrepSession("Squid", "Miller Manson", Decimal("10"), date(2026, 7, 10), "stowaway"),
        PrepSession("Squid", "Flynn O'Connor", Decimal("14"), date(2026, 7, 18), "stowaway"),
        PrepSession("Other", "Miller Manson", Decimal("99"), date(2026, 7, 18), "stowaway"),
    ]
    got = product_labour("Squid", ss, on=date(2026, 7, 20))
    want = (session_cost(ss[0]) + session_cost(ss[1])) / Decimal("2")
    assert got == want


def test_product_labour_excludes_rateless_sessions_not_zeroes_them():
    from modules.recipes.labour import PrepSession, product_labour, session_cost
    ss = [
        PrepSession("Squid", "Miller Manson", Decimal("10"), date(2026, 7, 18), "stowaway"),
        PrepSession("Squid", "Nobody McGhost", Decimal("10"), date(2026, 7, 18), "stowaway"),
    ]
    # only the known-rate session counts; a zero for the ghost would flatter it
    assert product_labour("Squid", ss, on=date(2026, 7, 20)) == session_cost(ss[0])


def test_product_labour_none_when_no_sessions():
    from modules.recipes.labour import product_labour
    assert product_labour("Squid", [], on=date(2026, 7, 20)) is None


# ---- dual GP ---------------------------------------------------------------

def test_breakdown_shows_gp_both_ways(costs):
    """Food GP looks great; after real prep labour the true GP is materially lower."""
    r = Recipe("Squid", "stowaway", sell_incl_gst=Decimal("24.00"),
               lines=(RecipeLine("squid", Decimal("200"), "g"),))
    b = cost_breakdown(r, costs, date(2026, 7, 20), labour=Decimal("5.00"),
                       price_mode="as_of")
    assert b["food_cost"] == Decimal("2.28")
    assert b["labour_cost"] == Decimal("5.00")
    assert b["total_cost"] == Decimal("7.28")
    assert b["gp_food"] > b["gp_true"]                    # labour eats margin
    assert b["flag"] is not None                          # food GP too good, flagged
    assert b["gp_true"] is not None
